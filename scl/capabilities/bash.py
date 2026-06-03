"""
Bash Function Call Module

Represents a Bash capability, inheriting from Capability.
Implements the abstract execute method for executing Bash commands with safety checks.

Features and design goals
--------------------------
- Execute Bash commands.
- Command Execution (defaults to CWD, supports multiple allowed directories)
- Avoid executing harmful commands (e.g., rm -rf, sudo)
- Returns command output or raises an error describing why the command cannot be executed.

----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
"""

import logging
import os
import subprocess
from typing import Any

from opentelemetry import trace

from scl.meta.capability import Capability
from scl.otel.otel import meter, tracer

logger = logging.getLogger(__name__)

bash_execution_counter = meter.create_counter(
    "bash_command.executed", description="Number of times a bash command was executed"
)

# Patterns that indicate dangerous commands (case‑insensitive)
DANGEROUS_PATTERNS = [
    "rm -rf",
    "rm -r",
    "sudo",
    "mkfs",
    "dd if=",
    ":(){ :|:& };:",  # fork bomb
    "chmod 777",
    "wget",
    "curl",
    "shutdown",
    "reboot",
]


class BashFunctionCall(Capability):
    """
    Capability that safely executes a Bash command.

    The command is stored in `original_body` and may contain Python‑style
    format placeholders that will be filled from `args_dict` at execution time.
    """

    @tracer.start_as_current_span("BashFunctionCall.__init__")
    def __init__(
        self,
        name: str,
        description: str,
        original_body: str,
        llm_description: str | None = None,
        function_impl: str | None = None,
        allowed_directories: list[str] | None = None,
    ):
        current_span = trace.get_current_span()
        current_span.set_attribute("bash_function_call.name", name)
        current_span.set_attribute(
            "bash_function_call.has_allowed_dirs",
            allowed_directories is not None,
        )

        super().__init__(
            name=name,
            type="bash_function_call",
            description=description,
            original_body=original_body,
            llm_description=llm_description,
            function_impl=function_impl,
        )

        # If not provided, only the current working directory is allowed.
        self.allowed_directories = (
            allowed_directories if allowed_directories is not None else [os.getcwd()]
        )

        logger.debug(
            "BashFunctionCall '%s' initialized with allowed dirs: %s",
            name,
            self.allowed_directories,
        )
        logger.info("BashFunctionCall '%s' created", name)

    @staticmethod
    def _is_dangerous(command: str) -> str | None:
        """Check if the command contains a dangerous pattern."""
        command_lower = command.lower()
        for pattern in DANGEROUS_PATTERNS:
            if pattern in command_lower:
                return pattern
        return None

    @tracer.start_as_current_span("BashFunctionCall.execute")
    def execute(self, args_dict: dict[str, Any]) -> str:
        """
        Format the command with `args_dict`, perform safety checks, and run it.

        Args:
            args_dict: Dictionary of parameters to substitute into the command.
                       May also contain:
                         - 'cwd' : override the working directory (must be allowed).

        Returns:
            The command's stdout output as a string.

        Raises:
            ValueError: If a dangerous pattern is detected or the command is empty.
            RuntimeError: If the subprocess returns a non‑zero exit code.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("bash_function_call.name", self.name)

        # Retrieve the command template from the capability's original body
        if not self.original_body:
            error_msg = f"BashFunctionCall '{self.name}' has no command to execute"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        # Substitute parameters
        try:
            command = self.original_body.format(**args_dict)
        except KeyError as e:
            error_msg = f"Missing argument {e} for command '{self.original_body}'"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg) from e

        logger.info("Prepared command: %s", command)
        current_span.set_attribute("bash.command", command)
        current_span.add_event("bash.command.text", {"command": command})

        # Safety check
        dangerous_pattern = self._is_dangerous(command)
        if dangerous_pattern:
            error_msg = (
                f"Command '{command}' contains dangerous pattern '{dangerous_pattern}' "
                "and will not be executed."
            )
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        # Determine working directory
        cwd = args_dict.get("cwd", os.getcwd())
        if not any(
            os.path.abspath(cwd).startswith(os.path.abspath(allowed_dir))
            for allowed_dir in self.allowed_directories
        ):
            error_msg = (
                f"Working directory '{cwd}' is not within allowed directories: "
                f"{self.allowed_directories}"
            )
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        logger.debug("Running command in directory: %s", cwd)

        try:
            result = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                cwd=cwd,
                capture_output=True,
                text=True,
            )
        except Exception as e:
            logger.error("Command execution failed: %s", e, exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise

        stdout = result.stdout
        stderr = result.stderr
        returncode = result.returncode

        current_span.set_attribute("bash.returncode", returncode)
        current_span.set_attribute("bash.stdout_length", len(stdout))
        current_span.set_attribute("bash.stderr_length", len(stderr))

        if returncode != 0:
            error_msg = f"Command failed (exit {returncode}):\n{stderr}"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise RuntimeError(error_msg)

        logger.info("Command executed successfully, output length: %d", len(stdout))
        logger.debug("stdout: %s", stdout)
        bash_execution_counter.add(1, {"bash_function_call.name": self.name})

        return stdout

    def __repr__(self) -> str:
        return f"BashFunctionCall(name='{self.name}', allowed_dirs={self.allowed_directories})"


"""
    Example usage:
    --------------
    from scl.capabilities.bash import BashFunctionCall

    # A simple echo command with a placeholder
    greet = BashFunctionCall(
        name="greet",
        description="Greet a person using echo",
        original_body='echo "Hello {name}!"',
    )
    output = greet.execute({"name": "Alice"})
    print(output)  # prints "Hello Alice!" (with trailing newline)

    # Using a custom working directory (must be under an allowed directory)
    lister = BashFunctionCall(
        name="list_home",
        description="List files in the home directory",
        original_body="ls -la",
        allowed_directories=["/home", "/tmp"],
    )
    output = lister.execute({"cwd": "/home/user"})
    print(output)

    # Trying to execute a dangerous command raises an error
    dangerous = BashFunctionCall(
        name="danger",
        description="This should be blocked",
        original_body="rm -rf /tmp/test",
    )
    try:
        dangerous.execute({})
    except ValueError as e:
        print(e)  # will print the safety violation message
"""
