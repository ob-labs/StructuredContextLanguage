"""
FunctionCall Module

Represents a callable function capability, inheriting from Capability.
Implements the abstract execute method for sandboxed function execution.
Logic for execute method as below:
        func_code = cap.function_impl
        func_lines = [f"def dynamic_func({', '.join(args_dict.keys())}):"]
        func_lines.extend([f"    {line}" for line in func_code.split('\n')])
        func_def = '\n'.join(func_lines)
        local_vars = {}
        ## todo debug/trace
        logging.info(f"args_dict: {args_dict}")
        logging.info(f"func_def: {func_def}")
        exec(func_def, globals(), local_vars)
        func = local_vars['dynamic_func']
        return func(**args_dict)

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
"""
import logging
from typing import Optional, Dict, Any

from opentelemetry import trace
from scl.otel.otel import tracer, meter
from scl.embeddings.impl import embed
from scl.meta.capability import Capability

logger = logging.getLogger(__name__)

# Optional metric for function call executions
function_call_counter = meter.create_counter(
    "function_call.executed",
    description="Number of times a FunctionCall was executed"
)


class FunctionCall(Capability):
    """
    Concrete implementation of Capability for function call invocations.

    The execute method dynamically wraps the stored function_impl code
    into a function definition matching the provided arguments.
    """

    @tracer.start_as_current_span("FunctionCall.__init__")
    def __init__(self,
                 name: str,
                 description: str,
                 original_body: str,
                 llm_description: Optional[str] = None,
                 function_impl: Optional[str] = None):
        current_span = trace.get_current_span()
        current_span.set_attribute("function_call.name", name)
        current_span.set_attribute("function_call.has_function_impl", function_impl is not None)

        # Corrected parameter order to match Capability.__init__
        super().__init__(
            name=name,
            type="function_call",
            description=description,
            original_body=original_body,
            llm_description=llm_description,
            function_impl=function_impl
        )

        logger.debug(f"FunctionCall '{name}' initialized with impl length: {len(function_impl) if function_impl else 0}")
        logger.info(f"FunctionCall '{name}' created")

    @tracer.start_as_current_span("FunctionCall.execute")
    def execute(self, args_dict: Dict[str, Any]) -> Any:
        """
        Execute the function call with the provided arguments.

        Wraps the function_impl code into a dynamic function definition,
        executes it, and calls the resulting function with args_dict.

        Args:
            args_dict: Dictionary mapping parameter names to values.

        Returns:
            Result of the function execution.

        Raises:
            ValueError: If function_impl is missing or empty.
            Exception: Any exception raised during dynamic compilation or execution.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("function_call.name", self.name)
        current_span.set_attribute("function_call.args_count", len(args_dict))

        logger.debug(f"Executing FunctionCall '{self.name}' with args: {list(args_dict.keys())}")

        if not self.function_impl:
            error_msg = f"FunctionCall '{self.name}' has no function_impl to execute"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        try:
            # Build the dynamic function definition as per the design comments
            func_code = self.function_impl
            param_names = ', '.join(args_dict.keys())
            func_lines = [f"def dynamic_func({param_names}):"]
            # Indent each line of the function implementation for proper Python syntax
            func_lines.extend([f"    {line}" for line in func_code.split('\n')])
            func_def = '\n'.join(func_lines)

            # Log the generated code for debugging/traceability
            logger.info(f"args_dict: {args_dict}")
            logger.info(f"func_def: {func_def}")

            local_vars = {}
            # Execute the definition in the current global context
            exec(func_def, globals(), local_vars)
            func = local_vars['dynamic_func']

            # Call the newly defined function with the provided arguments
            result = func(**args_dict)

            function_call_counter.add(1, {"function_call.name": self.name})
            current_span.set_attribute("function_call.result_type", type(result).__name__)
            logger.info(f"FunctionCall '{self.name}' executed successfully")
            return result

        except Exception as e:
            logger.error(f"FunctionCall '{self.name}' execution failed: {e}", exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise

    def __repr__(self) -> str:
        return f"FunctionCall(name='{self.name}', impl_available={self.function_impl is not None})"


"""
    Example usage:
    --------------
    from scl.capabilities.function_call import FunctionCall

    # Create a simple arithmetic capability
    add_cap = FunctionCall(
        name="add",
        description="Returns the sum of two numbers",
        original_body="Adds a and b",
        function_impl="return a + b"
    )

    # Execute with parameters
    result = add_cap.execute({"a": 5, "b": 7})
    print(result)  # 12

    # A more complex function using a print statement
    greet_cap = FunctionCall(
        name="greet",
        description="Prints a greeting to a person",
        original_body="Greets the user by name",
        function_impl="print(f'Hello, {name}!')"
    )
    greet_cap.execute({"name": "Alice"})  # prints "Hello, Alice!"
"""