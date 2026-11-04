"""Private, atomic runtime state and credential-safe error summaries."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def write_json(path, value):
    path = Path(path)
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix="." + path.name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_append(path):
    path = Path(path)
    private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "a", encoding="utf-8")


def error_info(error, stage):
    # Never stringify exceptions: subprocess exceptions embed argv credentials.
    return {"stage": stage, "error_type": type(error).__name__,
            "returncode": getattr(error, "returncode", None)}


def read_state(path, default=None, valid=None):
    """Recover missing/invalid state without logging its potentially private data."""
    path = Path(path)
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or (valid is not None and not valid(value)):
            raise ValueError("invalid state")
        path.chmod(0o600)
        return value
    except FileNotFoundError:
        return default
    except (ValueError, TypeError, UnicodeError):
        import uuid
        path.chmod(0o600)
        path.replace(path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex))
        return default


def valid_session(value):
    return all(isinstance(value.get(key), str) and value[key]
               for key in ("session_id", "token"))


@contextmanager
def file_lock(path, blocking=True):
    import fcntl
    path = Path(path)
    private_dir(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(fd, flags)
        yield
    finally:
        os.close(fd)


def lock_busy(path):
    try:
        with file_lock(path, blocking=False):
            return False
    except BlockingIOError:
        return True
