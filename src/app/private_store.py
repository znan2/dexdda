"""Private atomic JSON and process-wide advisory locks for local operations."""

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from app.registry.loader import write_atomic


class StoreError(Exception):
    pass


class PrivateStore:
    def __init__(self, path: Path):
        self.path = path

    def read(self, default):
        if not self.path.exists():
            return default
        try:
            if self.path.is_symlink():
                raise StoreError("unsafe_store")
            os.chmod(self.path, 0o600)
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            raise StoreError("invalid_store") from None

    def write(self, value):
        if self.path.is_symlink():
            raise StoreError("unsafe_store")
        write_atomic(
            self.path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        os.chmod(self.path, 0o600)
        # Persist the replacement directory entry, not only the temporary file.
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @contextmanager
    def lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        path = self.path.with_suffix(self.path.suffix + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreError("operation_busy") from None
            yield
        finally:
            os.close(fd)
