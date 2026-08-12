"""Desktop entry point for HexCast (model B: everything runs on the user's
machine). Boots the local FastAPI server and opens the browser to the editor.

Frozen-app subtlety: the render pipeline is spawned as Python subprocesses
(`[sys.executable, "pipeline/xxx.py", ...]`). Inside a PyInstaller bundle
`sys.executable` is THIS binary, so we re-dispatch: if we're invoked with a
script path, run it via runpy instead of starting the server.
"""
import os
import socket
import sys
import threading
import time


def _resource_root() -> str:
    # PyInstaller unpacks bundled data next to sys._MEIPASS; in dev it's ../ (repo root)
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _dispatch_script() -> None:
    """When re-invoked as a pipeline subprocess, run that script and exit."""
    if len(sys.argv) > 1 and sys.argv[1].endswith(".py"):
        root = _resource_root()
        os.chdir(root)
        sys.path.insert(0, os.path.join(root, "pipeline"))
        sys.path.insert(0, root)
        script = sys.argv[1]
        sys.argv = sys.argv[1:]           # let the script see its own argv[0]
        import runpy
        runpy.run_path(script, run_name="__main__")
        sys.exit(0)


def _log_path() -> str:
    return os.path.join(os.path.expanduser("~/HexCast"), "hexcast.log")


def _log(message: str) -> None:
    """Best-effort launcher log for packaged builds whose console is hidden."""
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except Exception:
        pass


def _available_port(host: str, preferred: int) -> int:
    """Avoid opening a dead page when a stale process already owns the port."""
    for port in range(preferred, preferred + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
            return port
        except OSError:
            continue
    raise RuntimeError(f"no free local port in {preferred}-{preferred + 19}")


def _fatal(message: str) -> None:
    _log("FATAL: " + message)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, "HexCast", 0x10)
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def main() -> None:
    root = _resource_root()
    os.chdir(root)                        # so app.py finds editor/dist, assets, pipeline
    sys.path.insert(0, root)

    # bundled ffmpeg first on PATH (the pipeline shells out to `ffmpeg`)
    os.environ["PATH"] = os.path.join(root, "bin") + os.pathsep + os.environ.get("PATH", "")
    # the app bundle is read-only -> keep all user data in the home dir
    os.environ.setdefault("HEXCAST_DATA_DIR", os.path.expanduser("~/HexCast/projects"))
    # carry over projects from the pre-rebrand location (~/Remaster -> ~/HexCast)
    _legacy_dir = os.path.expanduser("~/Remaster/projects")
    if os.path.isdir(_legacy_dir) and not os.path.exists(os.environ["HEXCAST_DATA_DIR"]):
        os.makedirs(os.path.dirname(os.environ["HEXCAST_DATA_DIR"]), exist_ok=True)
        os.rename(_legacy_dir, os.environ["HEXCAST_DATA_DIR"])
    os.makedirs(os.environ["HEXCAST_DATA_DIR"], exist_ok=True)
    # central control plane for accounts + usage + auto-update (override to run
    # against a different backend, or set to "" for self-contained local accounts)
    os.environ.setdefault("HEXCAST_AUTH_URL", "https://hexcast-central-657487551020.asia-south1.run.app")

    host = "127.0.0.1"
    preferred_port = int(os.environ.get("PORT", "8765"))
    try:
        port = _available_port(host, preferred_port)
    except Exception as e:
        _fatal(f"HexCast could not reserve a local port. {e}")
        return

    server_errors = []
    def serve():
        try:
            import uvicorn
            uvicorn.run("app:app", host=host, port=port, log_level="warning")
        except Exception as e:
            server_errors.append(str(e))
            _log("local service stopped: " + str(e))

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()

    # wait for the server to be ready
    import urllib.request
    url = f"http://{host}:{port}"
    ready = False
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            ready = True
            break
        except Exception:
            if not server_thread.is_alive():
                break
            time.sleep(0.4)
    if not ready:
        detail = server_errors[-1] if server_errors else "local service did not become ready"
        _fatal("HexCast could not start its local service. " + detail
               + f"\n\nSee {_log_path()} for details.")
        return
    _log(f"started on {url}")

    # Native desktop window (own window + dock icon, no browser chrome). Falls
    # back to the default browser if the WebView backend is unavailable.
    try:
        import webview
        webview.create_window("HexCast", url + "/editor/",
                              width=1360, height=900, min_size=(1024, 680))
        webview.start()          # blocks until the window is closed -> app quits
    except Exception as e:
        _log("native window unavailable; opening browser: " + str(e))
        import webbrowser
        webbrowser.open(url + "/editor/")
        try:
            while server_thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        if server_errors:
            _fatal("HexCast's local service stopped. Reopen HexCast.\n\n"
                   f"See {_log_path()} for details.")


if __name__ == "__main__":
    _dispatch_script()
    main()
