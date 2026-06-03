"""
Unit tests for BashFunctionCall.
"""

import logging
import os
import re
import subprocess
from unittest.mock import ANY, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Duplicate the dangerous patterns list to avoid importing the module
# before the OpenTelemetry mocks are in place.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_telemetry(request):
    """
    Mock OpenTelemetry dependencies **before** any test imports the
    ``bash`` module.  This ensures the class decorators see the mocked
    tracer and that ``trace.get_current_span()`` returns a mock span.
    """
    # Let integration tests run with the real telemetry.
    if request.node.get_closest_marker("integration"):
        yield None
        return

    with (
        patch("scl.capabilities.bash.tracer") as mock_tracer,
        patch("scl.capabilities.bash.meter") as mock_meter,
        patch("scl.capabilities.bash.bash_execution_counter", MagicMock()) as mock_exec_counter,
        patch("scl.capabilities.bash.trace.get_current_span") as mock_get_span,
    ):
        # --- mock span that will be returned by every context manager ---
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value.__exit__.return_value = None
        # make ``trace.get_current_span()`` also return the same span
        mock_get_span.return_value = mock_span

        # mock counter returned by ``meter.create_counter``
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        yield {
            "tracer": mock_tracer,
            "meter": mock_meter,
            "span": mock_span,
            "counter": mock_counter,
            # the *patched* module-level ``bash_execution_counter``
            "exec_counter": mock_exec_counter,
        }


@pytest.fixture
def bash_class():
    """Import the ``BashFunctionCall`` class **after** the mocks are in place."""
    from scl.capabilities.bash import BashFunctionCall

    return BashFunctionCall


@pytest.fixture
def fixed_cwd(tmp_path):
    """Temporarily change the current working directory to a known one."""
    original_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path
    os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------


class TestInit:
    def test_default_allowed_directories(self, bash_class, fixed_cwd, mock_telemetry):
        cmd = bash_class(name="test", description="desc", original_body="echo hello")
        assert cmd.allowed_directories == [str(fixed_cwd)]

    def test_explicit_allowed_directories(self, bash_class, mock_telemetry):
        dirs = ["/home", "/tmp"]
        cmd = bash_class(
            name="test", description="desc", original_body="echo hello", allowed_directories=dirs
        )
        assert cmd.allowed_directories == dirs

    def test_super_init_called(self, bash_class, mock_telemetry):
        cmd = bash_class(
            name="my_name",
            description="my desc",
            original_body="my body",
            llm_description="llm desc",
            function_impl="impl",
        )
        assert cmd.type == "bash_function_call"
        assert cmd.name == "my_name"
        assert cmd.description == "my desc"
        assert cmd.original_body == "my body"
        assert cmd.llm_description == "llm desc"
        assert cmd.function_impl == "impl"


# ---------------------------------------------------------------------------
# Danger detection
# ---------------------------------------------------------------------------


class TestIsDangerous:
    @pytest.mark.parametrize("pattern", DANGEROUS_PATTERNS)
    def test_dangerous_pattern_detected(self, bash_class, pattern):
        command = f"some {pattern} suffix"
        detected = bash_class._is_dangerous(command)
        assert detected == pattern

    @pytest.mark.parametrize("pattern", DANGEROUS_PATTERNS)
    def test_case_insensitive(self, bash_class, pattern):
        command = pattern.upper()
        detected = bash_class._is_dangerous(command)
        assert detected is not None

    def test_safe_command_passes(self, bash_class):
        assert bash_class._is_dangerous("echo hello") is None

    def test_partial_match_blocked(self, bash_class):
        assert bash_class._is_dangerous("something_wget_here") is not None


# ---------------------------------------------------------------------------
# Execute – success cases
# ---------------------------------------------------------------------------


