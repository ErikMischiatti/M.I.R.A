"""The primary runtime's listening end of `mira.application.instance`.

A `QLocalServer` (a Unix-domain socket on Linux) on the runtime's socket
path, readable and writable by this user only. Each connection carries one
request line; the server hands the decoded `SurfaceRequest` to ``handler``
on the Qt main thread, then replies and closes. The reply is sent only after
the handler returned, so a forwarding launcher exits once its surface is
showing.

The caller holds the runtime lock, so any socket file already at the path is
a leftover of a dead runtime and is removed before listening.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from mira.application.instance import MAX_MESSAGE_BYTES, InstanceError, SurfaceRequest


class InstanceServer:
    def __init__(self, path: Path, handler: Callable[[SurfaceRequest], None]):
        self.path = path
        self._handler = handler
        self._server = QLocalServer()
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self._buffers: dict[QLocalSocket, bytes] = {}

    def listen(self) -> None:
        # Only the lock holder gets here: a file at the path is stale.
        QLocalServer.removeServer(str(self.path))
        if not self._server.listen(str(self.path)):
            raise InstanceError(f"cannot listen on {self.path}: {self._server.errorString()}")

    @property
    def listening(self) -> bool:
        return self._server.isListening()

    def close(self) -> None:
        """Stop accepting; removes the socket file. Idempotent."""
        if self._server.isListening():
            self._server.close()
        for connection in list(self._buffers):
            connection.abort()
        self._buffers.clear()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            self._buffers[connection] = b""
            connection.readyRead.connect(lambda c=connection: self._on_ready(c))
            connection.disconnected.connect(lambda c=connection: self._forget(c))
            if connection.bytesAvailable():
                self._on_ready(connection)

    def _forget(self, connection: QLocalSocket) -> None:
        self._buffers.pop(connection, None)
        connection.deleteLater()

    def _on_ready(self, connection: QLocalSocket) -> None:
        if connection not in self._buffers:
            return
        data = self._buffers[connection] + bytes(connection.readAll())
        if b"\n" not in data and len(data) <= MAX_MESSAGE_BYTES:
            self._buffers[connection] = data
            return
        del self._buffers[connection]
        try:
            request = SurfaceRequest.decode(data.split(b"\n", 1)[0])
        except ValueError as error:
            print(f"[mira] rejected instance request: {error}")
            self._reply(connection, {"ok": False, "error": str(error)})
            return
        print(f"[mira] surface request received: {request.show}")
        try:
            self._handler(request)
        except Exception as error:  # a broken request must not take the runtime down
            print(f"[mira] surface request failed: {error}")
            self._reply(connection, {"ok": False, "error": str(error)})
            return
        self._reply(connection, {"ok": True, "pid": os.getpid()})

    def _reply(self, connection: QLocalSocket, data: dict) -> None:
        connection.write((json.dumps(data) + "\n").encode("utf-8"))
        connection.flush()
        connection.disconnectFromServer()
