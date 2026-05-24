from __future__ import annotations

import platform
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import webbrowser
from urllib.parse import urlparse

from mira.actions.action_models import ActionResult


APP_ALIASES = {
    "firefox": "firefox",
    "browser": "firefox",
    "navigatore": "firefox",
    "chrome": "google-chrome",
    "google chrome": "google-chrome",
    "terminale": "terminal",
    "terminale linux": "terminal",
    "terminal": "terminal",
    "calcolatrice": "calculator",
    "calculator": "calculator",
    "file": "files",
    "files": "files",
    "explorer": "files",
    "cartelle": "files",
}

APP_COMMANDS = {
    "firefox": ["firefox"],
    "google-chrome": ["google-chrome"],
    "terminal": ["x-terminal-emulator"],
    "calculator": ["gnome-calculator"],
    "files": ["xdg-open", "."],
}

DIRECTORY_ALIASES = {
    "home": Path.home(),
    "casa": Path.home(),
    "desktop": Path.home() / "Desktop",
    "scrivania": Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "download": Path.home() / "Downloads",
    "documenti": Path.home() / "Documents",
    "documents": Path.home() / "Documents",
    "progetto": Path.cwd(),
    "project": Path.cwd(),
    "cartella corrente": Path.cwd(),
    "current": Path.cwd(),
}


def _normalize_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        return ""

    parsed_raw = urlparse(url)
    if parsed_raw.scheme and parsed_raw.scheme not in {"http", "https"}:
        return ""

    if not parsed_raw.scheme:
        url = f"https://{url}"

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""

    if not parsed.netloc or any(char.isspace() for char in parsed.netloc):
        return ""

    if "." not in parsed.netloc and parsed.netloc != "localhost":
        return ""

    return url


def _is_allowed_directory(path: Path) -> bool:
    allowed_roots = [Path.home().resolve(), Path.cwd().resolve()]
    for root in allowed_roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue

    return False


def _resolve_directory_path(raw_directory: str) -> tuple[str | None, Path | None]:
    normalized = raw_directory.strip().lower()
    if not normalized:
        return None, None

    candidate = DIRECTORY_ALIASES.get(normalized)
    if candidate is None:
        candidate = Path(raw_directory).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve()
    except OSError:
        return raw_directory, None

    if not resolved.exists() or not resolved.is_dir():
        return raw_directory, None

    if not _is_allowed_directory(resolved):
        return raw_directory, None

    return raw_directory, resolved


def _resolve_app_command(app_name: str) -> tuple[str | None, list[str] | None]:
    normalized = app_name.strip().lower()
    if not normalized:
        return None, None

    app_key = APP_ALIASES.get(normalized, normalized)
    command = APP_COMMANDS.get(app_key)

    if command is None:
        return app_key, None

    executable = command[0]
    if shutil.which(executable) is None:
        return app_key, None

    return app_key, command


def make_open_url_action():
    def handler(parameters: dict) -> ActionResult:
        raw_url = str(parameters.get("url", "")).strip()
        url = _normalize_url(raw_url)

        if not url:
            return ActionResult(
                success=False,
                action_name="open_url",
                message="Nessun URL valido fornito.",
                data={"reason": "invalid_url", "requested_url": raw_url},
            )

        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            return ActionResult(
                success=False,
                action_name="open_url",
                message=f"Errore durante l'apertura del link: {exc}",
                data={"url": url},
            )

        if not opened:
            return ActionResult(
                success=False,
                action_name="open_url",
                message="Non sono riuscito ad aprire il link richiesto.",
                data={"url": url},
            )

        return ActionResult(
            success=True,
            action_name="open_url",
            message=f"Ho aperto il link {url}.",
            data={"url": url},
        )

    return handler


