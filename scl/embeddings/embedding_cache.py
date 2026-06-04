"""
File path:
scl/embedding/embedding_cache.py

Features and design goals
- Local JSON cache file (`./embedding_cache.json`) to store and reuse embeddings.
- Cache key = raw input text, cache value = the embedding vector (list of floats).

Standalone persistent cache for embeddings.
Used by the composite embedding coordinator.
"""

import json
import logging
import os

from opentelemetry import trace

from scl.otel.otel import meter, tracer


class EmbeddingCache:
    def __init__(self, cache_file="embedding_cache.json"):
        self.logger = logging.getLogger(__name__)
        self.cache_file = cache_file
        self.cache = {}

        # OTEL metrics
        self.cache_hits = meter.create_counter(
            "embedding_cache_hits", description="Number of cache hits"
        )
        self.cache_misses = meter.create_counter(
            "embedding_cache_misses", description="Number of cache misses"
        )

        self._load()

    @tracer.start_as_current_span("EmbeddingCache._load")
    def _load(self):
        """Load cache from disk."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    self.cache = json.load(f)
                self.logger.info("Embedding cache loaded (%d entries)", len(self.cache))
            except Exception as e:
                self.logger.error("Failed to load cache: %s", e)
                self.cache = {}
        else:
            self.logger.info("No cache file found, starting empty")

    @tracer.start_as_current_span("EmbeddingCache._save")
    def _save(self):
        """Persist cache to disk."""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
            self.logger.debug("Cache saved (%d entries)", len(self.cache))
        except Exception as e:
            self.logger.error("Failed to save cache: %s", e)

    @tracer.start_as_current_span("EmbeddingCache.get")
    def get(self, text):
        """Retrieve cached embedding for the given text, or None if missing."""
        current_span = trace.get_current_span()
        current_span.set_attribute("cache.key", text)

        result = self.cache.get(text)
        if result is not None:
            self.logger.debug("Cache hit for key: %s", text)
            current_span.set_attribute("cache.hit", True)
            self.cache_hits.add(1)
        else:
            self.logger.debug("Cache miss for key: %s", text)
            current_span.set_attribute("cache.hit", False)
            self.cache_misses.add(1)
        return result

    @tracer.start_as_current_span("EmbeddingCache.set")
    def set(self, text, embedding):
        """Store embedding for the given text."""
        current_span = trace.get_current_span()
        current_span.set_attribute("cache.key", text)

        self.cache[text] = embedding
        self._save()
        self.logger.debug("Cached embedding for key: %s", text)

    def __contains__(self, text):
        return text in self.cache


"""
    Example usage:
    from scl.embedding.embedding_cache import EmbeddingCache
    
    cache = EmbeddingCache()
    emb = cache.get("hello world")
    if emb is None:
        # compute embedding externally
        computed = [0.1, 0.2, 0.3]
        cache.set("hello world", computed)
        emb = computed
    # use emb
"""