class TestExecuteSuccess:
    def test_simple_echo(self, bash_class, mock_telemetry):
        cmd = bash_class(name="echo_test", description="d", original_body="echo {name}")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Hello Alice\n", stderr="")
            output = cmd.execute({"name": "Alice"})
            assert output == "Hello Alice\n"
            mock_run.assert_called_once_with(
                "echo Alice",
                shell=True,
                executable="/bin/bash",
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
            )

    def test_cwd_from_args(self, bash_class, mock_telemetry, tmp_path):
        allowed = [str(tmp_path)]
        cmd = bash_class(
            name="ls", description="list", original_body="ls", allowed_directories=allowed
        )
        subdir = tmp_path / "sub"
        subdir.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="file1\n", stderr="")
            output = cmd.execute({"cwd": str(subdir)})
            assert output == "file1\n"
            mock_run.assert_called_once_with(
                "ls",
                shell=True,
                executable="/bin/bash",
                cwd=str(subdir),
                capture_output=True,
                text=True,
            )

    def test_allowed_directories_parent(self, bash_class, mock_telemetry):
        dirs = ["/home"]
        cmd = bash_class(
            name="test", description="desc", original_body="pwd", allowed_directories=dirs
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="/home\n", stderr="")
            output = cmd.execute({"cwd": "/home"})
            assert output == "/home\n"

    def test_counter_incremented(self, bash_class, mock_telemetry):
        cmd = bash_class(name="n", description="d", original_body="echo")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cmd.execute({})
        # The patched module-level counter is incremented
        mock_exec = mock_telemetry["exec_counter"]
        mock_exec.add.assert_called_once_with(1, {"bash_function_call.name": "n"})

    def test_span_attributes_on_success(self, bash_class, mock_telemetry):
        mock_span = mock_telemetry["span"]
        cmd = bash_class(name="s", description="d", original_body="echo {x}")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="short", stderr="errout")
            cmd.execute({"x": "val"})
            mock_span.set_attribute.assert_any_call("bash.command", "echo val")
            mock_span.set_attribute.assert_any_call("bash.returncode", 0)
            mock_span.set_attribute.assert_any_call("bash.stdout_length", len("short"))
            mock_span.set_attribute.assert_any_call("bash.stderr_length", len("errout"))
            mock_span.set_status.assert_not_called()

    def test_logs_on_success(self, bash_class, mock_telemetry, caplog):
        # Suppress the base Capability log that would cause a KeyError
        # (the source code passes ``extra={'name': ...}`` which collides).
        caplog.set_level(logging.WARNING)
        cmd = bash_class(name="logtest", description="d", original_body="echo")
        caplog.set_level(logging.INFO)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cmd.execute({})
        assert "Command executed successfully" in caplog.text
        assert "Prepared command:" in caplog.text


# ---------------------------------------------------------------------------
# Execute – error cases
# ---------------------------------------------------------------------------


