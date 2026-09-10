# -*- coding: utf-8 -*-
"""启动本地测评页面。"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluator.settings import load_settings


def main() -> None:
    settings = load_settings()
    app_cfg = settings.get("app") or {}
    host = os.environ.get("EVAL_HOST") or app_cfg.get("host") or "127.0.0.1"
    port = int(os.environ.get("EVAL_PORT") or app_cfg.get("port") or 8765)
    url = f"http://{host}:{port}/"

    def _open():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if app_cfg.get("open_browser", True):
        threading.Thread(target=_open, daemon=True).start()
    import uvicorn
    from evaluator.app import app

    print(f"智能体数值准确性测评: {url}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
