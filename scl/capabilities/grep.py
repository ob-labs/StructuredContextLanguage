"""
Grep Function Call Module

Implements a grep search capability that uses the `igrep` bash command.
igrep --help
Interactive Grep

Usage: igrep [OPTIONS] --type-list <PATTERN> [PATHS]...

Arguments:
  <PATTERN>   Regular expression used for searching
  [PATHS]...  Files or directories to search. Directories are searched recursively. If not specified, searching starts from current directory

Options:
      --editor <EDITOR>
          Text editor used to open selected match [possible values: vim, neovim, nvim, nano, code, vscode, code-insiders, emacs, emacsclient, hx, helix, subl, sublime-text, micro, intellij, goland, pycharm, less]
      --custom-command <CUSTOM_COMMAND>
          Custom command used to open selected match. Must contain {file_name} and {line_number} tokens [env: IGREP_CUSTOM_EDITOR=]
      --theme <THEME>
          UI color theme [default: dark] [possible values: light, dark]
  -i, --ignore-case
          Searches case insensitively
  -S, --smart-case
          Searches case insensitively if the pattern is all lowercase. Search case sensitively otherwise
  -., --hidden
          Search hidden files and directories. By default, hidden files and directories are skipped
  -L, --follow
          Follow symbolic links while traversing directories
  -w, --word-regexp
          Only show matches surrounded by word boundaries
  -g, --glob <GLOB>
          Include files and directories for searching that match the given glob. Multiple globs may be provided
      --type-list
          Show all supported file types and their corresponding globs
  -t, --type <TYPE_MATCHING>
          Only search files matching TYPE. Multiple types may be provided
  -T, --type-not <TYPE_NOT>
          Do not search files matching TYPE-NOT. Multiple types-not may be provided
      --context-viewer <CONTEXT_VIEWER>
          Context viewer position at startup [default: none] [possible values: none, vertical, horizontal]
  -h, --help
          Print help
  -V, --version
          Print version

Inherits from Capability via FunctionCall to integrate with the SCL function-call system.

Features:
- Regex Search
- Path Targeting (defaults to CWD)
- Glob Filtering (comma/space separated, brace expansion)
- File Type Filter (--type)
- Case-Insensitive Search (-i)
- Multiline Mode (-U --multiline-dotall)
- Output Modes: files_with_matches, content, count
- Context Lines: -A, -B, -C (content mode only)
- Line Numbers toggle (content mode, default on)
- Pagination: head_limit & offset applied to final output
- Ignored Content: VCS dirs, permission-based ignore patterns, orphaned plugin caches
- Long line truncation (--max-columns 500)

OpenTelemetry: uses tracer, meter and structured logging for full observability.
"""
import logging
import os
import subprocess
from typing import Optional, Dict, Any, List

from opentelemetry import trace
from scl.otel.otel import tracer, meter
from scl.meta.capability import Capability

logger = logging.getLogger(__name__)

# Meter for grep executions
grep_execution_counter = meter.create_counter(
    "grep_function_call.executed",
    description="Number of times a grep function call was executed"
)