def make_open_app_action():
    def handler(parameters: dict) -> ActionResult:
        raw_app_name = str(parameters.get("app_name", "")).strip()

        if not raw_app_name:
            return ActionResult(
                success=False,
                action_name="open_app",
                message="Nessuna applicazione specificata.",
            )

        resolved_name, command = _resolve_app_command(raw_app_name)

        if command is None:
            available_apps = ", ".join(sorted(APP_COMMANDS.keys()))
            return ActionResult(
                success=False,
                action_name="open_app",
                message=(
                    f"Applicazione '{raw_app_name}' non disponibile o non consentita. "
                    f"Disponibili: {available_apps}."
                ),
                data={"requested_app": raw_app_name},
            )

        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                action_name="open_app",
                message=f"Errore durante l'avvio di '{resolved_name}': {exc}",
                data={"requested_app": raw_app_name, "resolved_app": resolved_name},
            )

        return ActionResult(
            success=True,
            action_name="open_app",
            message=f"Ho avviato l'applicazione '{resolved_name}'.",
            data={"requested_app": raw_app_name, "resolved_app": resolved_name},
        )

    return handler


def make_show_notification_action():
    def handler(parameters: dict) -> ActionResult:
        title = str(parameters.get("title", "N.E.R.O")).strip() or "N.E.R.O"
        text = str(parameters.get("text", "")).strip()

        if not text:
            text = "Questa è una notifica di test."

        notify_send = shutil.which("notify-send")
        if notify_send is None:
            return ActionResult(
                success=False,
                action_name="show_notification",
                message="Il comando 'notify-send' non è disponibile su questo sistema.",
                data={"title": title, "text": text},
            )

        try:
            subprocess.Popen(
                [notify_send, title, text],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                action_name="show_notification",
                message=f"Errore durante la notifica: {exc}",
                data={"title": title, "text": text},
            )

        return ActionResult(
            success=True,
            action_name="show_notification",
            message="Ho mostrato una notifica locale.",
            data={"title": title, "text": text},
        )

    return handler


def make_open_directory_action():
    def handler(parameters: dict) -> ActionResult:
        raw_directory = str(parameters.get("directory", "")).strip()

        if not raw_directory:
            return ActionResult(
                success=False,
                action_name="open_directory",
                message="Nessuna cartella specificata.",
            )

        requested_directory, directory_path = _resolve_directory_path(raw_directory)
        if directory_path is None:
            available = ", ".join(sorted(DIRECTORY_ALIASES.keys()))
            return ActionResult(
                success=False,
                action_name="open_directory",
                message=(
                    f"Cartella '{raw_directory}' non disponibile o non consentita. "
                    f"Puoi chiedere: {available}."
                ),
                data={"requested_directory": raw_directory},
            )

        xdg_open = shutil.which("xdg-open")
        if xdg_open is None:
            return ActionResult(
                success=False,
                action_name="open_directory",
                message="Il comando 'xdg-open' non è disponibile su questo sistema.",
                data={
                    "requested_directory": requested_directory,
                    "path": str(directory_path),
                },
            )

        try:
            subprocess.Popen(
                [xdg_open, str(directory_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            return ActionResult(
                success=False,
                action_name="open_directory",
                message=f"Errore durante l'apertura della cartella: {exc}",
                data={
                    "requested_directory": requested_directory,
                    "path": str(directory_path),
                },
            )

        return ActionResult(
            success=True,
            action_name="open_directory",
            message=f"Ho aperto la cartella {directory_path}.",
            data={
                "requested_directory": requested_directory,
                "path": str(directory_path),
            },
        )

    return handler


def make_get_system_info_action():
    def handler(parameters: dict) -> ActionResult:
        info = {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "platform_version": platform.version(),
            "hostname": socket.gethostname(),
            "python_version": sys.version.split()[0],
        }

        message = (
            f"Sistema: {info['platform']} {info['platform_release']} "
            f"su host {info['hostname']}."
        )

        return ActionResult(
            success=True,
            action_name="get_system_info",
            message=message,
            data=info,
        )

    return handler


def make_get_project_path_action():
    def handler(parameters: dict) -> ActionResult:
        project_path = Path.cwd().resolve()

        return ActionResult(
            success=True,
            action_name="get_project_path",
            message=f"La cartella del progetto è {project_path}.",
            data={"path": str(project_path)},
        )

    return handler