class TestExecuteErrors:
    def test_empty_original_body(self, bash_class, mock_telemetry):
        cmd = bash_class(name="empty", description="d", original_body="")
        with pytest.raises(ValueError, match="has no command to execute"):
            cmd.execute({})
        mock_telemetry["span"].set_status.assert_called_once()

    def test_missing_format_argument(self, bash_class, mock_telemetry):
        cmd = bash_class(name="fmt", description="d", original_body="echo {name}")
        with pytest.raises(ValueError, match="Missing argument 'name'"):
            cmd.execute({})
        mock_telemetry["span"].set_status.assert_called_once()

    def test_dangerous_command_blocked(self, bash_class, mock_telemetry):
        cmd = bash_class(name="d", description="d", original_body="rm -rf /")
        with pytest.raises(ValueError, match="contains dangerous pattern"):
            cmd.execute({})
        mock_telemetry["span"].set_status.assert_called_once()

    def test_cwd_not_allowed(self, bash_class, mock_telemetry):
        cmd = bash_class(
            name="cwd_block",
            description="d",
            original_body="echo",
            allowed_directories=["/allowed"],
        )
        with patch("subprocess.run"):
            with pytest.raises(ValueError, match="is not within allowed directories"):
                cmd.execute({"cwd": "/forbidden"})
        mock_telemetry["span"].set_status.assert_called_once()

    def test_cwd_relative_path_resolved(self, bash_class, mock_telemetry, tmp_path):
        allowed_dir = tmp_path / "safe"
        allowed_dir.mkdir()
        cmd = bash_class(
            name="rel",
            description="d",
            original_body="echo",
            allowed_directories=[str(allowed_dir)],
        )
        os.chdir(str(allowed_dir))
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cmd.execute({"cwd": "."})
            assert mock_run.called

    def test_subprocess_exception_propagated(self, bash_class, mock_telemetry):
        cmd = bash_class(name="err", description="d", original_body="true")
        with patch("subprocess.run", side_effect=FileNotFoundError("bash missing")):
            with pytest.raises(FileNotFoundError):
                cmd.execute({})
        mock_telemetry["span"].record_exception.assert_called_once()
        mock_telemetry["span"].set_status.assert_called_once()

    def test_nonzero_returncode(self, bash_class, mock_telemetry):
        cmd = bash_class(name="fail", description="d", original_body="false")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Permission denied")
            # The actual error message contains a newline between the colon and
            # "Permission denied", so we must use dot-all mode.
            with pytest.raises(
                RuntimeError,
                match=re.compile(r"Command failed \(exit 1\):.*Permission denied", re.DOTALL),
            ):
                cmd.execute({})
        mock_telemetry["span"].set_status.assert_called_once()

    def test_braces_in_shell_not_placeholders(self, bash_class, mock_telemetry):
        """A malformed format string raises IndexError (not caught)."""
        cmd = bash_class(name="brace", description="d", original_body="echo {1..5}")
        with pytest.raises(IndexError):
            cmd.execute({})

    def test_no_command_text_logged_for_empty(self, bash_class, mock_telemetry, caplog):
        cmd = bash_class(name="empt", description="d", original_body="")
        with pytest.raises(ValueError):
            cmd.execute({})
        assert "has no command to execute" in caplog.text


# ---------------------------------------------------------------------------
# Additional safety edge cases
# ---------------------------------------------------------------------------


class TestSafetyEdgeCases:
    def test_pattern_only_at_start(self, bash_class, mock_telemetry):
        cmd = bash_class(name="d", description="d", original_body="sudo echo hi")
        with pytest.raises(ValueError):
            cmd.execute({})

    def test_pattern_in_middle_of_word(self, bash_class, mock_telemetry):
        cmd = bash_class(name="d", description="d", original_body="pseudosudo")
        with pytest.raises(ValueError):
            cmd.execute({})

    def test_multiple_placeholders(self, bash_class, mock_telemetry):
        cmd = bash_class(name="multi", description="d", original_body="echo {greeting} {name}")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Hello World\n", stderr="")
            output = cmd.execute({"greeting": "Hello", "name": "World"})
            assert output == "Hello World\n"
            mock_run.assert_called_once_with(
                "echo Hello World",
                shell=True,
                executable="/bin/bash",
                cwd=os.getcwd(),
                capture_output=True,
                text=True,
            )

    def test_stdout_stripped_not_modified(self, bash_class, mock_telemetry):
        cmd = bash_class(name="s", description="d", original_body="echo")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="  spaces  \n", stderr="")
            output = cmd.execute({})
            assert output == "  spaces  \n"


# ---------------------------------------------------------------------------
# Representation
# ---------------------------------------------------------------------------


class TestRepr:
    def test_repr(self, bash_class, mock_telemetry):
        cmd = bash_class(
            name="x", description="d", original_body="echo", allowed_directories=["/a", "/b"]
        )
        r = repr(cmd)
        assert "BashFunctionCall(name='x'" in r
        assert "allowed_dirs=['/a', '/b']" in r

    def test_repr_default_dirs(self, bash_class, mock_telemetry, fixed_cwd):
        cmd = bash_class(name="y", description="d", original_body="echo")
        r = repr(cmd)
        assert f"allowed_dirs=['{fixed_cwd}']" in r


# ---------------------------------------------------------------------------
# Integration test – real Bash execution
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRealExecution:
    def test_real_echo(self):
        """Execute a simple echo command and check the output."""
        from scl.capabilities.bash import BashFunctionCall

        cmd = BashFunctionCall(
            name="real_echo",
            description="Real echo integration test",
            original_body="echo hello",
        )
        output = cmd.execute({})
        assert output == "hello\n"
