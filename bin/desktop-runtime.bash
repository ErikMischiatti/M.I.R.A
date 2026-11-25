# Shared launcher policy. Called after the launcher resolves repo_root.
# Native Windows is preferred under WSL only when its environment is installed.
launch_desktop_runtime() {
    local module="$1"
    shift
    local mode="${MIRA_DESKTOP_RUNTIME:-auto}"
    case "$mode" in
        auto|linux|windows) ;;
        *) printf '[launcher] MIRA_DESKTOP_RUNTIME must be auto, linux or windows\n' >&2; exit 2 ;;
    esac
    if [ "$mode" != linux ] && { [ -n "${WSL_INTEROP:-}" ] || [ -d /mnt/wslg ]; }; then
        local candidate
        for candidate in "$repo_root/.venv/Scripts/python.exe" "$repo_root/.windows-runtime/python.exe"; do
            if [ -f "$candidate" ]; then
                exec python3 "$repo_root/scripts/launch_windows.py" "$candidate" "$module" "$@"
            fi
        done
        if [ "$mode" = windows ]; then
            printf '[launcher] Native Windows environment missing; see docs/development.md\n' >&2
            exit 1
        fi
        printf '[launcher] No native Windows environment; using Linux/WSLg (Windows desktop integration unavailable)\n' >&2
    elif [ "$mode" = windows ]; then
        printf '[launcher] Windows mode requires WSL interoperability\n' >&2
        exit 1
    fi
}
