"""
Capability Registry Module

This module provides a registry for managing and retrieving AI tool capabilities
using various search strategies (exact name, semantic similarity, and history).

Features:
- Initialize with a specific storage backend.
- Support multiple RAG functions as BM25 or embedding-based.
- Support adding capabilities.
    - The name and description of capabilities are required and unique.
    - Once a new capability is added, trigger an update if needed.(for example BM25)
- Retrieve capabilities by exact tool name.
- Semantic search for capabilities based on message context (RAG).
    - Able to config rules of Semantic search logic.
- History-based capability suggestion.
- Record capability usage for future recommendations.
- Fully instrumented with OpenTelemetry for distributed tracing, metrics, and logging.

Design Goals:
- Decouple capability storage from retrieval logic via StoreBase interface.
- Support pluggable storage backends (in-memory, vector DB, etc.).
- Provide OpenTelemetry observability out-of-the-box.
- Maintain logging at INFO and DEBUG levels for operational insight.

Missing/Optional Features (reserved for community contributions):
- Bulk import/export of capabilities.
- Capability versioning and deprecation management.
- Access control and rate limiting hooks.
- Automatic tool schema generation from docstrings.
"""

import sys
import os
import logging
from typing import List, Dict
from scl.meta.capability import Capability
from scl.otel.metric_decorator import record_latency
from scl.otel.otel import search_time_histogram, tool_execute_time_histogram
from scl.otel.otel import tracer, meter
from opentelemetry import trace

# Add the StructuredContextLanguage directory to the path
scl_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(scl_root)
from scl.storage.base import StoreBase
from scl.meta.msg import Msg


