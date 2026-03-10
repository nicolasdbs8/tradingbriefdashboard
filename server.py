from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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
DEFAULT_TOP_X = 40
UNIVERSE_REFRESH_SEC = 900
SCANNER_FAST_REFRESH_SEC = 20
MIN_QUOTE_VOLUME_USD = 20_000_000
MAX_SPREAD_PCT = 0.12
MIN_LISTING_AGE_DAYS = 90
FALLBACK_UNIVERSE = [
    "BTC/USDC", "ETH/USDC", "SOL/USDC", "XRP/USDC", "BNB/USDC",
    "DOGE/USDC", "ADA/USDC", "AVAX/USDC", "LINK/USDC", "DOT/USDC",
    "MATIC/USDC", "TRX/USDC", "LTC/USDC", "BCH/USDC", "ATOM/USDC",
    "NEAR/USDC", "APT/USDC", "ARB/USDC", "OP/USDC", "INJ/USDC",
    "ETC/USDC", "FIL/USDC", "ICP/USDC", "SUI/USDC", "AAVE/USDC",
    "UNI/USDC", "XLM/USDC", "ALGO/USDC", "HBAR/USDC", "VET/USDC",
    "RUNE/USDC", "GRT/USDC", "FTM/USDC", "EGLD/USDC", "SEI/USDC",
    "PEPE/USDC", "SHIB/USDC", "JUP/USDC", "TIA/USDC", "WIF/USDC",
]
STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "DAI", "BUSD", "USDP"}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")

app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

_cache: Dict[str, object] = {
    "briefs": {},  # symbol -> {"data": brief_data, "timestamp": ts}
    "last_refresh": 0.0,
    "interval": DEFAULT_INTERVAL,
    "current_symbol": "BTC/USDC",
    "scanner_symbols": FALLBACK_UNIVERSE[:DEFAULT_TOP_X],
    "scanner_fast_rows": {},  # symbol -> lightweight scanner row
    "last_universe_refresh": time.time(),
    "last_scanner_fast_refresh": 0.0,
}
_lock = threading.Lock()


def _clamp_interval(value: int) -> int:
    if value < MIN_INTERVAL:
        return MIN_INTERVAL
    if value > MAX_INTERVAL:
        return MAX_INTERVAL
    return value


def _normalize_symbol(symbol: Optional[str]) -> Optional[str]:
    if not symbol:
        return None
    raw = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in raw:
        return raw
    for quote in ("USDC", "USDT"):
        if raw.endswith(quote) and len(raw) > len(quote):
            base = raw[: -len(quote)]
            return f"{base}/{quote}"
    return raw


def _is_leveraged_token(base: str) -> bool:
    return any(base.endswith(suffix) for suffix in LEVERAGED_SUFFIXES)


def _ticker_quote_volume_usd(ticker: dict) -> float:
    quote_vol = ticker.get("quoteVolume")
    if quote_vol is not None:
        try:
            return float(quote_vol)
        except (TypeError, ValueError):
            return 0.0
    try:
        base_vol = float(ticker.get("baseVolume") or 0.0)
        last = float(ticker.get("last") or 0.0)
        return base_vol * last
    except (TypeError, ValueError):
        return 0.0


def _ticker_spread_pct(ticker: dict) -> Optional[float]:
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    try:
        bid_f = float(bid) if bid is not None else 0.0
        ask_f = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        return None
    if bid_f <= 0 or ask_f <= 0:
        return None
    mid = (bid_f + ask_f) / 2
    if mid <= 0:
        return None
    return ((ask_f - bid_f) / mid) * 100


def _is_too_new(market: dict) -> bool:
    info = market.get("info") or {}
    onboard = info.get("onboardDate")
    if not onboard:
        return False
    try:
        onboard_ms = int(onboard)
    except (TypeError, ValueError):
        return False
    age_sec = time.time() - (onboard_ms / 1000)
    return age_sec < MIN_LISTING_AGE_DAYS * 86400


def _select_preferred_quote(candidates: list[dict]) -> Optional[dict]:
    usdc = next((c for c in candidates if c["quote"] == "USDC"), None)
    usdt = next((c for c in candidates if c["quote"] == "USDT"), None)
    if usdc and usdt:
        return usdc if usdc["volume"] >= usdt["volume"] * 0.6 else usdt
    return usdc or usdt


