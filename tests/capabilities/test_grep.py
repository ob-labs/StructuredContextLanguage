"""
Tests for GrepFunctionCall (grep.py)
Uses pytest and mocking to avoid real subprocess and OpenTelemetry calls.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from scl.capabilities.grep import GrepFunctionCall

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_span():
    """Return a mock span that records set_attribute and set_status."""
    span = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=None)
    return span


@pytest.fixture
def mock_tracer(mock_span):
    """Patch the module-level tracer and trace.get_current_span to return mock spans."""
    with (
        patch("scl.capabilities.grep.tracer") as tracer_mock,
        patch("scl.capabilities.grep.trace.get_current_span", return_value=mock_span),
    ):
        tracer_mock.start_as_current_span.return_value = mock_span
        yield tracer_mock


@pytest.fixture
def mock_meter():
    """Patch the module-level meter and its counter."""
    with patch("scl.capabilities.grep.meter") as meter_mock:
        counter_mock = MagicMock()
        meter_mock.create_counter.return_value = counter_mock
        with patch("scl.capabilities.grep.grep_execution_counter", counter_mock):
            yield meter_mock, counter_mock


@pytest.fixture
def mock_capability_init():
    """
    Prevent real Capability.__init__ from running. We manually set required
    attributes in the instance fixtures afterwards.
    """
    with patch.object(GrepFunctionCall.__bases__[0], "__init__") as m:
        m.return_value = None
        yield m


@pytest.fixture
def default_instance(mock_tracer, mock_meter, mock_capability_init):
    """Create a basic GrepFunctionCall instance with no extra search params."""
    inst = GrepFunctionCall(
        name="test_grep",
        description="test desc",
        original_body="original",
        llm_description="llm desc",
    )
    # Manually set attributes that Capability.__init__ would normally set
    inst._name = "test_grep"  # name is a read-only property; set the backing field
    inst._description = "test desc"
    inst._type = "grep_function_call"
    inst._original_body = "original"
    inst._llm_description = "llm desc"
    return inst


@pytest.fixture
def instance_with_params(mock_tracer, mock_meter, mock_capability_init):
    """Create instance with default search_params."""
    search_params = {"glob": "*.py", "output_mode": "content", "ignore_case": True}
    inst = GrepFunctionCall(
        name="test_grep",
        description="test desc",
        original_body="original",
        search_params=search_params,
    )
    inst._name = "test_grep"  # name is a read-only property; set the backing field
    inst._description = "test desc"
    inst._type = "grep_function_call"
    inst._original_body = "original"
    inst._llm_description = None
    return inst


# ---------------------------------------------------------------------------
# Tests for __init__
# ---------------------------------------------------------------------------


def test_init_basic(default_instance, mock_capability_init):
    """Verify attributes are set correctly."""
    assert default_instance.name == "test_grep"
    assert default_instance.search_params == {}


def test_init_with_search_params(instance_with_params):
    """Search params are stored."""
    assert instance_with_params.search_params["glob"] == "*.py"
    assert instance_with_params.search_params["ignore_case"] is True


# ---------------------------------------------------------------------------
# Tests for execute method
# ---------------------------------------------------------------------------


def test_execute_pattern_missing(default_instance):
    """Raises ValueError when pattern is not provided."""
    with pytest.raises(ValueError, match="No search pattern provided"):
        default_instance.execute({})


def test_execute_merging_args(default_instance, mock_span):
    """Runtime args override default search_params."""
    default_instance.search_params = {"path": "/tmp", "output_mode": "count"}
    default_instance._build_command = MagicMock(return_value=["igrep", "pattern", "/other"])
    default_instance._run_command = MagicMock(return_value="5\n")

    result = default_instance.execute({"pattern": "needle", "path": "/other"})
    cmd_args = default_instance._build_command.call_args[0][0]
    assert cmd_args["path"] == "/other"
    assert cmd_args["pattern"] == "needle"
    assert cmd_args["output_mode"] == "count"  # from default


def test_execute_success_no_pagination(default_instance, mock_span):
    """Basic execution returns run_command output."""
    default_instance._build_command = MagicMock(return_value=["igrep", "test", "."])
    default_instance._run_command = MagicMock(return_value="file1.py:1:test\nfile2.py:5:test")
    result = default_instance.execute({"pattern": "test"})
    assert result == "file1.py:1:test\nfile2.py:5:test"
    # Counter should be incremented
    from scl.capabilities.grep import grep_execution_counter

    grep_execution_counter.add.assert_called_once_with(1, {"grep.name": "test_grep"})


def test_execute_pagination_head_limit(default_instance):
    """Apply head_limit to output lines."""
    default_instance._build_command = MagicMock(return_value=["igrep", "x", "."])
    default_instance._run_command = MagicMock(return_value="a\nb\nc\nd\n")
    result = default_instance.execute({"pattern": "x", "head_limit": 2})
    assert result == "a\nb"


def test_execute_pagination_offset(default_instance):
    """Apply offset to output lines."""
    default_instance._build_command = MagicMock(return_value=["igrep", "x", "."])
    default_instance._run_command = MagicMock(return_value="0\n1\n2\n3\n4")
    result = default_instance.execute({"pattern": "x", "offset": 2})
    assert result == "2\n3\n4"


def test_execute_pagination_head_limit_and_offset(default_instance):
    """Apply offset then head_limit."""
    default_instance._build_command = MagicMock(return_value=["igrep", "x", "."])
    default_instance._run_command = MagicMock(return_value="line0\nline1\nline2\nline3\nline4")
    result = default_instance.execute({"pattern": "x", "offset": 1, "head_limit": 2})
    assert result == "line1\nline2"


def test_execute_empty_output(default_instance):
    """Empty output remains empty after pagination."""
    default_instance._build_command = MagicMock(return_value=["igrep", "x", "."])
    default_instance._run_command = MagicMock(return_value="")
    result = default_instance.execute({"pattern": "x"})
    assert result == ""


def test_execute_records_exception(mock_span, default_instance):
    """Exception during execution is logged to span and re-raised."""
    default_instance._build_command = MagicMock(return_value=["igrep", "x", "."])
    default_instance._run_command = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        default_instance.execute({"pattern": "x"})
    mock_span.record_exception.assert_called_once()
    mock_span.set_status.assert_called()


# ---------------------------------------------------------------------------
# Tests for _build_command
# ---------------------------------------------------------------------------


def test_build_command_minimal(default_instance):
    """Only pattern and default path."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo"})
    assert cmd[:1] == ["igrep"]
    assert cmd[-2:] == ["foo", os.getcwd()]


