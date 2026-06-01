"""
Capability Module

Design Goals & Features:
------------------------
1. Provide a unified abstract base class for both Skill and FunctionCall implementations.
2. Encapsulate common attributes: name, description, type, original_body, llm_description, function_impl.
3. Support progressive loading via embedding vector of the description for RAG.
4. Cached embedding computation to avoid repeated embedding calls.
5. It has a method to execute the cap with given arguments[dict] like execute(self, args_dict: Dict[str, Any]), but leave it to subclass for implementations.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
- Dependencies are documented as `pip install` commands, not requirements.txt.
"""
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

# OpenTelemetry imports
from opentelemetry import trace
from scl.otel.otel import tracer, meter  # Assuming this is the correct import path

# External embedding function (behavior: performs embedding operation)
from scl.embeddings.embedding import embed

# Setup logger
logger = logging.getLogger(__name__)

# Setup metrics
capability_embedding_counter = meter.create_counter(
    "capability.embedding.generated",
    description="Number of times embedding_description is computed for a Capability"
)


class Capability(ABC):
    """
    Abstract base class for Skill and FunctionCall classes.
    Provides a common interface for both skill-based and function call-based implementations.

    Attributes:
        _name (str): Name of the capability.
        _type (str): Implementation type (e.g., 'skill', 'function').
        _description (Optional[str]): Human-readable description.
        _original_body (Optional[str]): Original source/body of the capability.
        _llm_description (Optional[str]): LLM-generated description for tool usage.
        _function_impl (Optional[str]): Actual code implementation for sandbox execution.
        _embedding_description (Optional[Any]): Cached embedding vector of the description.
    """

    @tracer.start_as_current_span("Capability.__init__")
    def __init__(self,
                 name: str,
                 type: str,
                 description: Optional[str] = None,
                 original_body: Optional[str] = None,
                 llm_description: Optional[str] = None,
                 function_impl: Optional[str] = None):
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", name)
        current_span.set_attribute("capability.type", type)
        current_span.set_attribute("capability.has_description", description is not None)
        current_span.set_attribute("capability.has_original_body", original_body is not None)
        current_span.set_attribute("capability.has_llm_description", llm_description is not None)
        current_span.set_attribute("capability.has_function_impl", function_impl is not None)

        self._name = name
        self._description = description
        self._embedding_description = None
        self._original_body = original_body
        self._type = type
        self._llm_description = llm_description
        self._function_impl = function_impl

        logger.info(
            "Initialized Capability",
            extra={
                "name": self._name,
                "type": self._type,
                "has_description": self._description is not None,
                "has_function_impl": self._function_impl is not None
            }
        )
        logger.debug(
            f"Capability created: name='{self._name}', type='{self._type}', "
            f"description_preview='{self._description[:50] if self._description else None}...'"
        )

    @property
    def name(self) -> str:
        """Name of the skill/function call."""
        return self._name

    @property
    def description(self) -> Optional[str]:
        """Function description for progressive loading."""
        return self._description

    @property
    def original_body(self) -> Optional[str]:
        """Original description body."""
        return self._original_body

    @property
    @tracer.start_as_current_span("Capability.embedding_description.get")
    def embedding_description(self):
        """
        Embedding vector of the description used for RAG progressive loading.
        Computed lazily and cached. Returns None if no description is available.
        """
        if self._embedding_description is None:
            current_span = trace.get_current_span()
            current_span.set_attribute("capability.name", self._name)
            current_span.set_attribute("capability.type", self._type)

            # Guard against missing description
            if self._description is None:
                self._embedding_description = None
                logger.warning(
                    "Cannot generate embedding for capability '%s': no description provided.",
                    self._name
                )
                current_span.set_attribute("embedding.skipped", True)
                return None

            logger.debug(f"Generating embedding for capability '{self._name}'")
            try:
                self._embedding_description = embed(self._description)
                capability_embedding_counter.add(1, {"capability.type": self._type})
                logger.info(f"Successfully generated embedding for capability '{self._name}'")
            except Exception as e:
                logger.error(f"Failed to generate embedding for capability '{self._name}': {e}", exc_info=True)
                current_span.record_exception(e)
                current_span.set_status(trace.Status(trace.StatusCode.ERROR, "Embedding generation failed"))
                raise  # Re-raise after logging/tracing

        return self._embedding_description

    @property
    def type(self) -> str:
        """Implementation type."""
        return self._type

    @property
    def llm_description(self) -> Optional[str]:
        """LLM-generated description for tool field."""
        return self._llm_description

    @property
    def function_impl(self) -> Optional[str]:
        """Function implementation for sandbox execution."""
        return self._function_impl

    @abstractmethod
    @tracer.start_as_current_span("Capability.execute")
    def execute(self, args_dict: Dict[str, Any]) -> Any:
        """
        Execute the capability with the given arguments.

        Subclasses must implement this method with concrete execution logic.

        Args:
            args_dict: Dictionary of argument names to values for the invocation.

        Returns:
            The result of the capability execution.

        Raises:
            NotImplementedError: If subclass does not implement this method.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("capability.name", self._name)
        current_span.set_attribute("capability.type", self._type)
        current_span.set_attribute("args.count", len(args_dict))
        logger.debug(f"Executing capability '{self._name}' with args: {list(args_dict.keys())}")
        # Implementation is left to subclasses; this method will not be called directly.
        raise NotImplementedError("Subclasses must implement execute method")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}'...)"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Capability):
            return False
        return (self._name == other._name and
                self._description == other._description and
                self._original_body == other._original_body)


# ----------------------------------------------------------------------
# Example usage (how other files would import and use this module)
# ----------------------------------------------------------------------
#
# In another file, say `skills.py`:
#
#   from capability import Capability
#
#   class Skill(Capability):
#       def execute(self, args_dict):
#           # Business logic for a skill
#           print(f"Executing skill {self.name} with {args_dict}")
#           return {"status": "ok", "result": f"Skill {self.name} done"}
#
#   class FunctionCall(Capability):
#       def execute(self, args_dict):
#           # Actually run the code in a sandbox
#           code = self.function_impl or "pass"
#           print(f"Running function {self.name} code: {code}")
#           # ... sandbox execution ...
#           return f"FunctionCall {self.name} result"
#
#   # Creating capabilities
#   skill1 = Skill(
#       name="web_search",
#       type="skill",
#       description="Search the web for information",
#       original_body="Search function..."
#   )
#   func1 = FunctionCall(
#       name="calculate",
#       type="function",
#       description="Calculate mathematical expression",
#       function_impl="def calculate(expr): return eval(expr)"
#   )
#
#   # Lazy embedding computation (first access triggers generation)
#   emb_skill = skill1.embedding_description
#   emb_func = func1.embedding_description
#
#   # Execute capabilities
#   result_skill = skill1.execute({"query": "latest news"})
#   result_func = func1.execute({"expr": "2+2"})