def _fetch_universe_symbols(top_x: int = DEFAULT_TOP_X) -> List[str]:
    try:
        import ccxt
    except Exception as exc:
        logging.warning("ccxt unavailable, using fallback universe: %s", exc)
        return FALLBACK_UNIVERSE[:top_x]

    try:
        ex = ccxt.binance({"enableRateLimit": True, "timeout": 5000})
        markets = ex.load_markets()
        tickers = ex.fetch_tickers()
    except Exception as exc:
        logging.warning("Universe fetch failed, using fallback universe: %s", exc)
        return FALLBACK_UNIVERSE[:top_x]

    grouped: Dict[str, list[dict]] = {}
    for symbol, market in markets.items():
        if not market.get("spot", True) or not market.get("active", True):
            continue
        quote = str(market.get("quote") or "").upper()
        base = str(market.get("base") or "").upper()
        if quote not in {"USDC", "USDT"}:
            continue
        if not base or base in STABLES or _is_leveraged_token(base):
            continue
        if _is_too_new(market):
            continue
        ticker = tickers.get(symbol) or {}
        vol = _ticker_quote_volume_usd(ticker)
        if vol < MIN_QUOTE_VOLUME_USD:
            continue
        spread_pct = _ticker_spread_pct(ticker)
        if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
            continue
        grouped.setdefault(base, []).append(
            {"symbol": symbol, "quote": quote, "volume": vol}
        )

    selected: list[dict] = []
    for candidates in grouped.values():
        pick = _select_preferred_quote(candidates)
        if pick:
            selected.append(pick)

    selected.sort(key=lambda x: x["volume"], reverse=True)
    symbols = [str(row["symbol"]) for row in selected[:top_x]]
    if not symbols:
        return FALLBACK_UNIVERSE[:top_x]
    if "BTC/USDC" in symbols:
        symbols.remove("BTC/USDC")
    symbols.insert(0, "BTC/USDC")
    return symbols[:top_x]


def _quick_score(volume_usd: float, change_pct: Optional[float], spread_pct: Optional[float]) -> float:
    score = 0.0
    if volume_usd >= 1_000_000_000:
        score += 4.0
    elif volume_usd >= 300_000_000:
        score += 3.0
    elif volume_usd >= 100_000_000:
        score += 2.0
    elif volume_usd >= 50_000_000:
        score += 1.0
    if change_pct is not None:
        score += min(4.0, abs(change_pct) / 2.0)
    if spread_pct is not None:
        if spread_pct <= 0.03:
            score += 2.0
        elif spread_pct <= 0.06:
            score += 1.2
        elif spread_pct <= 0.1:
            score += 0.6
    return round(max(0.0, min(10.0, score)), 1)


def _refresh_scanner_fast(force: bool = False) -> None:
    with _lock:
        last = float(_cache.get("last_scanner_fast_refresh", 0.0))
        symbols = list(_cache.get("scanner_symbols", []))
        existing = dict(_cache.get("scanner_fast_rows", {}))
    if symbols and (not force) and (time.time() - last < SCANNER_FAST_REFRESH_SEC):
        return
    if not symbols:
        symbols = FALLBACK_UNIVERSE[:DEFAULT_TOP_X]
    try:
        import ccxt
    except Exception as exc:
        logging.warning("Fast scanner ccxt unavailable: %s", exc)
        return
    try:
        ex = ccxt.binance({"enableRateLimit": True, "timeout": 4500})
        tickers = ex.fetch_tickers()
    except Exception as exc:
        logging.warning("Fast scanner ticker fetch failed: %s", exc)
        return

    now = time.time()
    rows: Dict[str, dict] = {}
    for symbol in symbols:
        ticker = tickers.get(symbol) or tickers.get(symbol.replace("/", "")) or {}
        try:
            price = float(ticker.get("last")) if ticker.get("last") is not None else None
        except (TypeError, ValueError):
            price = None
        try:
            change_pct = float(ticker.get("percentage")) if ticker.get("percentage") is not None else None
        except (TypeError, ValueError):
            change_pct = None
        volume_usd = _ticker_quote_volume_usd(ticker)
        spread_pct = _ticker_spread_pct(ticker)
        score = _quick_score(volume_usd, change_pct, spread_pct)
        action = "WATCH" if (change_pct is not None and abs(change_pct) >= 1.0) else "WAIT"
        rows[symbol] = {
            "symbol": symbol,
            "status": "FAST",
            "action": action,
            "gate_open": False,
            "score": score,
            "setup_class": "FAST",
            "trigger_distance_pct": None,
            "price": price,
            "updated_at": now,
            "freshness_sec": 0,
            "fast_mode": True,
            "change_24h_pct": change_pct,
            "spread_pct": spread_pct,
            "volume_usd": volume_usd,
        }
    if not rows and existing:
        return
    with _lock:
        _cache["scanner_fast_rows"] = rows
        _cache["last_scanner_fast_refresh"] = now