def test_build_command_ignore_case(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "ignore_case": True})
    assert "-i" in cmd


def test_build_command_multiline_igrep(default_instance):
    """Multiline mode with igrep adds -U and --multiline-dotall."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "multiline": True})
    assert "-U" in cmd
    assert "--multiline-dotall" in cmd


def test_build_command_multiline_grep_raises(default_instance):
    """Multiline mode with standard grep raises RuntimeError."""
    default_instance._get_grep_binary = MagicMock(return_value="grep")
    with pytest.raises(RuntimeError, match="Multiline mode .* not supported by standard grep"):
        default_instance._build_command({"pattern": "foo", "multiline": True})


def test_build_command_output_files_with_matches(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "output_mode": "files_with_matches"})
    assert "-l" in cmd
    assert "-n" not in cmd


def test_build_command_output_count(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "output_mode": "count"})
    assert "-c" in cmd


def test_build_command_output_content_default_line_numbers(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "output_mode": "content"})
    assert "-n" in cmd


def test_build_command_output_content_no_line_numbers(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command(
        {"pattern": "foo", "output_mode": "content", "line_numbers": False}
    )
    assert "-n" not in cmd


def test_build_command_context_lines(default_instance):
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command(
        {
            "pattern": "foo",
            "output_mode": "content",
            "context_before": 2,
            "context_after": 1,
            "context_around": 3,
        }
    )
    assert cmd[cmd.index("-B") + 1] == "2"
    assert cmd[cmd.index("-A") + 1] == "1"
    assert cmd[cmd.index("-C") + 1] == "3"


def test_build_command_glob_single_igrep(default_instance):
    """With igrep, glob uses -g per pattern."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "glob": "*.py"})
    assert "-g" in cmd
    idx = cmd.index("-g")
    assert cmd[idx + 1] == "*.py"


