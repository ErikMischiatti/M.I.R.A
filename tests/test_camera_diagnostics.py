"""Camera status in the debug drawer: read-only, polled only while visible."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mira.domain.camera import CameraError, CameraInfo, CameraState, CameraStatus
from mira.domain.scheduler import ManualScheduler

from camera_doubles import FakeCameraSource


@pytest.fixture
def qt_application(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "status,text",
    [
        (CameraStatus(CameraState.DISABLED), "Disabled (set MIRA_CAMERA=1 to enable)"),
        (CameraStatus(CameraState.STOPPED), "Stopped"),
        (CameraStatus(CameraState.OPENING), "Opening"),
        (
            CameraStatus(CameraState.ACTIVE, "cam", CameraInfo("0", 640, 480, 30.0)),
            "Active: 0 640x480 @ 30 fps",
        ),
        (CameraStatus(CameraState.ACTIVE, "cam", CameraInfo("0", 640, 480)), "Active: 0 640x480"),
        (CameraStatus(CameraState.UNAVAILABLE, "device busy"), "Unavailable: device busy"),
        (CameraStatus(CameraState.FAILED, "5 consecutive reads failed"), "Failed: 5 consecutive reads failed"),
    ],
)
def test_each_camera_state_has_a_readable_line(qt_application, status, text):
    from mira.ui.debug_panel import describe_camera_status

    assert describe_camera_status(status) == text


def test_the_drawer_shows_the_current_status_and_polls_only_while_visible(qt_application):
    from mira.ui.debug_panel import DebugPanel
    from mira.ui.face.face_widget import FaceWidget

    current = [CameraStatus(CameraState.OPENING)]
    face = FaceWidget()
    panel = DebugPanel(face, lambda: current[0])
    try:
        assert panel.camera_status_label.text() == "Opening"
        assert not panel.camera_status_timer.isActive()

        panel.show()
        assert panel.camera_status_timer.isActive()
        current[0] = CameraStatus(CameraState.UNAVAILABLE, "no device")
        panel.refresh_camera_status()
        assert panel.camera_status_label.text() == "Unavailable: no device"

        panel.hide()
        assert not panel.camera_status_timer.isActive()
    finally:
        face.frame_timer.stop()
        panel.close()
        face.close()


def test_a_drawer_without_a_status_reader_has_no_camera_section(qt_application):
    from mira.ui.debug_panel import DebugPanel
    from mira.ui.face.face_widget import FaceWidget

    face = FaceWidget()
    panel = DebugPanel(face)
    try:
        assert panel.camera_status_label is None
        assert panel.camera_status_timer is None
    finally:
        face.frame_timer.stop()
        panel.close()
        face.close()


def test_the_window_reports_the_composed_camera_status(qt_application):
    from mira.application.composition import build_application
    from mira.ui.desktop_shell import DesktopShell

    application = build_application(
        scheduler=ManualScheduler(),
        camera_source=FakeCameraSource(open_error=CameraError("no device")),
    )
    desktop = DesktopShell(application)
    window = desktop.window
    try:
        assert window.debug_panel.camera_status_label.text() == "Stopped"
        assert window.camera_status() is application.camera.status
    finally:
        desktop.shutdown()
        application.shutdown()
        # A live notch would keep answering the application's screen signals.
        from PySide6.QtCore import QCoreApplication, QEvent

        for obj in (desktop.notch, desktop.window, desktop.animator):
            obj.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
