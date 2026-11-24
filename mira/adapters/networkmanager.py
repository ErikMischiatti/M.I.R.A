"""Network connectivity as NetworkManager judges it, over D-Bus.

NetworkManager already condenses links, addresses and its own reachability
check into one property, ``Connectivity`` on
``org.freedesktop.NetworkManager`` (system bus): none, portal, limited or
full. That is the user-level question — "can I reach the network?" — so
nothing here looks at interfaces or routes, which change far more often than
the answer does. The property is read once at start (the producer's
baseline) and then followed through ``PropertiesChanged`` signals delivered
by the Qt event loop (QtDBus, part of PySide6): no polling, no subprocess.

Without a system bus or NetworkManager the source is unavailable.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, SLOT, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

from mira.domain.system_events import Connectivity, ConnectivityEvent, SystemEventListener

SERVICE = "org.freedesktop.NetworkManager"
PATH = "/org/freedesktop/NetworkManager"
PROPERTIES = "org.freedesktop.DBus.Properties"

# NMConnectivityState
_STATES = {1: Connectivity.OFFLINE, 2: Connectivity.LIMITED, 3: Connectivity.LIMITED, 4: Connectivity.ONLINE}


def connectivity_from_nm(value) -> Connectivity | None:
    """The state for NetworkManager's value; None for UNKNOWN (0) or junk."""
    if hasattr(value, "variant"):
        value = value.variant()
    try:
        return _STATES.get(int(value))
    except (TypeError, ValueError):
        return None


class _Receiver(QObject):
    def __init__(self, source: NetworkManagerConnectivitySource):
        super().__init__()
        self._source = source

    @Slot(QDBusMessage)
    def changed(self, message: QDBusMessage):
        arguments = message.arguments()
        if len(arguments) >= 2 and arguments[0] == SERVICE and isinstance(arguments[1], dict):
            self._source.report(arguments[1].get("Connectivity"))


class NetworkManagerConnectivitySource:
    """A `SystemEventSource` for connectivity; see the module."""

    name = "network connectivity (NetworkManager)"

    def __init__(self, bus_factory=QDBusConnection.systemBus):
        self._bus_factory = bus_factory
        self._bus = None
        self._receiver: _Receiver | None = None
        self._listener: SystemEventListener | None = None

    def start(self, listener: SystemEventListener) -> bool:
        if self._receiver is not None:
            return True
        bus = self._bus_factory()
        if not bus.isConnected():
            return False
        reply = QDBusInterface(SERVICE, PATH, PROPERTIES, bus).call("Get", SERVICE, "Connectivity")
        if reply.type() is not QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return False
        receiver = _Receiver(self)
        if not bus.connect(SERVICE, PATH, PROPERTIES, "PropertiesChanged", receiver, SLOT("changed(QDBusMessage)")):
            receiver.deleteLater()
            return False
        self._bus, self._receiver, self._listener = bus, receiver, listener
        self.report(reply.arguments()[0])
        return True

    def shutdown(self) -> None:
        if self._receiver is not None:
            self._bus.disconnect(SERVICE, PATH, PROPERTIES, "PropertiesChanged", self._receiver,
                                 SLOT("changed(QDBusMessage)"))
            self._receiver.deleteLater()
        self._bus = self._receiver = self._listener = None

    def report(self, value) -> None:
        """Deliver NetworkManager's ``value``; ignores UNKNOWN and absence."""
        state = connectivity_from_nm(value)
        if state is not None and self._listener is not None:
            self._listener(ConnectivityEvent(state))
