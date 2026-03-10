from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from brief_engine import generate_trading_brief
import uvicorn

MIN_INTERVAL = 60
DEFAULT_INTERVAL = 300
MAX_INTERVAL = 3600

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache: Dict[str, object] = {
    "brief": None,
    "last_refresh": 0.0,
    "interval": DEFAULT_INTERVAL,
}
_lock = threading.Lock()


def _clamp_interval(value: int) -> int:
    if value < MIN_INTERVAL:
        return MIN_INTERVAL
    if value > MAX_INTERVAL:
        return MAX_INTERVAL
    return value


def _recalc() -> None:
    logging.info("Brief recalculated at timestamp")
    brief = generate_trading_brief()
    with _lock:
        _cache["brief"] = brief
        _cache["last_refresh"] = time.time()


def _scheduler_loop() -> None:
    logging.info("Scheduler started")
    while True:
        with _lock:
            interval = _cache["interval"]
            last = _cache["last_refresh"]
        if time.time() - last >= interval:
            try:
                _recalc()
            except Exception as exc:
                logging.warning("Brief recalculation failed: %s", exc)
        time.sleep(1)


@app.on_event("startup")
def startup_event() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.info("Refresh interval: %ss", DEFAULT_INTERVAL)
    threading.Thread(target=_scheduler_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/brief")
def get_brief() -> JSONResponse:
    with _lock:
        brief = _cache["brief"]
    if brief is None:
        try:
            _recalc()
            with _lock:
                brief = _cache["brief"]
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(brief["data"])


@app.post("/api/refresh")
def refresh_now() -> JSONResponse:
    try:
        _recalc()
        return JSONResponse({"status": "ok", "ts": time.time()})
    except Exception as exc:
        return JSONResponse({"status": "error", "error": str(exc)}, status_code=500)


@app.get("/api/config")
def get_config() -> JSONResponse:
    with _lock:
        interval = _cache["interval"]
    return JSONResponse({"refresh_interval": interval})


@app.post("/api/config")
async def set_config(request: Request) -> JSONResponse:
    body = await request.json()
    interval = _clamp_interval(int(body.get("refresh_interval", DEFAULT_INTERVAL)))
    with _lock:
        _cache["interval"] = interval
    logging.info("Refresh interval: %ss", interval)
    return JSONResponse({"refresh_interval": interval})


if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)
