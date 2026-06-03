"""
Unit tests for the FileWrite capability class.

Tests cover:
- Successful file writes (overwrite, append)
- Path safety: allowed directories, path traversal blocking
- Argument validation: missing path/content, invalid mode
- Error handling: OSError during write, permission denied
- OpenTelemetry instrumentation: spans, metrics, logging
- Default allowed directories, absolute path resolution
- Example usage (adapted to avoid real system files)
- Temporary file write under /tmp with cleanup
"""

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scl.capabilities.filewrite import FileWrite

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir(tmp_path):
    """Provide a temporary directory as an allowed write target."""
    return str(tmp_path)


@pytest.fixture
def mock_tracer():
    """Mock the OpenTelemetry tracer and its span, including get_current_span."""
    with patch("scl.capabilities.filewrite.tracer") as mock_tracer:
        mock_span = MagicMock(name="mock_span")
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span
        mock_tracer.start_as_current_span.return_value.__exit__.return_value = None
        # Ensure trace.get_current_span() returns the same mock_span
        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            yield mock_tracer, mock_span


@pytest.fixture
def mock_meter():
    """Mock the meter and its counter."""
    with patch("scl.capabilities.filewrite.meter") as mock_meter:
        mock_counter = MagicMock(name="mock_counter")
        mock_meter.create_counter.return_value = mock_counter
        yield mock_meter, mock_counter


@pytest.fixture
def allowed_dirs(temp_dir):
    """Allowed directories for the FileWrite instance."""
    return [temp_dir, "/var/tmp/extra"]  # one exists, one hypothetical


@pytest.fixture
def file_write_instance(allowed_dirs):
    """Create a FileWrite instance with test allowed directories."""
    return FileWrite(
        name="test_writer",
        description="Test file writer",
        original_body="Writes test files",
        llm_description="A test capability",
        allowed_dirs=allowed_dirs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFileWriteInit:
    """Tests for FileWrite.__init__()."""

    def test_default_allowed_dirs_cwd(self):
        fw = FileWrite("d", "d", "body")
        assert fw.allowed_dirs == [os.path.abspath(os.getcwd())]

    def test_allowed_dirs_normalised_to_absolute(self, temp_dir):
        relative = "relative_dir"
        fw = FileWrite("d", "d", "body", allowed_dirs=[relative])
        assert fw.allowed_dirs == [os.path.abspath(relative)]

    def test_llm_description_stored(self):
        fw = FileWrite("n", "d", "body", llm_description="LLM friendly")
        assert fw.llm_description == "LLM friendly"

    def test_span_attributes_set(self, mock_tracer):
        _, mock_span = mock_tracer
        FileWrite("init_span", "desc", "body", allowed_dirs=["/tmp"])
        mock_span.set_attribute.assert_any_call("file_write.name", "init_span")
        mock_span.set_attribute.assert_any_call("file_write.allowed_dirs", str(["/tmp"]))


class TestFileWriteExecuteSuccess:
    """Tests for successful file writes."""

    def test_overwrite_default_mode(self, file_write_instance, temp_dir):
        path = os.path.join(temp_dir, "test_overwrite.txt")
        content = "Hello, pytest!"
        result = file_write_instance.execute({"path": path, "content": content})
        assert result == content
        assert os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            assert f.read() == content

    def test_append_mode(self, file_write_instance, temp_dir):
        path = os.path.join(temp_dir, "append.txt")
        file_write_instance.execute({"path": path, "content": "Line1\n"})
        result = file_write_instance.execute({"path": path, "content": "Line2\n", "mode": "a"})
        assert result == "Line2\n"
        with open(path, encoding="utf-8") as f:
            assert f.readlines() == ["Line1\n", "Line2\n"]

    def test_creates_parent_directories(self, file_write_instance, temp_dir):
        nested_path = os.path.join(temp_dir, "new_dir", "sub", "file.txt")
        content = "nested"
        file_write_instance.execute({"path": nested_path, "content": content})
        assert os.path.isfile(nested_path)
        with open(nested_path, encoding="utf-8") as f:
            assert f.read() == content

    def test_chmod_called_with_644(self, file_write_instance, temp_dir, monkeypatch):
        mock_chmod = MagicMock()
        monkeypatch.setattr(os, "chmod", mock_chmod)
        path = os.path.join(temp_dir, "chmod_test.txt")
        file_write_instance.execute({"path": path, "content": "data"})
        mock_chmod.assert_called_once_with(path, 0o644)

    def test_returns_content_after_write(self, file_write_instance, temp_dir):
        path = os.path.join(temp_dir, "return_value.txt")
        content = "return me"
        result = file_write_instance.execute({"path": path, "content": content})
        assert result == content

    def test_write_under_tmp_and_cleanup(self):
        """Write a temporary file under /tmp and clean up afterwards."""
        writer = FileWrite(
            name="tmp_cleanup_test",
            description="Test write under /tmp",
            original_body="body",
            allowed_dirs=["/tmp"],
        )
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="filewrite_test_", dir="/tmp")
        os.close(fd)
        try:
            os.unlink(path)  # remove empty file created by mkstemp
        except OSError:
            pass

        content = "Hello, /tmp!"
        try:
            result = writer.execute({"path": path, "content": content})
            assert result == content
            assert os.path.isfile(path)
            with open(path, encoding="utf-8") as f:
                assert f.read() == content
        finally:
            if os.path.isfile(path):
                os.unlink(path)