def test_build_command_glob_single_grep(default_instance):
    """With standard grep, glob uses --include."""
    default_instance._get_grep_binary = MagicMock(return_value="grep")
    cmd = default_instance._build_command({"pattern": "foo", "glob": "*.py"})
    assert "--include" in cmd
    assert "-g" not in cmd
    idx = cmd.index("--include")
    assert cmd[idx + 1] == "*.py"


def test_build_command_glob_brace_expansion_igrep(default_instance):
    """Brace expansion and multiple -g flags with igrep."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "glob": "*. {js,ts}"})
    # tokens: ['*.', 'js', 'ts'] => three -g occurrences
    assert cmd.count("-g") == 3
    g_values = [cmd[i + 1] for i, val in enumerate(cmd) if val == "-g"]
    assert g_values == ["*.", "js", "ts"]


def test_build_command_glob_brace_expansion_grep(default_instance):
    """Brace expansion and multiple --include flags with grep."""
    default_instance._get_grep_binary = MagicMock(return_value="grep")
    cmd = default_instance._build_command({"pattern": "foo", "glob": "*. {js,ts}"})
    assert cmd.count("--include") == 3
    includes = [cmd[i + 1] for i, val in enumerate(cmd) if val == "--include"]
    assert includes == ["*.", "js", "ts"]


def test_build_command_type_filter_igrep(default_instance):
    """File type filtering with igrep uses --type."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "type": "python"})
    assert "--type" in cmd
    assert cmd[cmd.index("--type") + 1] == "python"


def test_build_command_type_filter_grep_raises(default_instance):
    """File type filtering with standard grep raises RuntimeError."""
    default_instance._get_grep_binary = MagicMock(return_value="grep")
    with pytest.raises(RuntimeError, match="File type filtering .* not supported by standard grep"):
        default_instance._build_command({"pattern": "foo", "type": "python"})


