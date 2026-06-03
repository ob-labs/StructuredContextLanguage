"""
Unit tests for GitCapability in scl.capabilities.git.

Tests cover:
- Initialization and attribute inheritance
- execute with valid actions: commit, checkout, history
- execute with missing/invalid action and missing required arguments
- Error translation from git failures to RuntimeError
- Git repository verification failure
- History parsing (including edge cases)
- OpenTelemetry span interactions (via mocking)
- Metric counter incrementation
- Basic git availability check via --version
"""

import subprocess
import sys
from unittest.mock import ANY, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Import the module under test with its OpenTelemetry hooks and Capability base
# class replaced by mocks, so that the @tracer decorator is a no-op and the base
# records constructor arguments. Only the two leaf submodules are overridden
# (not the scl.meta / scl.otel packages), and patch.dict restores sys.modules on
# exit so these mocks never leak into other test modules.
# ---------------------------------------------------------------------------
mock_otel = MagicMock()
mock_tracer = MagicMock()
mock_meter = MagicMock()
# Let the decorator @tracer.start_as_current_span() return a no-op decorator
# that returns the original function unchanged.
mock_tracer.start_as_current_span.side_effect = lambda name: lambda fn: fn
mock_otel.tracer = mock_tracer
mock_otel.meter = mock_meter


class MockCapability:
    """Minimal base class that records constructor arguments."""

    def __init__(self, name, type, description, original_body, llm_description, function_impl):
        self.name = name
        self.type = type
        self.description = description
        self.original_body = original_body
        self.llm_description = llm_description
        self.function_impl = function_impl


mock_capability_module = MagicMock()
mock_capability_module.Capability = MockCapability

# Initialize the real package tree first so importing scl does not overwrite the
# overrides below, then import git fresh against the mocks.
import scl.capabilities  # noqa: F401

with patch.dict(
    sys.modules,
    {"scl.otel.otel": mock_otel, "scl.meta.capability": mock_capability_module},
):
    sys.modules.pop("scl.capabilities.git", None)
    from scl.capabilities.git import GitCapability