def _ensure_universe(force: bool = False) -> List[str]:
    with _lock:
        symbols = list(_cache.get("scanner_symbols", []))
        last = float(_cache.get("last_universe_refresh", 0.0))
    if symbols and not force and (time.time() - last < UNIVERSE_REFRESH_SEC):
        return symbols
    fresh = _fetch_universe_symbols(DEFAULT_TOP_X)
    with _lock:
        _cache["scanner_symbols"] = fresh
        _cache["last_universe_refresh"] = time.time()
    return fresh


def _recalc_symbol(symbol: str, set_current: bool = False) -> dict:
    logging.info("Recalculating brief for %s", symbol)
    brief = generate_trading_brief(symbol=symbol)
    payload = {"data": brief["data"], "timestamp": time.time()}
    with _lock:
        briefs = _cache["briefs"]
        briefs[symbol] = payload
        _cache["last_refresh"] = payload["timestamp"]
        if set_current:
            _cache["current_symbol"] = symbol
    return payload


def _derive_action(brief: dict) -> str:
    active_setup = brief.get("trade", {}).get("active_setup", "NONE")
    if active_setup == "LONG":
        return "LONG ACTIVE"
    if active_setup == "SHORT":
        return "SHORT ACTIVE"
    score = float(brief.get("setup_score", {}).get("final") or 0.0)
    gate = bool(brief.get("setup_score", {}).get("trade_gate"))
    active_event = brief.get("level_event", {}).get("active_event", "none")
    if gate or active_event in {"break", "sweep_reclaim"} or score >= 6:
        return "WATCH"
    return "WAIT"


def _derive_status(brief: dict, action: str) -> str:
    if action in {"LONG ACTIVE", "SHORT ACTIVE"}:
        return "SETUP ACTIVE"
    score = float(brief.get("setup_score", {}).get("final") or 0.0)
    gate = bool(brief.get("setup_score", {}).get("trade_gate"))
    if (not gate) and score < 6:
        return "NO SETUP"
    return "WATCH"


def _scanner_row(symbol: str, payload: Optional[dict]) -> dict:
    if not payload:
        return {
            "symbol": symbol,
            "status": "PENDING",
            "action": "WAIT",
            "gate_open": False,
            "score": None,
            "setup_class": "PENDING",
            "trigger_distance_pct": None,
            "price": None,
            "updated_at": None,
            "freshness_sec": None,
        }
    brief = payload.get("data", {})
    action = _derive_action(brief)
    status = _derive_status(brief, action)
    ts = float(payload.get("timestamp", 0.0))
    now = time.time()
    return {
        "symbol": symbol,
        "status": status,
        "action": action,
        "gate_open": bool(brief.get("setup_score", {}).get("trade_gate")),
        "score": brief.get("setup_score", {}).get("final"),
        "setup_class": brief.get("setup_score", {}).get("class", "PENDING"),
        "trigger_distance_pct": brief.get("critical_level_distance_pct"),
        "price": brief.get("price"),
        "updated_at": ts if ts > 0 else None,
        "freshness_sec": max(0, int(now - ts)) if ts > 0 else None,
    }


def _scanner_sort_key(row: dict) -> tuple:
    gate_rank = 0 if row.get("gate_open") else 1
    action = row.get("action")
    action_rank = 0 if action in {"LONG ACTIVE", "SHORT ACTIVE"} else 1 if action == "WATCH" else 2
    score = float(row["score"]) if row.get("score") is not None else -1.0
    dist = row.get("trigger_distance_pct")
    dist_rank = abs(float(dist)) if dist is not None else 999.0
    freshness = row.get("freshness_sec")
    fresh_rank = freshness if freshness is not None else 999999
    return (gate_rank, action_rank, -score, dist_rank, fresh_rank, row.get("symbol", ""))


