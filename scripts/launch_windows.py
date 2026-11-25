"""WSL bridge to the repository's native Windows runtime; no shell interpolation."""
from __future__ import annotations

import base64
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


def script_for(interpreter: str, module: str, arguments: list[str], environment: dict[str, str]) -> str:
    args = list(arguments)
    for index, arg in enumerate(args):
        if arg == '--config':
            if index + 1 < len(args):
                args[index + 1] = windows_path(args[index + 1])
        elif arg.startswith('--config='):
            args[index] = '--config=' + windows_path(arg.split('=', 1)[1])
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
    lines.append('& ' + literal(windows_path(interpreter)) + ' -u -m ' + literal(module)
                 + ' ' + ' '.join(literal(arg) for arg in args))
    lines.append('exit $LASTEXITCODE')
    return '\n'.join(lines)


def main() -> int:
    interpreter, module, *arguments = sys.argv[1:]
    shell = shutil.which('powershell.exe')
    if not shell:
        print('[launcher] Windows interoperability is unavailable: powershell.exe not found', file=sys.stderr)
        return 1
    script = script_for(interpreter, module, arguments, dict(os.environ))
    print('[launcher] native Windows runtime (monitor/DPI and Windows desktop integration)', flush=True)
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return subprocess.run([shell, '-NoProfile', '-EncodedCommand', encoded]).returncode


if __name__ == '__main__':
    raise SystemExit(main())
