from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: _control_writer.py CONTROL_FILE", file=sys.stderr)
        return 2

    target = Path(sys.argv[1])
    content = sys.stdin.buffer.read()
    # Do not shrink the file when a small command follows a large trajectory
    # payload.  Wine can keep the previous EOF cached on NTFS3 and then expose
    # stale bytes past the new JSON document indefinitely.  Keeping the
    # existing length and replacing the unused tail with JSON whitespace makes
    # the final document valid from both the Linux and Wine views.  It also
    # avoids the transient empty document created by O_TRUNC.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        previous_size = os.fstat(descriptor).st_size
        padded = content.ljust(max(previous_size, len(content)), b" ")
        os.lseek(descriptor, 0, os.SEEK_SET)
        view = memoryview(padded)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError(f"short write to {target}")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
