"""
File Write Function Call Module

Represents a file Write capability, inheriting from Capability.
Implements the abstract execute method for writing file content with safety checks.

Features and design goals
--------------------------
- Write to specific file according to path.
- Path Targeting (defaults to CWD, supports multiple allowed directories for writing)
- Returns file content or raises an error describing why the file cannot be written.
- File permissions set as 644 by default.
- Support for file creation, appending content, or overwriting existing content.
- Full OpenTelemetry instrumentation: tracing, metrics, structured logging.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
"""
import logging
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

from opentelemetry import trace
from scl.otel.otel import tracer, meter
from scl.meta.capability import Capability

logger = logging.getLogger(__name__)

# Metric for file write operations
file_write_counter = meter.create_counter(
    "file_write.executed",
    description="Number of file write executions"
)


class FileWrite(Capability):
    """
    Capability that writes content to a file with safety and path restrictions.

    On execution, expects an args_dict with:
        - 'path': (str) the target file path (required)
        - 'content': (str) the content to write (required)
        - 'mode': (str) 'w' for overwrite, 'a' for append (default 'w')
    """

    @tracer.start_as_current_span("FileWrite.__init__")
    def __init__(
        self,
        name: str,
        description: str,
        original_body: str,
        llm_description: Optional[str] = None,
        allowed_dirs: Optional[List[str]] = None,
    ):
        current_span = trace.get_current_span()
        current_span.set_attribute("file_write.name", name)
        current_span.set_attribute("file_write.allowed_dirs", str(allowed_dirs))

        # The base Capability does not require function_impl; we set it to None
        super().__init__(
            name=name,
            type="file_write",
            description=description,
            original_body=original_body,
            llm_description=llm_description,
            function_impl=None,
        )

        # Normalise allowed directories to absolute paths (defaults to CWD)
        if allowed_dirs:
            self.allowed_dirs = [os.path.abspath(d) for d in allowed_dirs]
        else:
            self.allowed_dirs = [os.path.abspath(os.getcwd())]

        logger.debug(
            f"FileWrite '{name}' allowed directories: {self.allowed_dirs}"
        )
        logger.info(f"FileWrite '{name}' created")

    @tracer.start_as_current_span("FileWrite.execute")
    def execute(self, args_dict: Dict[str, Any]) -> str:
        """
        Execute the file write operation.

        Args:
            args_dict: Dictionary containing:
                - path (str): Target file path (relative or absolute)
                - content (str): Content to write
                - mode (str, optional): 'w' (default) or 'a'

        Returns:
            The content that was written (for verification).

        Raises:
            ValueError: If arguments are missing or invalid.
            PermissionError: If the target path is outside allowed directories.
            OSError: If file operations fail (permissions, file system issues).
        """
        current_span = trace.get_current_span()
        path_arg = args_dict.get("path")
        content = args_dict.get("content")
        mode = args_dict.get("mode", "w")

        # Validate arguments
        if not path_arg:
            error_msg = "Missing 'path' argument"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)
        if content is None:
            error_msg = "Missing 'content' argument"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)
        if mode not in ("w", "a"):
            error_msg = f"Invalid mode '{mode}': must be 'w' or 'a'"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        # Resolve absolute path
        target_path = os.path.abspath(path_arg)
        current_span.set_attribute("file_write.path", target_path)
        current_span.set_attribute("file_write.mode", mode)
        current_span.set_attribute("file_write.content_length", len(content))

        logger.debug(f"Resolved target path: {target_path}")

        # Security: ensure path is within at least one allowed directory
        allowed = False
        for allowed_dir in self.allowed_dirs:
            # Use commonpath to verify containment
            try:
                if os.path.commonpath([target_path, allowed_dir]) == allowed_dir:
                    allowed = True
                    break
            except ValueError:
                # Different drives on Windows, etc.
                continue
        if not allowed:
            error_msg = (
                f"Path '{target_path}' is outside allowed directories: "
                f"{self.allowed_dirs}"
            )
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise PermissionError(error_msg)

        # Write operation with 644 default permissions
        try:
            # Ensure parent directory exists (create if missing)
            parent_dir = os.path.dirname(target_path)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)
                logger.debug(f"Created parent directory: {parent_dir}")

            # Write content
            with open(target_path, mode, encoding="utf-8") as f:
                f.write(content)

            # Set permissions to 644 (owner rw, group r, others r)
            os.chmod(target_path, 0o644)

            # Update span and metrics
            file_write_counter.add(1, {"file_write.mode": mode})
            current_span.set_attribute("file_write.success", True)
            current_span.set_attribute("file_write.bytes_written", len(content))

            logger.info(
                f"File written successfully: {target_path} "
                f"(mode={mode}, bytes={len(content)})"
            )

            # Return the written content as per design goal
            return content

        except OSError as e:
            error_msg = f"Failed to write file '{target_path}': {e}"
            logger.error(error_msg, exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise OSError(error_msg) from e

    def __repr__(self) -> str:
        return (
            f"FileWrite(name='{self.name}', "
            f"allowed_dirs={self.allowed_dirs})"
        )


"""
    Example usage:
    --------------
    from scl.capabilities.file_write import FileWrite

    # Create a file write capability restricted to a specific directory
    writer = FileWrite(
        name="template_writer",
        description="Writes templates to the output directory",
        original_body="Writes file content safely",
        allowed_dirs=["/var/output", "/tmp/sandbox"]
    )

    # Overwrite (default mode 'w')
    result = writer.execute({
        "path": "/tmp/sandbox/hello.txt",
        "content": "Hello, world!\n"
    })
    print(result)  # "Hello, world!\n"

    # Append content
    result_append = writer.execute({
        "path": "/tmp/sandbox/hello.txt",
        "content": "This line is appended.\n",
        "mode": "a"
    })
    print(result_append)  # outputs the appended line

    # Attempt to write outside allowed directories raises PermissionError
    try:
        writer.execute({"path": "/etc/passwd", "content": "malicious"})
    except PermissionError as e:
        print(f"Blocked: {e}")
"""