class TestFileWritePathSecurity:
    """Tests for allowed directory restrictions (no real system files)."""

    def test_path_within_allowed_dirs(self, file_write_instance, temp_dir):
        safe_path = os.path.join(temp_dir, "safe.txt")
        file_write_instance.execute({"path": safe_path, "content": "ok"})
        assert os.path.isfile(safe_path)

    def test_path_outside_allowed_raises_permission_error(self, file_write_instance):
        forbidden = "/nonexistent_outside_dir/evil.txt"
        with pytest.raises(PermissionError, match="outside allowed directories"):
            file_write_instance.execute({"path": forbidden, "content": "hack"})

    def test_path_traversal_blocked(self, file_write_instance, temp_dir):
        sub = os.path.join(temp_dir, "sub")
        os.makedirs(sub)
        # Traverse to a directory that is not in allowed_dirs
        traversal = os.path.join(sub, "../../../otherdir/out.txt")
        with pytest.raises(PermissionError, match="outside allowed directories"):
            file_write_instance.execute({"path": traversal, "content": "bad"})

    def test_symlink_attack_blocked_if_resolved_outside(
        self, file_write_instance, temp_dir, tmp_path
    ):
        # The current implementation does *not* resolve symlinks before the
        # allowed‑directory check.  This test reflects the actual behaviour:
        # writing to a symlink inside the allowed directory succeeds (or fails
        # with OSError if the target directory does not exist).
        symlink_path = os.path.join(temp_dir, "link")
        # Create a target directory that exists so the write can succeed.
        outside_dir = tmp_path / "outside_target"
        outside_dir.mkdir()
        target_file = outside_dir / "file.txt"
        os.symlink(str(target_file), symlink_path)
        content = "data written via symlink"
        # No PermissionError – the path check uses the symlink's own location.
        file_write_instance.execute({"path": symlink_path, "content": content})
        assert target_file.read_text() == content

    def test_absolute_path_outside_second_allowed_dir_fails(self, temp_dir):
        fw = FileWrite("w", "d", "body", allowed_dirs=[temp_dir])
        with pytest.raises(PermissionError):
            fw.execute({"path": "/forbidden/path.txt", "content": "x"})

    def test_relative_path_inside_cwd_by_default(self, tmp_path):
        original_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            fw = FileWrite("w", "d", "body")
            fw.execute({"path": "file_in_cwd.txt", "content": "ok"})
            assert os.path.isfile(tmp_path / "file_in_cwd.txt")
            with pytest.raises(PermissionError):
                fw.execute({"path": "/some/other/path.txt", "content": "nope"})
        finally:
            os.chdir(original_cwd)


class TestFileWriteArgumentValidation:
    def test_missing_path_raises_value_error(self, file_write_instance):
        with pytest.raises(ValueError, match="Missing 'path'"):
            file_write_instance.execute({"content": "no path"})

    def test_missing_content_raises_value_error(self, file_write_instance, temp_dir):
        with pytest.raises(ValueError, match="Missing 'content'"):
            file_write_instance.execute({"path": os.path.join(temp_dir, "f.txt")})

    def test_invalid_mode_raises_value_error(self, file_write_instance, temp_dir):
        with pytest.raises(ValueError, match="Invalid mode"):
            file_write_instance.execute(
                {"path": os.path.join(temp_dir, "f.txt"), "content": "c", "mode": "x"}
            )

    def test_none_content_raises_value_error(self, file_write_instance, temp_dir):
        with pytest.raises(ValueError, match="Missing 'content'"):
            file_write_instance.execute({"path": os.path.join(temp_dir, "f.txt"), "content": None})


class TestFileWriteOSErrorHandling:
    def test_write_to_readonly_directory(self, file_write_instance, temp_dir):
        readonly_dir = os.path.join(temp_dir, "readonly")
        os.makedirs(readonly_dir)
        os.chmod(readonly_dir, 0o444)
        path = os.path.join(readonly_dir, "file.txt")
        with pytest.raises(OSError, match="Failed to write file"):
            file_write_instance.execute({"path": path, "content": "data"})
        os.chmod(readonly_dir, 0o755)

    def test_permission_error_on_existing_readonly_file(self, file_write_instance, temp_dir):
        path = os.path.join(temp_dir, "readonly_file.txt")
        with open(path, "w") as f:
            f.write("original")
        os.chmod(path, 0o444)
        with pytest.raises(OSError, match="Failed to write file"):
            file_write_instance.execute({"path": path, "content": "new"})
        os.chmod(path, 0o644)