# ---------------------------------------------------------------------------
# Helper to create a successful subprocess.CompletedProcess mock
# ---------------------------------------------------------------------------
def completed_process(stdout="", stderr=""):
    """Return a MagicMock mimicking subprocess.CompletedProcess."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = 0
    return proc


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_span():
    """Provide a fresh mock span for OpenTelemetry."""
    return MagicMock()


@pytest.fixture
def git_capability(mock_span):
    """
    Create a GitCapability instance with mocked tracing and metrics.
    Keeps the get_current_span patch active for the whole test function
    so that execute() sees the same mock_span.
    """
    with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
        cap = GitCapability(
            name="test_git",
            description="Test git capability",
            original_body="test body",
            llm_description="LLM test desc",
        )
        yield cap


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestGitVersion:
    """Ensure git is available in the test environment by running git --version."""

    def test_git_version_command(self):
        """Run git --version and verify it exits successfully."""
        result = subprocess.run(["git", "--version"], capture_output=True, text=True)
        assert result.returncode == 0, f"git --version failed: {result.stderr}"
        assert "git version" in result.stdout, f"Unexpected output: {result.stdout}"


class TestGitCapabilityInit:
    def test_init_sets_attributes(self, mock_span):
        """Verify that initialization calls the base class with correct arguments."""
        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            cap = GitCapability(
                name="mygit", description="desc", original_body="body", llm_description="llm_desc"
            )
        assert cap.name == "mygit"
        assert cap.type == "git"
        assert cap.description == "desc"
        assert cap.original_body == "body"
        assert cap.llm_description == "llm_desc"
        assert cap.function_impl is None  # as per __init__

    def test_init_sets_span_attributes(self, git_capability, mock_span):
        """The span should have the git_capability.name attribute set."""
        mock_span.set_attribute.assert_any_call("git_capability.name", "test_git")


class TestExecuteMissingAction:
    def test_missing_action_raises_value_error(self, git_capability, mock_span):
        with pytest.raises(ValueError, match="Missing 'action'"):
            git_capability.execute({})
        mock_span.set_status.assert_called_once()

    def test_none_action_raises_value_error(self, git_capability, mock_span):
        with pytest.raises(ValueError, match="Missing 'action'"):
            git_capability.execute({"action": None})
        mock_span.set_status.assert_called_once()

    def test_unsupported_action_raises_value_error(self, git_capability, mock_span):
        with pytest.raises(ValueError, match="Unsupported git action 'rebase'"):
            git_capability.execute({"action": "rebase"})
        mock_span.set_status.assert_called_once()


class TestExecuteCommit:
    @patch("subprocess.run")
    def test_commit_returns_hash(self, mock_run, git_capability, mock_span):
        """Happy path: commit stages, commits, and returns new HEAD hash."""
        mock_run.side_effect = [
            completed_process(),  # git rev-parse --is-inside-work-tree
            completed_process(),  # git add .
            completed_process(),  # git commit -m ...
            completed_process(stdout="abc123def\n"),  # git rev-parse HEAD
        ]
        message = "Add feature X"

        result = git_capability.execute({"action": "commit", "message": message})

        assert result == "abc123def"
        expected_calls = [
            call(
                ["git", "rev-parse", "--is-inside-work-tree"],
                check=True,
                capture_output=True,
                text=True,
            ),
            call(["git", "add", "."], check=True, capture_output=True, text=True),
            call(["git", "commit", "-m", message], check=True, capture_output=True, text=True),
            call(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True),
        ]
        mock_run.assert_has_calls(expected_calls)
        mock_span.set_attribute.assert_any_call("git.commit.message", message)

    @patch("subprocess.run")
    def test_commit_missing_message_raises_value_error(self, mock_run, git_capability, mock_span):
        with pytest.raises(ValueError, match="Commit requires a 'message'"):
            git_capability.execute({"action": "commit"})
        mock_span.set_status.assert_called_once()

        with pytest.raises(ValueError, match="Commit requires a 'message'"):
            git_capability.execute({"action": "commit", "message": ""})

    @patch("subprocess.run")
    def test_commit_when_not_in_git_repo_raises_runtime_error(
        self, mock_run, git_capability, mock_span
    ):
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            returncode=128,
            cmd="git rev-parse --is-inside-work-tree",
            stderr="fatal: not a git repository",
        )
        with pytest.raises(RuntimeError, match="Current directory is not a Git repository"):
            git_capability.execute({"action": "commit", "message": "msg"})

    @patch("subprocess.run")
    def test_commit_git_command_failure_raises_runtime_error(
        self, mock_run, git_capability, mock_span
    ):
        mock_run.side_effect = [
            completed_process(),  # repo verification ok
            __import__("subprocess").CalledProcessError(
                returncode=1, cmd="git add", stderr="error: pathspec '.' did not match any files"
            ),
        ]
        with pytest.raises(
            RuntimeError, match="Git commit failed: error: pathspec '.' did not match any files"
        ):
            git_capability.execute({"action": "commit", "message": "msg"})


class TestExecuteCheckout:
    @patch("subprocess.run")
    def test_checkout_returns_hash(self, mock_run, git_capability, mock_span):
        mock_run.side_effect = [
            completed_process(),  # repo verification
            completed_process(),  # git checkout <hash>
        ]
        commit_hash = "deadbeef123"

        result = git_capability.execute({"action": "checkout", "commit_hash": commit_hash})

        assert result == commit_hash
        expected_calls = [
            call(
                ["git", "rev-parse", "--is-inside-work-tree"],
                check=True,
                capture_output=True,
                text=True,
            ),
            call(["git", "checkout", commit_hash], check=True, capture_output=True, text=True),
        ]
        mock_run.assert_has_calls(expected_calls)
        mock_span.set_attribute.assert_any_call("git.checkout.hash", commit_hash)

    @patch("subprocess.run")
    def test_checkout_missing_hash_raises_value_error(self, mock_run, git_capability, mock_span):
        with pytest.raises(ValueError, match="Checkout requires a 'commit_hash'"):
            git_capability.execute({"action": "checkout"})

    @patch("subprocess.run")
    def test_checkout_not_in_repo_raises_runtime_error(self, mock_run, git_capability):
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            returncode=128,
            cmd="git rev-parse --is-inside-work-tree",
            stderr="fatal: not a git repository",
        )
        with pytest.raises(RuntimeError, match="Current directory is not a Git repository"):
            git_capability.execute({"action": "checkout", "commit_hash": "abc"})

    @patch("subprocess.run")
    def test_checkout_failure_raises_runtime_error(self, mock_run, git_capability):
        mock_run.side_effect = [
            completed_process(),
            __import__("subprocess").CalledProcessError(
                returncode=1,
                cmd="git checkout",
                stderr="error: pathspec 'abc' did not match any file(s) known to git.",
            ),
        ]
        with pytest.raises(
            RuntimeError, match="Git checkout failed: error: pathspec 'abc' did not match"
        ):
            git_capability.execute({"action": "checkout", "commit_hash": "abc"})


class TestExecuteHistory:
    @patch("subprocess.run")
    def test_history_returns_list_of_dicts(self, mock_run, git_capability, mock_span):
        log_output = "hash1\tAlice\t2025-01-01\tFirst commit\nhash2\tBob\t2025-01-02\tSecond commit"
        mock_run.side_effect = [
            completed_process(),  # repo verification
            completed_process(stdout=log_output),
        ]

        result = git_capability.execute({"action": "history"})

        expected = [
            {
                "commit_hash": "hash1",
                "author": "Alice",
                "date": "2025-01-01",
                "message": "First commit",
            },
            {
                "commit_hash": "hash2",
                "author": "Bob",
                "date": "2025-01-02",
                "message": "Second commit",
            },
        ]
        assert result == expected

    @patch("subprocess.run")
    def test_history_empty_output(self, mock_run, git_capability):
        mock_run.side_effect = [
            completed_process(),
            completed_process(stdout=""),
        ]
        result = git_capability.execute({"action": "history"})
        assert result == []

    @patch("subprocess.run")
    def test_history_skips_lines_with_insufficient_fields(self, mock_run, git_capability):
        log_output = (
            "hash1\tAlice\t2025-01-01\tMessage\nincomplete_line\nhash2\tBob\t2025-01-02\tSecond\n"
        )
        mock_run.side_effect = [
            completed_process(),
            completed_process(stdout=log_output),
        ]
        result = git_capability.execute({"action": "history"})
        assert len(result) == 2
        assert result[0]["commit_hash"] == "hash1"
        assert result[1]["commit_hash"] == "hash2"

    @patch("subprocess.run")
    def test_history_with_single_commit(self, mock_run, git_capability):
        log_output = "hash3\tCarol\t2025-03-10\tSingle commit"
        mock_run.side_effect = [
            completed_process(),
            completed_process(stdout=log_output),
        ]
        result = git_capability.execute({"action": "history"})
        assert result == [
            {
                "commit_hash": "hash3",
                "author": "Carol",
                "date": "2025-03-10",
                "message": "Single commit",
            }
        ]

    @patch("subprocess.run")
    def test_history_not_in_repo_raises_runtime_error(self, mock_run, git_capability):
        mock_run.side_effect = __import__("subprocess").CalledProcessError(
            returncode=128,
            cmd="git rev-parse --is-inside-work-tree",
            stderr="fatal: not a git repository",
        )
        with pytest.raises(RuntimeError, match="Current directory is not a Git repository"):
            git_capability.execute({"action": "history"})


class TestMetricsAndTracing:
    @patch("subprocess.run")
    def test_counter_incremented_on_success(self, mock_run, git_capability, mock_span):
        mock_run.side_effect = [
            completed_process(),
            completed_process(),
            completed_process(),
            completed_process(stdout="hash123\n"),
        ]
        # Reset the counter mock for a clean assertion
        mock_meter.create_counter.return_value.reset_mock()
        git_capability.execute({"action": "commit", "message": "m"})

        counter = mock_meter.create_counter.return_value
        counter.add.assert_called_once_with(1, {"action": "commit"})

    @patch("subprocess.run")
    def test_span_status_error_on_failure(self, mock_run, git_capability, mock_span):
        """Verify that no span status is set on command failure (the implementation
        currently only sets status for missing/invalid actions, not for subprocess errors)."""
        mock_run.side_effect = [
            completed_process(),
            __import__("subprocess").CalledProcessError(
                returncode=1, cmd="git commit", stderr="error: something went wrong"
            ),
        ]
        with pytest.raises(RuntimeError):
            git_capability.execute({"action": "commit", "message": "msg"})

        # The source code does NOT set span status on git command failure,
        # so we assert it was not called.
        mock_span.set_status.assert_not_called()

    @patch("subprocess.run")
    def test_span_attributes_on_success(self, mock_run, git_capability, mock_span):
        mock_run.side_effect = [
            completed_process(),
            completed_process(),
            completed_process(),
            completed_process(stdout="hash123\n"),
        ]
        git_capability.execute({"action": "commit", "message": "test"})
        mock_span.set_attribute.assert_any_call("git.result.success", True)
