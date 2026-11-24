"""One MIRA desktop runtime per user: election of the primary, and forwarding.

`MIRA` and `MIRA-NOTCH` are two entry points into the same runtime. The first
launcher becomes the runtime (the *primary*); any later one sends its surface
request to it and exits. Two pieces, both in one per-user directory:

- ``runtime.lock`` decides who is primary. The primary holds an exclusive
  `flock` on it for its whole life. The kernel drops the lock when the
  process ends — normally, by a crash or by SIGKILL — so a dead runtime can
  never keep the lock, and taking it is atomic: two launchers started at the
  same instant cannot both win.
- ``runtime.sock`` is how a launcher reaches the primary: a Unix-domain
  stream socket the primary listens on (`mira.ui.instance_server`). A socket
  file left behind by a crash is harmless: whoever takes the lock knows no
  primary exists and removes it before listening.

`claim_or_forward` loops over the two: try to deliver the request; else try
to take the lock; else (someone holds the lock but is not listening yet —
it is still starting) wait briefly and try again, for a bounded time.

The protocol is one JSON line each way, semantic and small::

    → {"show": "main window"}            or {"show": "notch", "notch_config": "/abs/path"}
    ← {"ok": true, "pid": 4242}

Directory: ``$XDG_RUNTIME_DIR/mira`` — per user, mode 0700, on tmpfs, emptied at logout and reboot — else
``/tmp/mira-<uid>``, created 0700 and refused unless owned by this user and
private. Never derived from the working directory, so launchers agree from
anywhere, and other users never share it.

Qt-free on purpose: a launcher that only forwards exits without ever
creating a `QApplication`.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path

SOCKET_NAME = "runtime.sock"
LOCK_NAME = "runtime.lock"

# The surfaces a request can ask for; `mira.ui.desktop_shell.Surface` values.
MAIN_WINDOW = "main window"
NOTCH = "notch"
SURFACES = frozenset({MAIN_WINDOW, NOTCH})

MAX_MESSAGE_BYTES = 4096
DEFAULT_WAIT_S = 15.0
RETRY_INTERVAL_S = 0.1
REPLY_TIMEOUT_S = 10.0


class InstanceError(RuntimeError):
    """The runtime directory is unusable, or a primary never answered."""


@dataclass(frozen=True, slots=True)
class SurfaceRequest:
    """Show one surface in the runtime; the only request there is."""

    show: str
    notch_config: str | None = None

    def __post_init__(self):
        if self.show not in SURFACES:
            raise ValueError(f"unknown surface {self.show!r}")

    def encode(self) -> bytes:
        data = {"show": self.show}
        if self.notch_config is not None:
            data["notch_config"] = self.notch_config
        return (json.dumps(data) + "\n").encode("utf-8")

    @classmethod
    def decode(cls, line: bytes) -> SurfaceRequest:
        """Parse one request line; ValueError for anything malformed."""
        try:
            data = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"not a request: {error}") from error
        if not isinstance(data, dict) or set(data) - {"show", "notch_config"}:
            raise ValueError("not a request")
        config = data.get("notch_config")
        if config is not None and not isinstance(config, str):
            raise ValueError("notch_config must be a path")
        return cls(data.get("show"), config)


def runtime_directory(environ: dict[str, str] | None = None, *, uid: int | None = None) -> Path:
    """This user's runtime directory, created if needed; see the module."""

    environ = os.environ if environ is None else environ
    uid = os.getuid() if uid is None else uid
    xdg = (environ.get("XDG_RUNTIME_DIR") or "").strip()
    if xdg and Path(xdg).is_absolute() and Path(xdg).is_dir():
        return _private_directory(Path(xdg) / "mira", uid)
    return _private_directory(Path("/tmp") / f"mira-{uid}", uid)


def _private_directory(path: Path, uid: int) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as error:
        raise InstanceError(f"cannot create runtime directory {path}: {error}") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise InstanceError(f"runtime directory {path} is not a private directory of this user")
    return path


@dataclass
class Primary:
    """This process is the runtime. Holds the lock until `release` or exit."""

    directory: Path
    lock_fd: int

    @property
    def socket_path(self) -> Path:
        return self.directory / SOCKET_NAME

    def release(self) -> None:
        # The lock file itself stays: unlinking it would let a newcomer lock a
        # fresh file while a waiter still holds the old one.
        if self.lock_fd >= 0:
            os.close(self.lock_fd)
            self.lock_fd = -1


@dataclass(frozen=True)
class Forwarded:
    """Another process is the runtime and accepted the request."""

    pid: int | None


def try_lock(directory: Path) -> int | None:
    """An fd holding the exclusive runtime lock, or None if someone has it."""

    # Not inherited by programs MIRA launches (PEP 446), so an application
    # opened by an action can never keep the lock alive after MIRA exits.
    fd = os.open(directory / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return None
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def send_request(path: Path, request: SurfaceRequest, *, timeout_s: float = REPLY_TIMEOUT_S) -> dict | None:
    """Deliver ``request`` to the primary at ``path``; its reply, or None.

    None means nobody is listening there (no file, a stale file, refused).
    A primary that accepts but never answers raises `InstanceError`.
    """

    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout_s)
        try:
            connection.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED, errno.ENOTSOCK):
                return None
            raise
        try:
            connection.sendall(request.encode())
            reply = _read_line(connection)
        except (socket.timeout, OSError) as error:
            raise InstanceError(f"the running M.I.R.A. did not answer: {error}") from error
    finally:
        connection.close()
    try:
        data = json.loads(reply.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstanceError(f"unreadable reply from the running M.I.R.A.: {reply!r}") from error
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise InstanceError(f"the running M.I.R.A. refused the request: {data!r}")
    return data


def _read_line(connection: socket.socket) -> bytes:
    data = b""
    while b"\n" not in data:
        chunk = connection.recv(MAX_MESSAGE_BYTES)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_MESSAGE_BYTES:
            break
    return data.split(b"\n", 1)[0]


def claim_or_forward(
    directory: Path,
    request: SurfaceRequest,
    *,
    wait_s: float = DEFAULT_WAIT_S,
    interval_s: float = RETRY_INTERVAL_S,
    sleep=time.sleep,
    clock=time.monotonic,
) -> Primary | Forwarded:
    """Become the runtime, or hand ``request`` to the one that is; see module.

    Raises `InstanceError` when a primary holds the lock but does not start
    listening within ``wait_s`` (a hung runtime).
    """

    socket_path = directory / SOCKET_NAME
    deadline = clock() + wait_s
    while True:
        reply = send_request(socket_path, request)
        if reply is not None:
            pid = reply.get("pid")
            return Forwarded(pid if isinstance(pid, int) else None)
        fd = try_lock(directory)
        if fd is not None:
            return Primary(directory, fd)
        if clock() >= deadline:
            raise InstanceError(
                f"another M.I.R.A. holds {directory / LOCK_NAME} but is not answering on {socket_path}"
            )
        sleep(interval_s)