def test_build_command_explicit_path(default_instance):
    """Explicit path is appended to command."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "path": "/custom"})
    assert cmd[-1] == "/custom"


def test_build_command_path_list(default_instance):
    """Multiple paths are all appended."""
    default_instance._get_grep_binary = MagicMock(return_value="igrep")
    cmd = default_instance._build_command({"pattern": "foo", "path": ["/dir1", "/dir2"]})
    assert cmd[-2:] == ["/dir1", "/dir2"]


# ---------------------------------------------------------------------------
# Tests for _parse_glob
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "input_glob,expected_list",
    [
        ("*.py", ["*.py"]),
        ("*.py,*.txt", ["*.py", "*.txt"]),
        ("*.py *.txt", ["*.py", "*.txt"]),
        ("*.py, *.txt", ["*.py", "*.txt"]),
        ("*. {js,ts}", ["*.", "js", "ts"]),
        ("{js,ts}", ["js", "ts"]),
        ("single", ["single"]),
    ],
)
def test_parse_glob(default_instance, input_glob, expected_list):
    result = default_instance._parse_glob(input_glob)
    assert result == expected_list


def test_parse_glob_complex_mix(default_instance):
    result = default_instance._parse_glob("*.py, {css,html}")
    assert result == ["*.py", "css", "html"]


# ---------------------------------------------------------------------------
# Tests for _get_grep_binary
# ---------------------------------------------------------------------------


@patch("scl.capabilities.grep.subprocess.run")
def test_get_grep_binary_igrep_available(mock_run, default_instance):
    """Preferred binary igrep is found."""
    mock_run.side_effect = [MagicMock(returncode=0)]
    bin_name = default_instance._get_grep_binary()
    assert bin_name == "igrep"
    assert mock_run.call_args_list[0][0][0] == ["igrep", "--version"]


@patch("scl.capabilities.grep.subprocess.run")
def test_get_grep_binary_fallback_to_grep(mock_run, default_instance):
    """Fall back to grep when igrep not found."""
    mock_run.side_effect = [FileNotFoundError, MagicMock(returncode=0)]
    bin_name = default_instance._get_grep_binary()
    assert bin_name == "grep"


@patch("scl.capabilities.grep.subprocess.run")
def test_get_grep_binary_neither_available(mock_run, default_instance):
    """Raise FileNotFoundError when neither binary is available."""
    mock_run.side_effect = FileNotFoundError
    with pytest.raises(FileNotFoundError, match="Neither `igrep` nor standard `grep` found"):
        default_instance._get_grep_binary()


# ---------------------------------------------------------------------------
# Tests for _is_binary_available
# ---------------------------------------------------------------------------


@patch("scl.capabilities.grep.subprocess.run")
def test_is_binary_available_true(mock_run):
    mock_run.return_value = MagicMock(returncode=0)
    assert GrepFunctionCall._is_binary_available("igrep") is True


@patch("scl.capabilities.grep.subprocess.run")
def test_is_binary_available_false_not_found(mock_run):
    mock_run.side_effect = FileNotFoundError
    assert GrepFunctionCall._is_binary_available("nosuch") is False


# ---------------------------------------------------------------------------
# Tests for _run_command
# ---------------------------------------------------------------------------


@patch("scl.capabilities.grep.subprocess.run")
def test_run_command_success(mock_run, default_instance):
    mock_run.return_value = MagicMock(returncode=0, stdout="output")
    result = default_instance._run_command(["igrep", "test", "."])
    assert result == "output"


@patch("scl.capabilities.grep.subprocess.run")
def test_run_command_no_match(mock_run, default_instance):
    mock_run.return_value = MagicMock(returncode=1, stdout="")
    result = default_instance._run_command(["igrep", "test", "."])
    assert result == ""


@patch("scl.capabilities.grep.subprocess.run")
def test_run_command_error(mock_run, default_instance):
    mock_run.return_value = MagicMock(returncode=2, stderr="permission denied")
    with pytest.raises(RuntimeError, match="grep failed with code 2"):
        default_instance._run_command(["igrep", "test", "."])


@patch("scl.capabilities.grep.subprocess.run")
def test_run_command_binary_not_found(mock_run, default_instance):
    mock_run.side_effect = FileNotFoundError
    with pytest.raises(FileNotFoundError):
        default_instance._run_command(["igrep", "test", "."])


# ---------------------------------------------------------------------------
# Test __repr__
# ---------------------------------------------------------------------------


def test_repr(default_instance):
    default_instance.search_params["pattern"] = "hello"
    rep = repr(default_instance)
    assert "GrepFunctionCall(name='test_grep'" in rep
    assert "hello" in rep


# ---------------------------------------------------------------------------
# Integration test: real grep execution on grep.py source
# ---------------------------------------------------------------------------


def test_real_grep_on_source_file(default_instance, mock_tracer, mock_meter):
    """
    Real grep execution on grep.py source using the standard `grep` binary.
    Patches `_get_grep_binary` to force `grep` to avoid dependency on igrep.
    """
    with patch.object(default_instance, "_get_grep_binary", return_value="grep"):
        # Construct absolute path to scl/capabilities/grep.py (repo root is two
        # levels up from this test dir: tests/capabilities/).
        test_dir = os.path.dirname(os.path.abspath(__file__))
        source_path = os.path.normpath(
            os.path.join(test_dir, "..", "..", "scl", "capabilities", "grep.py")
        )

        pattern = "Grep Function Call Module"  # unique phrase in the docstring

        result = default_instance.execute(
            {
                "pattern": pattern,
                "path": source_path,
                "output_mode": "content",
                "line_numbers": False,
            }
        )

        assert pattern in result, f"Pattern {pattern!r} not found in output:\n{result}"