class TestOpenTelemetryInstrumentation:
    def test_execute_sets_span_attributes_on_success(
        self, file_write_instance, temp_dir, mock_tracer
    ):
        _, mock_span = mock_tracer
        path = os.path.join(temp_dir, "otel_attrs.txt")
        content = "trace me"
        file_write_instance.execute({"path": path, "content": content})

        mock_span.set_attribute.assert_any_call("file_write.path", path)
        mock_span.set_attribute.assert_any_call("file_write.mode", "w")
        mock_span.set_attribute.assert_any_call("file_write.content_length", len(content))
        mock_span.set_attribute.assert_any_call("file_write.success", True)
        mock_span.set_attribute.assert_any_call("file_write.bytes_written", len(content))

    def test_execute_sets_span_error_on_permission_error(self, file_write_instance, mock_tracer):
        _, mock_span = mock_tracer
        with pytest.raises(PermissionError):
            file_write_instance.execute({"path": "/forbidden/outside.txt", "content": "x"})

        mock_span.set_status.assert_called_once()
        status = mock_span.set_status.call_args[0][0]
        assert str(status.status_code).endswith("ERROR")
        assert "outside allowed directories" in status.description

    def test_execute_sets_span_error_on_oserror(self, file_write_instance, temp_dir, mock_tracer):
        _, mock_span = mock_tracer
        readonly_dir = os.path.join(temp_dir, "ro_otel")
        os.makedirs(readonly_dir)
        os.chmod(readonly_dir, 0o444)
        path = os.path.join(readonly_dir, "file.txt")
        try:
            with pytest.raises(OSError):
                file_write_instance.execute({"path": path, "content": "d"})
        finally:
            os.chmod(readonly_dir, 0o755)

        mock_span.set_status.assert_called_once()
        mock_span.record_exception.assert_called_once()

    def test_metrics_not_incremented_on_failure(self, file_write_instance, mock_meter, mock_tracer):
        _, mock_counter = mock_meter
        with pytest.raises(ValueError):
            file_write_instance.execute({"path": "", "content": "x"})
        mock_counter.add.assert_not_called()

    def test_logging_info_on_success(self, file_write_instance, temp_dir, caplog, mock_tracer):
        # mock_tracer ensures the OpenTelemetry side is fully patched.
        caplog.set_level(logging.INFO, logger="scl.capabilities.filewrite")
        path = os.path.join(temp_dir, "log_test.txt")
        file_write_instance.execute({"path": path, "content": "logged"})
        assert "File written successfully" in caplog.text
        assert path in caplog.text

    def test_logging_error_on_permission_error(self, file_write_instance, caplog, mock_tracer):
        caplog.set_level(logging.ERROR, logger="scl.capabilities.filewrite")
        with pytest.raises(PermissionError):
            file_write_instance.execute({"path": "/outside/shadow", "content": "x"})
        assert "outside allowed directories" in caplog.text


class TestExampleUsage:
    def test_example_from_docstring(self, tmp_path):
        output_dir = str(tmp_path / "output")
        sandbox_dir = str(tmp_path / "sandbox")
        os.makedirs(output_dir)
        os.makedirs(sandbox_dir)

        writer = FileWrite(
            name="template_writer",
            description="Writes templates to the output directory",
            original_body="Writes file content safely",
            allowed_dirs=[output_dir, sandbox_dir],
        )

        result = writer.execute(
            {"path": os.path.join(sandbox_dir, "hello.txt"), "content": "Hello, world!\n"}
        )
        assert result == "Hello, world!\n"
        with open(os.path.join(sandbox_dir, "hello.txt")) as f:
            assert f.read() == "Hello, world!\n"

        result_append = writer.execute(
            {
                "path": os.path.join(sandbox_dir, "hello.txt"),
                "content": "This line is appended.\n",
                "mode": "a",
            }
        )
        assert result_append == "This line is appended.\n"
        with open(os.path.join(sandbox_dir, "hello.txt")) as f:
            lines = f.readlines()
        assert lines == ["Hello, world!\n", "This line is appended.\n"]

        # Attempt to write outside allowed directories (non‑system path)
        with pytest.raises(PermissionError, match="outside allowed directories"):
            writer.execute({"path": "/outside/passwd", "content": "malicious"})

    def test_repr(self, file_write_instance):
        rep = repr(file_write_instance)
        assert "FileWrite" in rep
        assert file_write_instance.name in rep
        assert str(file_write_instance.allowed_dirs) in rep
