"""
Git Function Call Module

Represents a Git capability, inheriting from Capability.
Implements the abstract execute method for executing Git commands with safety checks.

Features and design goals
--------------------------
Use git to implement hash-based version control for current folder.
- Commit changes with a message.
- Checkout specific commit.
- View commit history.

----------------------------
- OpenTelemetry integrated for tracing, metrics, and structured logging.
- Logger provides info and debug levels.

Future features (not yet implemented):
- Branch management (create, switch, delete)
- Merge, rebase
- Remote operations (push, pull, fetch)
- Diff view
- Stash support
"""

import logging
import subprocess
from typing import Any

from opentelemetry import trace

from scl.meta.capability import Capability
from scl.otel.otel import meter, tracer

logger = logging.getLogger(__name__)

# Metric: number of git operations by action type
git_operation_counter = meter.create_counter(
    "git.operation", description="Number of git operations performed, tagged by action type"
)


class GitCapability(Capability):
    """
    Capability to perform Git operations within the current working directory.

    Supports commit, checkout (detached HEAD to a specific commit hash),
    and viewing commit history. All operations are executed with safe
    arguments to prevent injection.
    """

    @tracer.start_as_current_span("GitCapability.__init__")
    def __init__(
        self, name: str, description: str, original_body: str, llm_description: str | None = None
    ):
        current_span = trace.get_current_span()
        current_span.set_attribute("git_capability.name", name)

        super().__init__(
            name=name,
            type="git",
            description=description,
            original_body=original_body,
            llm_description=llm_description,
            function_impl=None,  # Git operations are built-in, no external code
        )

        logger.debug(f"GitCapability '{name}' initialized")
        logger.info(f"GitCapability '{name}' created")

    @tracer.start_as_current_span("GitCapability.execute")
    def execute(self, args_dict: dict[str, Any]) -> Any:
        """
        Execute a Git operation based on the provided arguments.

        Expected args_dict keys:
        - 'action': 'commit', 'checkout', or 'history'
        For 'commit': 'message' (str) required.
        For 'checkout': 'commit_hash' (str) required.
        For 'history': no additional arguments; returns list of commit info.

        Returns:
            - commit: the new commit hash (str)
            - checkout: the checked-out commit hash (str)
            - history: list of dicts with keys 'commit_hash', 'author', 'date', 'message'

        Raises:
            ValueError: if required arguments are missing or action is unsupported.
            RuntimeError: if git command fails or current directory is not a repo.
        """
        current_span = trace.get_current_span()
        action = args_dict.get("action")
        current_span.set_attribute("git.action", action)

        if not action:
            error_msg = "Missing 'action' in args_dict. Supported: commit, checkout, history."
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        logger.debug(f"Executing git action '{action}' with args: {args_dict}")

        if action == "commit":
            message = args_dict.get("message")
            if not message:
                error_msg = "Commit requires a 'message' in args_dict."
                logger.error(error_msg)
                current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)
            result = self._commit(message)
            current_span.set_attribute("git.commit.message", message)
        elif action == "checkout":
            commit_hash = args_dict.get("commit_hash")
            if not commit_hash:
                error_msg = "Checkout requires a 'commit_hash' in args_dict."
                logger.error(error_msg)
                current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise ValueError(error_msg)
            result = self._checkout(commit_hash)
            current_span.set_attribute("git.checkout.hash", commit_hash)
        elif action == "history":
            result = self._history()
        else:
            error_msg = f"Unsupported git action '{action}'"
            logger.error(error_msg)
            current_span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
            raise ValueError(error_msg)

        git_operation_counter.add(1, {"action": action})
        current_span.set_attribute("git.result.success", True)
        logger.info(f"Git action '{action}' completed successfully")
        return result

    def _commit(self, message: str) -> str:
        """Stage all changes and commit with the given message. Returns new commit hash."""
        logger.debug(f"Committing with message: {message}")
        try:
            self._verify_git_repo()
            # Stage all changes
            subprocess.run(["git", "add", "."], check=True, capture_output=True, text=True)
            # Commit
            subprocess.run(
                ["git", "commit", "-m", message], check=True, capture_output=True, text=True
            )
            # Retrieve the new commit hash
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
            )
            commit_hash = hash_result.stdout.strip()
            logger.info(f"Committed with hash {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            error_msg = f"Git commit failed: {e.stderr.strip() if e.stderr else str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _checkout(self, commit_hash: str) -> str:
        """Checkout a specific commit (detached HEAD). Returns the checked-out hash."""
        logger.debug(f"Checking out commit: {commit_hash}")
        try:
            self._verify_git_repo()
            subprocess.run(
                ["git", "checkout", commit_hash], check=True, capture_output=True, text=True
            )
            logger.info(f"Checked out commit {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            error_msg = f"Git checkout failed: {e.stderr.strip() if e.stderr else str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _history(self) -> list[dict[str, str]]:
        """
        Return list of commits for the current branch.

        Each entry contains:
        - commit_hash
        - author
        - date
        - message
        """
        logger.debug("Retrieving commit history")
        try:
            self._verify_git_repo()
            result = subprocess.run(
                ["git", "log", "--pretty=format:%H%x09%an%x09%ad%x09%s", "--date=short"],
                check=True,
                capture_output=True,
                text=True,
            )
            lines = result.stdout.strip().split("\n")
            history = []
            for line in lines:
                if line:
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        history.append(
                            {
                                "commit_hash": parts[0],
                                "author": parts[1],
                                "date": parts[2],
                                "message": parts[3],
                            }
                        )
            logger.info(f"Retrieved {len(history)} commits from history")
            return history
        except subprocess.CalledProcessError as e:
            error_msg = f"Git history retrieval failed: {e.stderr.strip() if e.stderr else str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _verify_git_repo(self) -> None:
        """Ensure the current working directory is inside a Git repository."""
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError("Current directory is not a Git repository") from e


"""
    Example usage:
    --------------
    from scl.capabilities.git_function_call import GitCapability

    # Assume the current directory is already a Git repository.
    git_cap = GitCapability(
        name="git_manager",
        description="Handles version control operations with Git",
        original_body="Commit, checkout, and view history"
    )

    # Commit all current changes
    new_hash = git_cap.execute({"action": "commit", "message": "Added new feature"})
    print(f"New commit hash: {new_hash}")

    # View commit history
    history = git_cap.execute({"action": "history"})
    for entry in history:
        print(entry["commit_hash"], entry["message"])

    # Checkout a specific earlier commit
    git_cap.execute({"action": "checkout", "commit_hash": "abc123"})
"""
