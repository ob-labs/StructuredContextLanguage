"""
Unit tests for scl.capabilities.fileread.FileRead
(./scl/capabilities/fileread.py → ./scl/test/capabilities/test_fileread.py)

Tests cover:
- initialization with allowed directories
- missing path argument
- path resolution (absolute, relative, out-of-bounds)
- non-regular file handling
- binary extension rejection
- successful UTF-8 read
- unicode decode error handling
- OS error propagation
- OpenTelemetry instrumentation (spans, attributes, metrics)
- Capability base class integration
- reading the module's own source file
"""

import os
import sys
from unittest.mock import MagicMock, patch, ANY

import pytest


# ---------------------------------------------------------------------------
# Fixture to mock OTEL dependencies *before* FileRead is imported.
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_dependencies():
    """Set up mocked tracer, meter, trace and force‑reimport FileRead so that all
    OpenTelemetry integrations are under our control but the real Capability
    base class and business logic are left intact."""

    mock_tracer = MagicMock()
    mock_span = MagicMock()

    # ------------------------------------------------------------------
    # The core of the fix: make @tracer.start_as_current_span(...) act as a
    # transparent decorator that still enters a span context (our mock_span)
    # and calls the original function.
    # ------------------------------------------------------------------
    def start_as_current_span_side_effect(name):
        """Return a decorator that wraps the original function, entering the
        mocked span context before calling it."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                # Use the already‑configured return_value as context manager.
                # This avoids calling mock_tracer.start_as_current_span inside
                # the wrapper, which would trigger infinite recursion.
                ctx = mock_tracer.start_as_current_span.return_value
                with ctx:
                    return func(*args, **kwargs)
            return wrapper
        return decorator

    mock_tracer.start_as_current_span.side_effect = start_as_current_span_side_effect
    mock_tracer.start_as_current_span.return_value.__enter__.return_value = mock_span

    # Metric mock
    mock_meter = MagicMock()
    mock_counter = MagicMock()
    mock_meter.create_counter.return_value = mock_counter

    # Trace mock – used for get_current_span() and Status creation
    mock_trace = MagicMock()
    mock_trace.get_current_span.return_value = mock_span
    mock_trace.StatusCode.ERROR = 2
    mock_trace.Status.return_value = MagicMock(status_code=2)

    # Force a fresh import of the module under test
    sys.modules.pop('scl.capabilities.fileread', None)

    # Patch the *source* modules used by fileread.py
    with patch('scl.capabilities.fileread.tracer', mock_tracer), \
         patch('scl.capabilities.fileread.meter', mock_meter), \
         patch('scl.capabilities.fileread.trace', mock_trace):
        from scl.capabilities.fileread import FileRead
        yield FileRead, mock_tracer, mock_span, mock_meter, mock_counter


# ---------------------------------------------------------------------------
# Helper fixtures for temporary files / directories
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_allowed_dir(tmp_path):
    """Create a temporary directory that will serve as an allowed path."""
    return str(tmp_path)


@pytest.fixture
def text_file(tmp_allowed_dir):
    """A temporary UTF-8 text file inside the allowed directory."""
    file_path = os.path.join(tmp_allowed_dir, "test.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Hello World")
    return file_path


@pytest.fixture
def binary_file(tmp_allowed_dir):
    """A temporary file with a binary extension (.png)."""
    file_path = os.path.join(tmp_allowed_dir, "image.png")
    with open(file_path, "w") as f:
        f.write("fake image")
    return file_path


# ---------------------------------------------------------------------------
# Initialization tests
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_default_allowed_directories_is_cwd(self, mock_dependencies):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b")
        expected = [os.path.abspath(os.getcwd())]
        assert reader._allowed_dirs == expected

    def test_custom_absolute_directories_are_normalized(self, mock_dependencies):
        FileRead, *_ = mock_dependencies
        dirs = ["/tmp", "/var/run"]
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=dirs)
        expected = [os.path.abspath(d) for d in dirs]
        assert reader._allowed_dirs == expected

    def test_relative_allowed_directories_become_absolute(self, mock_dependencies, tmp_path):
        FileRead, *_ = mock_dependencies
        rel_dir = "rel"
        with patch.object(os, 'getcwd', return_value=str(tmp_path)):
            reader = FileRead(name="r", description="d", original_body="b",
                              allowed_directories=[rel_dir])
            assert reader._allowed_dirs[0] == os.path.join(str(tmp_path), rel_dir)

    def test_super_init_called_with_correct_args(self, mock_dependencies):
        """Verify that super().__init__ is called correctly by checking
        the instance attributes set by the base Capability class."""
        FileRead, *_ = mock_dependencies
        reader = FileRead(
            name="test_name",
            description="test_desc",
            original_body="body",
            llm_description="llm"
        )
        assert reader.name == "test_name"
        # base class may store type in 'type' or '_type'
        assert getattr(reader, 'type', getattr(reader, '_type', None)) == "file_read"
        assert reader.original_body == "body"
        assert reader.llm_description == "llm"

    def test_init_span_attributes(self, mock_dependencies):
        FileRead, mock_tracer, mock_span, *_ = mock_dependencies
        reader = FileRead(name="reader1", description="d", original_body="o",
                          allowed_directories=["/tmp"])
        mock_span.set_attribute.assert_any_call("file_read.name", "reader1")
        mock_span.set_attribute.assert_any_call("file_read.allowed_directories",
                                                str(reader._allowed_dirs))


# ---------------------------------------------------------------------------
# execute tests – error cases
# ---------------------------------------------------------------------------

class TestExecuteErrors:
    def test_missing_path_argument(self, mock_dependencies):
        FileRead, _, mock_span, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b")
        with pytest.raises(ValueError, match="Missing required argument 'path'"):
            reader.execute({})
        mock_span.set_status.assert_called()
        status_call = mock_span.set_status.call_args_list[-1]
        assert status_call[0][0].status_code == 2  # ERROR

    def test_absolute_path_outside_allowed_directories(self, mock_dependencies, tmp_allowed_dir):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[tmp_allowed_dir])
        bad_path = "/outside/file.txt"
        with pytest.raises(ValueError, match="is not within allowed directories"):
            reader.execute({"path": bad_path})

    def test_relative_path_not_found_in_any_allowed_dir(self, mock_dependencies, tmp_allowed_dir):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[tmp_allowed_dir])
        with pytest.raises(ValueError, match="does not exist in any allowed directory"):
            reader.execute({"path": "nonexistent.txt"})

    def test_path_is_directory_not_file(self, mock_dependencies, tmp_allowed_dir):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[tmp_allowed_dir])
        subdir = os.path.join(tmp_allowed_dir, "subdir")
        os.makedirs(subdir, exist_ok=True)
        with pytest.raises(ValueError, match="Path is not a regular file"):
            reader.execute({"path": "subdir"})

    def test_binary_extension_raises(self, mock_dependencies, binary_file):
        FileRead, *_ = mock_dependencies
        allowed_dir = os.path.dirname(binary_file)
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[allowed_dir])
        with pytest.raises(ValueError, match="Binary file extension '.png' is not allowed"):
            reader.execute({"path": os.path.basename(binary_file)})

    def test_unicode_decode_error_raises_valueerror(self, mock_dependencies, tmp_allowed_dir):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[tmp_allowed_dir])
        dummy_file = os.path.join(tmp_allowed_dir, "bad.txt")
        with open(dummy_file, "wb") as f:
            f.write(b'\x80\x81')
        with pytest.raises(ValueError, match="File could not be decoded as UTF-8"):
            reader.execute({"path": os.path.basename(dummy_file)})

    def test_os_error_during_read(self, mock_dependencies, text_file):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[os.path.dirname(text_file)])
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            with pytest.raises(OSError, match="Permission denied"):
                reader.execute({"path": os.path.basename(text_file)})


# ---------------------------------------------------------------------------
# execute tests – success cases
# ---------------------------------------------------------------------------

class TestExecuteSuccess:
    def test_absolute_path_success(self, mock_dependencies, text_file):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[os.path.dirname(text_file)])
        result = reader.execute({"path": text_file})
        assert result == "Hello World"

    def test_multiple_allowed_directories_finds_second(self, mock_dependencies, tmp_path):
        FileRead, *_ = mock_dependencies
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        test_file = dir2 / "findme.txt"
        test_file.write_text("found me")
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[str(dir1), str(dir2)])
        result = reader.execute({"path": "findme.txt"})
        assert result == "found me"

    def test_execute_span_attributes(self, mock_dependencies, text_file):
        FileRead, _, mock_span, *_ = mock_dependencies
        reader = FileRead(name="span_test", description="d", original_body="b",
                          allowed_directories=[os.path.dirname(text_file)])
        mock_span.reset_mock()
        reader.execute({"path": os.path.basename(text_file)})
        mock_span.set_attribute.assert_any_call("file_read.raw_path", os.path.basename(text_file))
        mock_span.set_attribute.assert_any_call("file_read.resolved_path", text_file)

    def test_read_own_source_file(self, mock_dependencies):
        """Read the module's source code to verify it returns the expected class definition."""
        FileRead, *_ = mock_dependencies
        import inspect
        source_file = inspect.getfile(FileRead)
        source_dir = os.path.dirname(source_file)
        reader = FileRead(
            name="source_reader",
            description="Read own source",
            original_body="test",
            allowed_directories=[source_dir]
        )
        content = reader.execute({"path": os.path.basename(source_file)})
        assert "class FileRead(Capability)" in content


