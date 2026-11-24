"""Cross-feature resource ownership after branch consolidation."""
from mira.application.composition import build_application
from mira.domain.scheduler import ManualScheduler


def test_combined_services_stop_before_the_scheduler(monkeypatch):
    app = build_application(scheduler=ManualScheduler())
    calls = []
    for name in ('listening', 'audio', 'device_info', 'system_notices', 'timer',
                 'sound_cues', 'sound', 'perception', 'camera', 'voice', 'scheduler'):
        monkeypatch.setattr(getattr(app, name), 'shutdown', lambda name=name: calls.append(name))
    app.shutdown()
    assert calls.index('perception') < calls.index('camera')
    assert calls.index('listening') < calls.index('audio')
    assert calls[-1] == 'scheduler'
    assert len(calls) == 11


def test_combined_default_graph_has_no_optional_hardware_workers():
    app = build_application(scheduler=ManualScheduler())
    try:
        app.start()
        assert not app.camera.running
        assert not app.perception.running
        assert not app.voice.enabled
    finally:
        app.shutdown()
