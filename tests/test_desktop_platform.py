"""Backend policy: desktop placement without overriding user or CI choices."""
import pytest
from mira.ui.desktop.platform import configure_desktop_platform


def test_wslg_uses_x11_when_a_display_is_available():
    env = {'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0'}
    assert configure_desktop_platform(environment=env, wslg=True)
    assert env['QT_QPA_PLATFORM'] == 'xcb'


@pytest.mark.parametrize('platform', ['offscreen', 'wayland', 'xcb', ''])
def test_explicit_platform_is_preserved(platform):
    env = {'DISPLAY': ':0', 'QT_QPA_PLATFORM': platform}
    assert not configure_desktop_platform(environment=env, wslg=True)
    assert env['QT_QPA_PLATFORM'] == platform


@pytest.mark.parametrize('wslg,env', [(False, {'DISPLAY': ':0'}), (True, {})])
def test_other_desktops_and_displayless_processes_are_unchanged(wslg, env):
    original = env.copy()
    assert not configure_desktop_platform(environment=env, wslg=wslg)
    assert env == original
