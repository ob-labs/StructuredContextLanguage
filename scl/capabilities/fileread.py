"""
File Read Function Call Module

Represents a file reading capability, inheriting from Capability.
Implements the abstract execute method for reading file content with safety checks.

Features and design goals
--------------------------
- Read specific file according to path.
- Path Targeting (defaults to CWD, supports multiple allowed directories)
- Avoid reading binary files (pictures, video, pdf, executables, etc.)
- Returns file content or raises an error describing why the file cannot be read.
- Full OpenTelemetry instrumentation: tracing, metrics, structured logging.

Project Constraints Applied:
----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.
"""

import logging
import os
from typing import Any

from opentelemetry import trace

from scl.meta.capability import Capability
from scl.otel.otel import meter, tracer

logger = logging.getLogger(__name__)

# Metric counting file read executions
file_read_counter = meter.create_counter(
    "file_read.executed", description="Number of times a file was read (successful or attempted)"
)

# Known binary file extensions – these will be refused
_BINARY_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".zip",
    ".gz",
    ".tar",
    ".7z",
    ".rar",
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".iso",
    ".psd",
    ".ai",
    ".sketch",
    ".vsd",
    ".odt",
    ".ods",
    ".odp",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".class",
    ".pyc",
    ".pyo",
}


class FileRead(Capability):
    """
    Concrete implementation of Capability for reading text files.

    Parameters
    ----------
    name, description, original_body, llm_description : see Capability
    allowed_directories : Optional[List[str]]
        Directories from which files may be read. Relative paths are
        resolved against these directories. Defaults to [current working directory].
    """

    @tracer.start_as_current_span("FileRead.__init__")
    def __init__(
        self,
        name: str,
        description: str,
        original_body: str,
        llm_description: str | None = None,
        allowed_directories: list[str] | None = None,
    ):
        current_span = trace.get_current_span()
        current_span.set_attribute("file_read.name", name)

        if allowed_directories is None:
            allowed_directories = [os.getcwd()]
        # Ensure all directories are absolute for safe path comparison
        self._allowed_dirs = [os.path.abspath(d) for d in allowed_directories]

        current_span.set_attribute("file_read.allowed_directories", str(self._allowed_dirs))
        logger.debug(f"FileRead '{name}' allowed directories: {self._allowed_dirs}")

        super().__init__(
            name=name,
            type="file_read",
            description=description,
            original_body=original_body,
            llm_description=llm_description,
            function_impl=None,  # Concrete tool – no dynamic code
        )

        logger.info(f"FileRead capability '{name}' created")

    @tracer.start_as_current_span("FileRead.execute")
    def execute(self, args_dict: dict[str, Any]) -> str:
        """
        Execute the file read operation.

        Args:
            args_dict: Must contain a "path" key with the file path to read.
                       The path may be absolute or relative to any allowed directory.

        Returns:
            File content as a string.

        Raises:
            ValueError: If the path is missing, points outside allowed directories,
                         does not exist, or points to a binary file.
            OSError: On actual file reading errors.
        """
        current_span = trace.get_current_span()
        current_span.set_attribute("file_read.name", self.name)

        # Validate input
        if "path" not in args_dict:
            error_msg = "Missing required argument 'path'"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        raw_path = args_dict["path"]
        current_span.set_attribute("file_read.raw_path", raw_path)
        logger.debug(f"FileRead '{self.name}' requested path: {raw_path}")

        # Resolve absolute path: if relative, try to find it inside an allowed directory
        if os.path.isabs(raw_path):
            candidate = os.path.abspath(raw_path)
            # Check if it resides under any allowed directory
            if not any(candidate.startswith(d) for d in self._allowed_dirs):
                error_msg = f"Absolute path '{raw_path}' is not within allowed directories: {self._allowed_dirs}"
                logger.error(error_msg)
                current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)
            target_path = candidate
        else:
            # Search through allowed directories for the first match
            found = None
            for base_dir in self._allowed_dirs:
                candidate = os.path.join(base_dir, raw_path)
                if os.path.exists(candidate):
                    found = candidate
                    break
            if found is None:
                error_msg = f"Relative path '{raw_path}' does not exist in any allowed directory: {self._allowed_dirs}"
                logger.error(error_msg)
                current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)
            target_path = os.path.abspath(found)

        current_span.set_attribute("file_read.resolved_path", target_path)
        logger.info(f"FileRead resolved path: {target_path}")

        # Check existence and type
        if not os.path.exists(target_path):
            error_msg = f"File not found: {target_path}"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        if not os.path.isfile(target_path):
            error_msg = f"Path is not a regular file: {target_path}"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        # Binary file check using extensions
        _, ext = os.path.splitext(target_path)
        if ext.lower() in _BINARY_EXTENSIONS:
            error_msg = f"Binary file extension '{ext}' is not allowed for reading: {target_path}"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        # Attempt to read the content
        try:
            with open(target_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError as ude:
            error_msg = f"File could not be decoded as UTF-8: {target_path}"
            logger.error(error_msg)
            current_span.record_exception(ude)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg) from ude
        except OSError as ose:
            logger.error(f"OS error reading file {target_path}: {ose}", exc_info=True)
            current_span.record_exception(ose)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(ose)))
            raise

        # Success – update observability
        file_read_counter.add(1, {"file_read.name": self.name, "outcome": "success"})
        current_span.set_attribute("file_read.content_length", len(content))
        logger.info(
            f"FileRead '{self.name}' successfully read {len(content)} bytes from {target_path}"
        )
        return content

    def __repr__(self) -> str:
        return f"FileRead(name='{self.name}', allowed_dirs={self._allowed_dirs})"


"""
    Example usage:
    --------------
    from scl.capabilities.file_read import FileRead

    # Create a file reader limited to the current working directory
    reader = FileRead(
        name="local_file",
        description="Reads text files from the project directory",
        original_body="Read local text files"
    )

    # Read an existing file
    content = reader.execute({"path": "README.md"})
    print(content)

    # Having multiple allowed directories
    wide_reader = FileRead(
        name="wide_reader",
        description="Can read from /data and /tmp",
        original_body="Wide file access",
        allowed_directories=["/data", "/tmp"]
    )

    # Reading from an absolute path (must be inside an allowed directory)
    report = wide_reader.execute({"path": "/data/reports/summary.txt"})
    print(report[:100])

    # This will raise a ValueError because the extension is binary:
    # reader.execute({"path": "photo.png"})
"""
