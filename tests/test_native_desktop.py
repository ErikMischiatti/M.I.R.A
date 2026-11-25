"""Native Windows lifecycle/IPC tests; no camera, audio or real system action."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='native Windows integration')


def test_native_lock_is_exclusive_and_released(tmp_path):
    from mira.application.instance import try_lock
    fd = try_lock(tmp_path)
    assert fd is not None
    try:
        assert try_lock(tmp_path) is None
    finally:
        os.close(fd)
    fd = try_lock(tmp_path)
    assert fd is not None
    os.close(fd)


def test_native_endpoint_is_short_and_per_user(tmp_path):
    from mira.application.instance import endpoint_path
    assert endpoint_path(tmp_path) == endpoint_path(tmp_path)
    assert endpoint_path(tmp_path) != endpoint_path(tmp_path / 'other-user')
    assert len(str(endpoint_path(tmp_path))) < 80
    assert '/' not in str(endpoint_path(tmp_path))
    assert '\\' not in str(endpoint_path(tmp_path))


def test_named_pipe_roundtrip_with_real_qt_server(tmp_path):
    from mira.application.instance import SurfaceRequest, endpoint_path, send_request
    endpoint = endpoint_path(tmp_path)
    code = '''
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication,QTimer
from mira.ui.instance_server import InstanceServer
app=QCoreApplication([])
server=InstanceServer(Path(sys.argv[1]),lambda request: print(request.show,flush=True))
server.listen()
print('READY',flush=True)
QTimer.singleShot(15000,app.quit)
app.exec()
server.close()
'''
    p = subprocess.Popen([sys.executable, '-u', '-c', code, str(endpoint)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == 'READY'
        reply = send_request(endpoint, SurfaceRequest('notch'), timeout_s=3)
        assert reply['ok'] is True
        assert reply['pid'] == p.pid
        assert p.stdout.readline().strip() == '[mira] surface request received: notch'
        assert p.stdout.readline().strip() == 'notch'
    finally:
        p.terminate()
        p.communicate(timeout=10)


def test_no_native_server_returns_none(tmp_path):
    from mira.application.instance import SurfaceRequest, endpoint_path, send_request
    assert send_request(endpoint_path(tmp_path), SurfaceRequest('notch'), timeout_s=0.1) is None


@pytest.mark.parametrize('surface', [[], ['--notch']])
def test_native_application_entrypoint_starts_and_shuts_down(surface):
    # Disable concrete audio/system adapters: validate the real entrypoint,
    # named pipe and UI lifecycle without querying microphones or hardware.
    code = r"""
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from mira import main as entry
from mira.core.audio_input import AudioInputService
from mira.domain.sound import SilentSoundOutput
entry.desktop_audio_backend=lambda:None
entry.desktop_sound_output=lambda:SilentSoundOutput()
original=QApplication.exec
def bounded(self):
    QTimer.singleShot(300,self.quit)
    return original()
QApplication.exec=bounded
entry.main(sys.argv[1:])
"""
    result = subprocess.run([sys.executable, '-u', '-c', code, *surface],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '[mira] ready: listening for surface requests' in result.stdout
    assert 'Traceback' not in result.stderr
