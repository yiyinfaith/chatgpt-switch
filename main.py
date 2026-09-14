from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from switcher.server import Application, serve, serve_webview, reuse_running


def main():
    parser = argparse.ArgumentParser(description="ChatGPT Switch — portable local desktop UI")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--autostart", action="store_true", help="Launched at login; honor the background startup preference")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--test-root", type=Path, help="Use isolated fixture config and fake install/restart adapters")
    parser.add_argument("--test-missing", action="store_true")
    parser.add_argument("--diagnose", type=Path)
    args = parser.parse_args()
    frozen = getattr(sys, "frozen", False)
    root = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
    ui_root = (Path(getattr(sys, "_MEIPASS", root)) / "ui")
    config_dir = Path.home() / ".codex"
    if args.test_root:
        root = args.test_root.resolve()
        config_dir = root / "fixture-config"
        config_dir.mkdir(parents=True, exist_ok=True)
    if not args.test_root and not args.diagnose and reuse_running(root, show=not args.autostart):
        return
    app = Application(root, config_dir, bool(args.test_root), args.test_missing)
    if args.diagnose:
        app.environment = app.integration.detect()
        args.diagnose.write_text(json.dumps(app.state(), ensure_ascii=False, indent=2), "utf-8")
        return
    app.initialize_settings()
    if args.no_browser:
        serve(app, ui_root, args.port, True)
    else:
        serve_webview(app, ui_root, args.port,
                      start_hidden=args.autostart and app.settings()["autoStartBackground"])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = "ChatGPT Switch 无法启动：" + str(exc)
        if sys.stderr:
            print(message, file=sys.stderr)
        elif sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "ChatGPT Switch", 0x10)
        sys.exit(1)
