# -*- coding: utf-8 -*-
"""FastAPI 入口：同一进程提供 API 与静态页面。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from evaluator.api import bootstrap, router
from evaluator.paths import STATIC_DIR

app = FastAPI(title="智能体数值准确性测评", version="1.0.0")
app.include_router(router)


@app.on_event("startup")
def _startup() -> None:
    bootstrap()


@app.get("/")
def index_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/runs/{run_id}")
def run_page(run_id: str):
    return FileResponse(STATIC_DIR / "run.html")


@app.get("/runs/{run_id}/cases/{case_id}")
def case_page(run_id: str, case_id: str):
    return FileResponse(STATIC_DIR / "case.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
