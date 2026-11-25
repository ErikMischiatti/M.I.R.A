"""WSL bridge to the repository's native Windows runtime; no shell interpolation."""
from __future__ import annotations

import base64
import json
import ntpath
import os
from pathlib import Path
import shutil
import subprocess
import sys


def windows_path(value: str) -> str:
    if ntpath.splitdrive(value)[0]:
        return value
    return subprocess.check_output(
        ['wslpath', '-w', str(Path(value).expanduser().absolute())], text=True,
    ).strip()


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def native_arguments(arguments: list[str]) -> list[str]:
    args = list(arguments)
    for index, arg in enumerate(args):
        if arg == '--config':
            if index + 1 < len(args):
                args[index + 1] = windows_path(args[index + 1])
        elif arg.startswith('--config='):
            args[index] = '--config=' + windows_path(arg.split('=', 1)[1])
    return args


def direct_launch(manifest_path: str, module: str, arguments: list[str], environment: dict[str, str]):
    """Use WSL interoperability directly once a local cache is complete.

    Named settings cross through WSLENV, not an encoded shell command line.
    The descriptor contains program paths only, never settings or credentials.
    """
    try:
        manifest = json.loads(Path(manifest_path).read_text())
        descriptor_path = Path(manifest_path).with_name(
            'ready-' + manifest['runtime_key'] + '-' + manifest['app_key'] + '.json')
        descriptor = json.loads(descriptor_path.read_text(encoding='utf-8-sig'))
        executable = subprocess.check_output(['wslpath', '-u', descriptor['Python']], text=True).strip()
        bootstrap = subprocess.check_output(['wslpath', '-u', descriptor['Bootstrap']], text=True).strip()
        executable_path = Path(executable)
        runtime_root = executable_path.parent.parent if executable_path.parent.name.lower() == 'scripts' else executable_path.parent
        app_root = Path(bootstrap).parent.parent
        if (not os.access(executable, os.X_OK) or not Path(bootstrap).is_file()
                or (runtime_root / '.complete').read_text().strip() != manifest['runtime_key']
                or (app_root / '.complete').read_text().strip() != manifest['app_key']):
            return None
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        return None
    env = dict(environment)
    env['QT_QPA_PLATFORM'] = env.get('QT_QPA_PLATFORM', 'windows')
    if env['QT_QPA_PLATFORM'] not in ('windows', 'offscreen', 'minimal'):
        env['QT_QPA_PLATFORM'] = 'windows'
    if env.get('MIRA_NOTCH_CONFIG'):
        env['MIRA_NOTCH_CONFIG'] = windows_path(env['MIRA_NOTCH_CONFIG'])
    forwarded = {key for key in env if key.startswith('MIRA_') and all(c.isalnum() or c == '_' for c in key)}
    forwarded.add('QT_QPA_PLATFORM')
    existing = [part for part in env.get('WSLENV', '').split(':')
                if part and part.split('/', 1)[0] not in forwarded]
    env['WSLENV'] = ':'.join(existing + sorted(forwarded))
    argv = [executable, '-u', descriptor['Bootstrap'], '--cwd', windows_path(str(Path.cwd())),
            descriptor['Source'], descriptor['Mirror'], module, *native_arguments(arguments)]
    return argv, env


def script_for(interpreter: str, module: str, arguments: list[str], environment: dict[str, str], *, cache_manifest: str | None = None) -> str:
    args = native_arguments(arguments)
    lines = ["$ErrorActionPreference='Stop'", "$ProgressPreference='SilentlyContinue'",
             'Set-Location -LiteralPath ' + literal(windows_path(str(Path.cwd()))),
             '[Environment]::CurrentDirectory=' + literal(windows_path(str(Path.cwd())))]
    for key, value in environment.items():
        if key.startswith('MIRA_') and all(c.isalnum() or c == '_' for c in key):
            if key == 'MIRA_NOTCH_CONFIG' and value:
                value = windows_path(value)
            lines.append('$env:' + key + '=' + literal(value))
    # A Linux xcb/Wayland override must not select a Linux plugin in Windows.
    platform = environment.get('QT_QPA_PLATFORM', 'windows')
    if platform not in ('windows', 'offscreen', 'minimal'):
        platform = 'windows'
    lines.append('$env:QT_QPA_PLATFORM=' + literal(platform))
    command_args = ' '.join(literal(arg) for arg in args)
    native_python = literal(windows_path(interpreter))
    if cache_manifest:
        cache_code = Path(__file__).with_name('windows_startup_cache.ps1').read_text()
        lines.append('Add-Type -AssemblyName System.IO.Compression.FileSystem')
        lines.append(cache_code)
        lines.append('$cached=$null; try { $cached=Get-MiraCachedRuntime ' + literal(windows_path(cache_manifest))
                     + ' } catch { [Console]::Error.WriteLine("[launcher] startup cache unavailable; using source runtime: " + $_.Exception.Message) }')
        lines.append('if ($null -ne $cached) { & $cached.Python -u $cached.Bootstrap $cached.Source $cached.Mirror '
                     + literal(module) + ' ' + command_args + ' } else { & ' + native_python
                     + ' -u -m ' + literal(module) + ' ' + command_args + ' }')
    else:
        lines.append('& ' + native_python + ' -u -m ' + literal(module) + ' ' + command_args)
    lines.append('exit $LASTEXITCODE')
    return '\n'.join(lines)


def main() -> int:
    interpreter, module, *arguments = sys.argv[1:]
    shell = shutil.which('powershell.exe')
    if not shell:
        print('[launcher] Windows interoperability is unavailable: powershell.exe not found', file=sys.stderr)
        return 1
    manifest = None
    # Local Windows checkouts already have fast native I/O. UNC/WSL checkouts
    # benefit from a user-local executable mirror; source resources stay live.
    if os.environ.get('MIRA_STARTUP_CACHE', '1').lower() not in ('0', 'off', 'false') and windows_path(interpreter).startswith('\\\\'):
        from startup_cache import prepare_manifest
        try:
            manifest = str(prepare_manifest(Path(__file__).resolve().parents[1], Path(interpreter), windows_path))
        except (OSError, subprocess.CalledProcessError) as error:
            print(f'[launcher] startup cache unavailable; using source runtime: {error}', file=sys.stderr)
    print('[launcher] native Windows runtime (monitor/DPI and Windows desktop integration)', flush=True)
    if manifest:
        direct = direct_launch(manifest, module, arguments, dict(os.environ))
        if direct is not None:
            argv, environment = direct
            os.execve(argv[0], argv, environment)
    script = script_for(interpreter, module, arguments, dict(os.environ), cache_manifest=manifest)
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return subprocess.run([shell, '-NoProfile', '-EncodedCommand', encoded]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