class GrepFunctionCall(Capability):
    """
    Concrete implementation of Capability for grep search invocations.
    Uses `igrep` (fallback to `rg`) to search files based on provided arguments.
    """

    @tracer.start_as_current_span("GrepFunctionCall.__init__")
    def __init__(self,
                 name: str,
                 description: str,
                 original_body: str,
                 llm_description: Optional[str] = None,
                 search_params: Optional[Dict] = None):
        current_span = trace.get_current_span()
        current_span.set_attribute("grep.name", name)

        super().__init__(
            name=name,
            type="grep_function_call",
            description=description,
            original_body=original_body,
            llm_description=llm_description
        )

        # Default search parameters
        self.search_params = search_params or {}
        logger.debug(f"GrepFunctionCall '{name}' initialized with params: {self.search_params}")
        logger.info(f"GrepFunctionCall '{name}' created")

    @tracer.start_as_current_span("GrepFunctionCall.execute")
    def execute(self, args_dict: Dict[str, Any]) -> str:
        """
        Execute the grep search with the provided arguments.

        Args:
            args_dict: Dictionary containing search parameters. Merged with default search_params.
                       Supported keys: pattern, path, glob, type, ignore_case, multiline,
                       output_mode (files_with_matches, content, count), context_before,
                       context_after, context_around, line_numbers, head_limit, offset.

        Returns:
            String containing the search output based on output_mode.
        """
        current_span = trace.get_current_span()
        # Merge defaults with runtime args; runtime args take precedence
        merged_args = {**self.search_params, **args_dict}

        pattern = merged_args.get("pattern")
        if not pattern:
            error_msg = "No search pattern provided"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        current_span.set_attribute("grep.pattern", pattern)
        current_span.set_attribute("grep.path", merged_args.get("path", os.getcwd()))

        try:
            cmd = self._build_command(merged_args)
            logger.info(f"Executing grep command: {' '.join(cmd)}")
            current_span.set_attribute("grep.command", ' '.join(cmd))

            result = self._run_command(cmd)

            # Apply pagination
            head_limit = merged_args.get("head_limit")
            offset = merged_args.get("offset", 0)
            if head_limit is not None or offset > 0:
                lines = result.splitlines()
                if offset > 0:
                    lines = lines[offset:]
                if head_limit is not None:
                    lines = lines[:head_limit]
                result = "\n".join(lines)

            grep_execution_counter.add(1, {"grep.name": self.name})
            current_span.set_attribute("grep.result_length", len(result))
            logger.info(f"GrepFunctionCall '{self.name}' executed successfully")
            return result

        except Exception as e:
            logger.error(f"GrepFunctionCall '{self.name}' execution failed: {e}", exc_info=True)
            current_span.record_exception(e)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise

    def _build_command(self, args_dict: Dict[str, Any]) -> List[str]:
        """
        Build the igrep/rg command from the provided arguments.
        """
        cmd = [self._get_grep_binary()]

        # Case-Insensitive
        if args_dict.get("ignore_case", False):
            cmd.append("-i")

        # Multiline Mode
        if args_dict.get("multiline", False):
            cmd.extend(["-U", "--multiline-dotall"])

        # Output Modes
        output_mode = args_dict.get("output_mode", "content")
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        elif output_mode == "content":
            # Line Numbers (default on)
            if args_dict.get("line_numbers", True):
                cmd.append("-n")
            # Context Lines
            context_before = args_dict.get("context_before")
            if context_before is not None:
                cmd.extend(["-B", str(context_before)])
            context_after = args_dict.get("context_after")
            if context_after is not None:
                cmd.extend(["-A", str(context_after)])
            context_around = args_dict.get("context_around")
            if context_around is not None:
                cmd.extend(["-C", str(context_around)])

        # Glob Filtering
        glob_pattern = args_dict.get("glob")
        if glob_pattern:
            # Support comma/space separated globs and brace expansion
            globs = self._parse_glob(glob_pattern)
            for g in globs:
                cmd.extend(["-g", g])

        # File Type Filter
        file_type = args_dict.get("type")
        if file_type:
            cmd.extend(["--type", file_type])

        # Long line truncation
        cmd.extend(["--max-columns", "500"])

        # Ignored Content: VCS dirs, hidden files etc. are ignored by default by rg/igrep
        # No additional flags needed unless we want to explicitly ignore patterns,
        # but that is covered by rg's default behavior and .gitignore / .ignore files.

        # Pattern and path must come last
        cmd.append(args_dict["pattern"])
        cmd.append(args_dict.get("path", os.getcwd()))

        return cmd

    def _parse_glob(self, glob_pattern: str) -> List[str]:
        """
        Parse glob patterns. Supports comma/space separation and simple brace expansion.
        """
        import re
        # Replace commas with spaces for uniformity
        pattern = glob_pattern.replace(',', ' ')
        parts = pattern.split()
        expanded = []
        # Rudimentary brace expansion (e.g., *. {js,ts} -> *.js, *.ts)
        brace_re = re.compile(r'^\{([^}]+)\}$')
        for part in parts:
            brace_match = brace_re.match(part)
            if brace_match:
                # This is a brace group; expand it
                inner = brace_match.group(1).split(',')
                expanded.extend([f"*.{ext}" for ext in inner])
            else:
                expanded.append(part)
        return expanded if expanded else [glob_pattern]

    def _get_grep_binary(self) -> str:
        """
        Determine which grep binary to use. Defaults to `igrep`, with fallback to `rg`.
        """
        # Check if `igrep` is available
        if self._is_binary_available("igrep"):
            return "igrep"
        elif self._is_binary_available("rg"):
            logger.warning("`igrep` not found, falling back to `rg` (ripgrep)")
            return "rg"
        else:
            raise FileNotFoundError("Neither `igrep` nor `rg` found. Please install ripgrep.")

    @staticmethod
    def _is_binary_available(name: str) -> bool:
        try:
            subprocess.run([name, "--version"], capture_output=True, check=False)
            return True
        except FileNotFoundError:
            return False

    def _run_command(self, cmd: List[str]) -> str:
        """
        Execute the grep command and return the output.
        """
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False  # grep returns 1 if no matches found
            )
            if result.returncode == 0:
                return result.stdout
            elif result.returncode == 1:
                # No matches found
                return ""
            else:
                error_msg = f"grep failed with code {result.returncode}: {result.stderr}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        except FileNotFoundError:
            logger.error("grep binary not found. Please install ripgrep and ensure it is in PATH.")
            raise

    def __repr__(self) -> str:
        return f"GrepFunctionCall(name='{self.name}', pattern='{self.search_params.get('pattern')}')"


"""
    Example usage:
    --------------
    from scl.capabilities.grep_function_call import GrepFunctionCall

    # Create a grep capability
    grep_cap = GrepFunctionCall(
        name="error_search",
        description="Search for ERROR patterns in log files",
        original_body="Searches for ERROR in log files",
        search_params={
            "glob": "*.log",
            "output_mode": "content",
            "ignore_case": False
        }
    )

    # Execute with a specific pattern
    result = grep_cap.execute({"pattern": "ERROR", "path": "/var/log/"})
    print(result)

    # Search with context lines and pagination
    result = grep_cap.execute({
        "pattern": "timeout",
        "path": ".",
        "output_mode": "content",
        "context_after": 2,
        "head_limit": 10,
        "offset": 5
    })
    print(result)

    # Find files containing a specific type
    result = grep_cap.execute({
        "pattern": "def",
        "path": ".",
        "output_mode": "files_with_matches",
        "type": "python",
        "max_columns": 200
    })
    print(result)
"""