def _scanner_summary(rows: list[dict]) -> dict:
    open_gates = sum(1 for row in rows if row.get("gate_open"))
    near_trigger = sum(
        1
        for row in rows
        if row.get("trigger_distance_pct") is not None and abs(float(row["trigger_distance_pct"])) <= 0.35
    )
    active = sum(1 for row in rows if row.get("action") in {"LONG ACTIVE", "SHORT ACTIVE"})
    return {
        "universe_size": len(rows),
        "open_gates": open_gates,
        "near_trigger": near_trigger,
        "active_setups": active,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _get_or_compute_symbol(symbol: str, set_current: bool = False) -> dict:
    with _lock:
        payload = _cache["briefs"].get(symbol)
    if payload is not None:
        if set_current:
            with _lock:
                _cache["current_symbol"] = symbol
        return payload
    return _recalc_symbol(symbol, set_current=set_current)


def _scheduler_loop() -> None:
    logging.info("Scheduler started")
    while True:
        try:
            _refresh_scanner_fast(force=False)
        except Exception as exc:
            logging.warning("Fast scanner refresh failed: %s", exc)
        with _lock:
            interval = _cache["interval"]
            last = _cache["last_refresh"]
        if time.time() - last >= interval:
            try:
                with _lock:
                    current_symbol = str(_cache.get("current_symbol", "BTC/USDC"))
                _recalc_symbol(current_symbol, set_current=True)
            except Exception as exc:
                logging.warning("Scheduler refresh failed: %s", exc)
        time.sleep(1)


def _warmup_loop() -> None:
    # Non-blocking warmup: keep startup fast, prime only lightweight scanner rows.
    time.sleep(0.2)
    try:
        _refresh_scanner_fast(force=True)
    except Exception as exc:
        logging.warning("Warmup failed: %s", exc)


@app.on_event("startup")
def startup_event() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.info("Refresh interval: %ss", DEFAULT_INTERVAL)
    threading.Thread(target=_scheduler_loop, daemon=True).start()
    threading.Thread(target=_warmup_loop, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/brief")
def get_brief(symbol: Optional[str] = None) -> JSONResponse:
    normalized = _normalize_symbol(symbol)
    with _lock:
        current_symbol = str(_cache.get("current_symbol", "BTC/USDC"))
    target_symbol = normalized or current_symbol
    try:
        payload = _get_or_compute_symbol(target_symbol, set_current=bool(normalized))
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse(payload["data"])


@app.get("/api/scanner/list")
def get_scanner_list() -> JSONResponse:
    symbols = _ensure_universe()
    with _lock:
        briefs = dict(_cache["briefs"])
        fast_rows = dict(_cache.get("scanner_fast_rows", {}))
    rows = []
    for symbol in symbols:
        payload = briefs.get(symbol)
        if payload:
            rows.append(_scanner_row(symbol, payload))
        else:
            fast_row = fast_rows.get(symbol)
            if fast_row:
                row = dict(fast_row)
                if row.get("updated_at"):
                    row["freshness_sec"] = max(0, int(time.time() - float(row["updated_at"])))
                rows.append(row)
            else:
                rows.append(_scanner_row(symbol, None))
    rows.sort(key=_scanner_sort_key)
    return JSONResponse({"rows": rows, "summary": _scanner_summary(rows)})


@app.get("/api/scanner/summary")
def get_scanner_summary() -> JSONResponse:
    symbols = _ensure_universe()
    with _lock:
        briefs = dict(_cache["briefs"])
        fast_rows = dict(_cache.get("scanner_fast_rows", {}))
    rows = []
    for symbol in symbols:
        if symbol in briefs:
            rows.append(_scanner_row(symbol, briefs.get(symbol)))
        elif symbol in fast_rows:
            row = dict(fast_rows[symbol])
            if row.get("updated_at"):
                row["freshness_sec"] = max(0, int(time.time() - float(row["updated_at"])))
            rows.append(row)
        else:
            rows.append(_scanner_row(symbol, None))
    return JSONResponse(_scanner_summary(rows))


@app.post("/api/refresh")
async def refresh_now(request: Request) -> JSONResponse:
    symbol = None
    try:
        body = await request.json()
        symbol = body.get("symbol")
    except Exception:
        symbol = None
    normalized = _normalize_symbol(symbol)
    try:
        if normalized:
            _recalc_symbol(normalized, set_current=True)
        else:
            with _lock:
                current_symbol = str(_cache.get("current_symbol", "BTC/USDC"))
            _recalc_symbol(current_symbol, set_current=True)
        return JSONResponse({"status": "ok", "ts": time.time(), "symbol": normalized})
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