class CapRegistry:
    """
    Central registry for discovering and tracking AI tool capabilities.

    This class wraps a StoreBase implementation to provide a uniform interface
    for capability retrieval, similarity search, and usage recording. All public
    methods are instrumented with OpenTelemetry spans and metrics.

    Attributes:
        cap_store (StoreBase): The underlying storage backend.
        logger (logging.Logger): Logger instance for this class.
        cap_fetch_counter (opentelemetry.metrics.Counter): Counter for tracking
            capability fetches by type (name, similarity, history).
    """

    def __init__(self, StoreBase: StoreBase):
        """
        Initialize the CapRegistry with any StoreBase implementation.

        Args:
            StoreBase: An instance of a StoreBase implementation (e.g., in-memory,
                       vector store, SQL database).
        """
        self.cap_store = StoreBase

        # ---------- OpenTelemetry Observability Setup ----------
        self.logger = logging.getLogger(__name__)
        # Create a counter to track capability retrievals
        self.cap_fetch_counter = meter.create_counter(
            "capability.fetches",
            description="Number of capability retrievals by method type"
        )
        # ---------------------------------------------------------

    @tracer.start_as_current_span("getCapsByNames")
    def getCapsByNames(self, ToolNames: List[str]) -> Dict[str, Capability]:
        """
        Retrieve multiple capabilities by their exact names.

        This method iterates through the provided list of tool names,
        fetches each from the underlying store, and returns a dictionary
        mapping tool name to its Capability object. Missing capabilities
        are silently omitted.

        Trace Attributes Added:
            - tool_names: The list of names requested.
            - count_found: Number of capabilities successfully retrieved.

        Args:
            ToolNames (List[str]): List of capability/tool names to fetch.

        Returns:
            Dict[str, Capability]: Dictionary where keys are tool names and
                                   values are the corresponding Capability objects.
                                   Returns empty dict if store is unavailable.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("tool_names", str(ToolNames))

        functions = {}
        if self.cap_store is None:
            self.logger.info("Database not initialized. Cannot perform search by names.")
            current_span.set_attribute("error", "store_uninitialized")
            return {}

        self.logger.debug(f"Attempting to fetch capabilities by names: {ToolNames}")

        for tool_name in ToolNames:
            self.logger.info(f"Searching for function: {tool_name}")
            function = self.get_cap_by_name(tool_name)
            self.logger.debug(f"Retrieved function for '{tool_name}': {function}")
            if function:
                functions[tool_name] = function
                self.cap_fetch_counter.add(1, {"method": "by_name", "found": "true"})
            else:
                self.cap_fetch_counter.add(1, {"method": "by_name", "found": "false"})

        current_span.set_attribute("count_found", len(functions))
        return functions

    @tracer.start_as_current_span("getCapsByName")
    def get_cap_by_name(self, name: str) -> Capability:
        """
        Retrieve a single capability by its exact name.

        Trace Attributes Added:
            - capability_name: The name being queried.

        Metrics:
            - Increments 'capability.fetches' counter with method='by_name_single'.

        Args:
            name (str): The exact name of the capability.

        Returns:
            Capability: The capability object if found; otherwise None (or whatever
                        the underlying store returns).
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("capability_name", name)

        self.logger.debug(f"Fetching capability by exact name: '{name}'")
        result = self.cap_store.get_cap_by_name(name)
        self.cap_fetch_counter.add(1, {"method": "by_name_single"})
        return result

    @tracer.start_as_current_span("getCapsBySimilarity")
    @record_latency(search_time_histogram, "search")
    def getCapsBySimilarity(self, msg: Msg, limit: int = 5, min_similarity: float = 0.5) -> Dict[str, Capability]:
        """
        Perform a semantic (RAG-like) search for capabilities based on message context.

        This method uses the underlying store's embedding-based similarity search
        to find the most relevant capabilities for a given message (e.g., user query).
        The results are filtered by a minimum similarity threshold.

        Trace Attributes Added:
            - limit: The maximum number of results requested.
            - min_similarity: The similarity cutoff.
            - message_content (truncated): Snippet of the message for context.

        Metrics:
            - Increments 'capability.fetches' counter with method='similarity'.
            - Recorded latency via @record_latency decorator.

        Args:
            msg (Msg): The message object containing context for search.
            limit (int): Maximum number of capabilities to return. Defaults to 5.
            min_similarity (float): Minimum similarity score (0.0 to 1.0) to include.
                                    Defaults to 0.5.

        Returns:
            Dict[str, Capability]: Dictionary of capacity_name -> Capability objects
                                   that exceed the similarity threshold, ordered
                                   by descending similarity (implementation dependent).
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("limit", limit)
        current_span.set_attribute("min_similarity", min_similarity)
        # Avoid logging large messages; record first 100 chars
        if msg and hasattr(msg, 'content'):
            snippet = str(msg.content)[:100] + "..." if len(str(msg.content)) > 100 else str(msg.content)
            current_span.set_attribute("message_content_preview", snippet)

        self.logger.debug(f"Semantic search with limit={limit}, min_similarity={min_similarity}")
        results = self.cap_store.search_by_similarity(msg, limit, min_similarity)
        self.cap_fetch_counter.add(len(results), {"method": "similarity"})
        self.logger.info(f"Similarity search returned {len(results)} capabilities.")
        return results

    @tracer.start_as_current_span("record_cap_history_safe")
    def record(self, msg: Msg, cap: Capability):
        """
        Record a capability usage event for future history-based recommendations.

        This method stores an association between a message context and the
        capability that was selected/executed. The underlying store can then
        use this history to improve suggestions.

        Trace Attributes Added:
            - capability_name: Name of the capability being recorded.
            - message_id (if available): Identifier for the message.

        Metrics:
            - Increments a metric for usage recordings (if desired, can be added).

        Args:
            msg (Msg): The message context in which the capability was used.
            cap (Capability): The capability that was executed.

        Returns:
            The result of the store's record operation (typically None).
        """
        current_span = trace.get_current_span()
        if cap:
            current_span.set_attribute("capability_name", cap.name if hasattr(cap, 'name') else "unknown")
        if msg and hasattr(msg, 'id'):
            current_span.set_attribute("message_id", str(msg.id))

        self.logger.debug(f"Recording usage of capability '{cap.name if cap else 'None'}'")
        result = self.cap_store.record(msg, cap)
        self.logger.info(f"Recorded capability usage for {cap.name if cap else 'None'}")
        return result

    @tracer.start_as_current_span("getCapsByHistory")
    @record_latency(search_time_histogram, "search")
    def getCapsByHistory(self, msg: Msg, limit: int = 5, min_similarity: float = 0.5) -> Dict[str, Capability]:
        """
        Retrieve capabilities that are historically associated with similar messages.

        This method leverages past usage recordings to recommend capabilities
        that were frequently used in similar contexts (collaborative filtering style).

        Trace Attributes Added:
            - limit: Max number of results.
            - min_similarity: Similarity threshold used by the store if applicable.

        Metrics:
            - Increments 'capability.fetches' counter with method='history'.

        Args:
            msg (Msg): The message context for which history is relevant.
            limit (int): Maximum number of capabilities to return. Defaults to 5.
            min_similarity (float): Minimum similarity score for historical matching
                                    (store-specific interpretation). Defaults to 0.5.

        Returns:
            Dict[str, Capability]: Dictionary of historically relevant capabilities.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("limit", limit)
        current_span.set_attribute("min_similarity", min_similarity)

        self.logger.debug(f"History-based search with limit={limit}, min_similarity={min_similarity}")
        results = self.cap_store.getCapsByHistory(msg, limit, min_similarity)
        self.cap_fetch_counter.add(len(results), {"method": "history"})
        self.logger.info(f"History-based search returned {len(results)} capabilities.")
        return results