# ---------------------------------------------------------------------------
# Representation
# ---------------------------------------------------------------------------

def test_repr(mock_dependencies):
    FileRead, *_ = mock_dependencies
    reader = FileRead(name="r", description="d", original_body="b",
                      allowed_directories=["/tmp", "/var"])
    r = repr(reader)
    assert "FileRead(name='r'" in r
    assert "/tmp" in r
    assert "/var" in r


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_allowed_directories_list(self, mock_dependencies):
        FileRead, *_ = mock_dependencies
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[])
        with pytest.raises(ValueError, match="does not exist in any allowed directory"):
            reader.execute({"path": "anything"})

    def test_relative_path_traversal_currently_allowed(self, mock_dependencies, tmp_allowed_dir):
        """Document current behaviour: relative '..' may escape allowed directories.
        This test is kept as a reminder that additional hardening may be needed."""
        FileRead, *_ = mock_dependencies
        parent_dir = os.path.dirname(tmp_allowed_dir)
        secret = os.path.join(parent_dir, "secret.txt")
        with open(secret, "w") as f:
            f.write("traversed")
        reader = FileRead(name="r", description="d", original_body="b",
                          allowed_directories=[tmp_allowed_dir])
        result = reader.execute({"path": "../secret.txt"})
        assert result == "traversed"