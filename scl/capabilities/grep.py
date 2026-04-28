"""
Grep Function Call Module

Implements a grep search capability using `igrep` as the primary backend, with
automatic fallback to the standard Unix `grep` when `igrep` is not available.
The tool provides a structured interface for regex file search, integrating with
the SCL function‑call system via the Capability base class.

Features:
- Regex Search
- Path Targeting (defaults to CWD, supports multiple paths)
- Glob Filtering (comma/space separated, brace expansion; translated to
  --include for grep)
- File Type Filter (--type) – **only available with igrep**; raises an error
  when used with grep
- Case-Insensitive Search (-i)
- Multiline Mode (-U --multiline-dotall) – **only available with igrep**;
  raises an error when used with grep
- Output Modes: files_with_matches, content, count
- Context Lines: -A, -B, -C (content mode only; supported by both igrep and
  GNU grep)
- Line Numbers toggle (content mode, default on)
- Pagination: head_limit & offset applied to final output
- Ignored Content: VCS dirs, permission-based ignore patterns – handled through
  igrep’s default ignore rules; grep does not automatically respect ignore files

OpenTelemetry: uses tracer, meter and structured logging for full observability.
"""
import logging
import os
import re
import subprocess
from itertools import product
from typing import Optional, Dict, Any, List, Union

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
    Uses `igrep` by preference; falls back to standard `grep` when `igrep`
    is not installed. Some advanced features are only available with `igrep`.
    """

    # Flags that are known to work with igrep (derived from `igrep --help`)
    _IGREP_SUPPORTED_OPTIONS = {
        "-i", "--ignore-case", "-S", "--smart-case",
        "-.", "--hidden", "-L", "--follow", "-w", "--word-regexp",
        "-g", "--glob", "-t", "--type", "-T", "--type-not",
        "--editor", "--custom-command", "--theme", "--context-viewer",
        "--type-list", "-h", "--help", "-V", "--version",
    }

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

        # Default search parameters (used when not overridden in execute)
        self.search_params = search_params or {}
        logger.debug(f"GrepFunctionCall '{name}' initialized with params: {self.search_params}")
        logger.info(f"GrepFunctionCall '{name}' created")

    @tracer.start_as_current_span("GrepFunctionCall.execute")
    def execute(self, args_dict: Dict[str, Any]) -> str:
        """
        Execute the grep search with the provided arguments.

        Args:
            args_dict: Dictionary containing search parameters. Merged with default
                       search_params. Supported keys: pattern, path (str or list of str),
                       glob, type, ignore_case, multiline, output_mode
                       (files_with_matches, content, count), context_before,
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
        current_span.set_attribute("grep.path", str(merged_args.get("path", os.getcwd())))

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
        Build the grep command using the appropriate binary (igrep or grep),
        translating options as needed for compatibility.
        """
        binary = self._get_grep_binary()
        cmd = [binary]

        # Features that are handled differently per binary
        output_mode = args_dict.get("output_mode", "content")
        ignore_case = args_dict.get("ignore_case", False)
        multiline = args_dict.get("multiline", False)
        line_numbers = args_dict.get("line_numbers", True)  # content mode only
        context_before = args_dict.get("context_before")
        context_after = args_dict.get("context_after")
        context_around = args_dict.get("context_around")
        glob_pattern = args_dict.get("glob")
        file_type = args_dict.get("type")

        # Supported flags common to both igrep and grep
        if ignore_case:
            cmd.append("-i")

        # Multiline mode – only igrep supports this
        if multiline:
            if binary == "grep":
                raise RuntimeError(
                    "Multiline mode (-U --multiline-dotall) is not supported by standard grep. "
                    "Install igrep to use this feature."
                )
            cmd.extend(["-U", "--multiline-dotall"])

        # Output modes
        if output_mode == "files_with_matches":
            cmd.append("-l")
        elif output_mode == "count":
            cmd.append("-c")
        elif output_mode == "content":
            if line_numbers:
                cmd.append("-n")
            if context_before is not None:
                cmd.extend(["-B", str(context_before)])
            if context_after is not None:
                cmd.extend(["-A", str(context_after)])
            if context_around is not None:
                cmd.extend(["-C", str(context_around)])

        # Glob filtering – igrep uses -g, grep uses --include
        if glob_pattern:
            globs = self._parse_glob(glob_pattern)
            if binary == "igrep":
                for g in globs:
                    cmd.extend(["-g", g])
            else:  # grep
                for g in globs:
                    cmd.extend(["--include", g])

        # File type filter – only igrep supports --type
        if file_type:
            if binary == "grep":
                raise RuntimeError(
                    "File type filtering (--type) is not supported by standard grep. "
                    "Install igrep to use this feature."
                )
            cmd.extend(["--type", file_type])

        # Pattern must come before path(s)
        cmd.append(args_dict["pattern"])

        # Path targeting – supports multiple paths
        paths = args_dict.get("path", os.getcwd())
        if isinstance(paths, str):
            cmd.append(paths)
        elif isinstance(paths, list):
            cmd.extend(paths)
        else:
            cmd.append(str(paths))

        return cmd

    def _get_grep_binary(self) -> str:
        """
        Choose the most capable binary available.
        Prefer `igrep`; fall back to standard `grep` if `igrep` is not found.
        """
        if self._is_binary_available("igrep"):
            logger.debug("Using 'igrep' for full feature support.")
            return "igrep"

        if self._is_binary_available("grep"):
            logger.debug("Using standard 'grep' (igrep not found).")
            return "grep"

        raise FileNotFoundError(
            "Neither `igrep` nor standard `grep` found. "
            "Please install igrep or ensure grep is in your PATH."
        )

    def _parse_glob(self, glob_pattern: str) -> List[str]:
        """
        Parse glob patterns. Supports comma/space separation and brace expansion.

        For example:
            "*.log, *.txt"             -> ["*.log", "*.txt"]
            "*.{js,ts}"                -> ["*.js", "*.ts"]
            "*.log,*.{md,rst} src/.*" -> ["*.log", "*.md", "*.rst", "src/.*"]
        """
        # Step 1: split by commas that are outside braces (replace them with spaces)
        depth = 0
        simplified = []
        for ch in glob_pattern:
            if ch == '{':
                depth += 1
                simplified.append(ch)
            elif ch == '}':
                depth -= 1
                simplified.append(ch)
            elif ch == ',' and depth == 0:
                simplified.append(' ')  # treat as whitespace separator
            else:
                simplified.append(ch)
        # Step 2: split by whitespace to obtain raw tokens
        raw_tokens = re.split(r'\s+', ''.join(simplified).strip())
        # Step 3: expand braces in each token
        expanded = []
        for token in raw_tokens:
            if not token:
                continue
            expanded.extend(self._expand_braces(token))
        return expanded if expanded else [glob_pattern]

    @staticmethod
    def _expand_braces(text: str) -> List[str]:
        """
        Expand brace groups like "{a,b}" into a list of strings.

        Supports multiple brace groups, e.g. "a{b,c}d{e,f}" -> ["abde", "abdf", "acde", "acdf"].
        No nesting of braces is supported.
        """
        # Find all brace groups
        brace_re = re.compile(r'\{([^{}]*)\}')
        matches = list(brace_re.finditer(text))
        if not matches:
            return [text]
        # Extract the comma-separated options for each group
        option_lists = [m.group(1).split(',') for m in matches]
        results = []
        for combo in product(*option_lists):
            # Reconstruct the string by replacing each brace group with the chosen option
            last_idx = 0
            parts = []
            for match, opt in zip(matches, combo):
                start, end = match.span()
                parts.append(text[last_idx:start])
                parts.append(opt)
                last_idx = end
            parts.append(text[last_idx:])
            results.append(''.join(parts))
        return results

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
            logger.error("grep binary not found. Please install igrep or ensure grep is in PATH.")
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

    # Find files containing a specific type (only works with igrep)
    result = grep_cap.execute({
        "pattern": "def",
        "path": ".",
        "output_mode": "files_with_matches",
        "type": "python"
    })
    print(result)

    # Use brace expansion in glob: "*.{md,rst}" will be expanded to "*.md" and "*.rst"
    grep_cap.search_params["glob"] = "*.{md,rst}"
    result = grep_cap.execute({"pattern": "TODO"})
    print(result)

    # Search multiple directories by passing a list of paths
    result = grep_cap.execute({
        "pattern": "FIXME",
        "path": ["/var/log", "/home/user/project"],
        "output_mode": "content"
    })
    print(result)
"""