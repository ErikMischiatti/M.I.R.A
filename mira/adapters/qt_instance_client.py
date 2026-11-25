"""Bounded native local IPC client (named pipes on Windows)."""
from __future__ import annotations

import time
from PySide6.QtNetwork import QLocalSocket


def exchange_local_message(name: str, message: bytes, timeout_s: float, limit: int) -> bytes | None:
    connection = QLocalSocket()
    deadline = time.monotonic() + timeout_s

    def remaining() -> int:
        return max(1, int((deadline - time.monotonic()) * 1000))

    try:
        connection.connectToServer(name)
        if not connection.waitForConnected(remaining()):
            if connection.error() in (
                QLocalSocket.LocalSocketError.ServerNotFoundError,
                QLocalSocket.LocalSocketError.ConnectionRefusedError,
            ):
                return None
            raise OSError(connection.errorString())
        if connection.write(message) != len(message):
            raise OSError(connection.errorString())
        connection.flush()
        while connection.bytesToWrite():
            if not connection.waitForBytesWritten(remaining()):
                raise TimeoutError('local request write timed out')
        data = b''
        while b'\n' not in data:
            if not connection.bytesAvailable() and not connection.waitForReadyRead(remaining()):
                raise TimeoutError('local runtime reply timed out')
            data += bytes(connection.readAll())
            if len(data) > limit:
                raise ValueError('local runtime reply exceeds size limit')
            if time.monotonic() >= deadline and b'\n' not in data:
                raise TimeoutError('local runtime reply timed out')
        return data.split(b'\n', 1)[0]
    finally:
        connection.abort()
