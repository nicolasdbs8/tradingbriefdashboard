from __future__ import annotations

import logging
import os
import time
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
try:
    from dotenv import load_dotenv
except ImportError:  # optional dependency for local .env usage
    load_dotenv = None

from src.broker.kraken_account import KrakenAccountClient
from src.capital import compute_capital_plan
from src.config import load_config
from src.data import fetch_ohlcv
from src.derivatives.binance_futures import BinanceFuturesDerivativesClient
from src.derivatives.bybit_v5 import BybitV5DerivativesClient
from src.execution.cost_model import ExecutionCostAssumptions
from src.execution.kraken_costs import KrakenFeeClient
from src.indicators import atr, ema, ema_slope, rsi, vwap_intraday
from src.market_structure.level_events import (
    detect_break_support,
    detect_reclaim,
    detect_sweep_support,
)
from src.report import BriefReport, build_metrics, build_brief_data, format_brief


_CACHE: Dict[str, Any] = {
    "ohlcv": {},  # key: (symbol, exchange, timeframe) -> (ts, df)
    "kraken_balance": (0.0, 0.0),
    "fees": (0.0, None),
    "derivatives": (0.0, None),
    "sr_overrides": {"mtime": 0.0, "data": {}},
}

if load_dotenv:
    load_dotenv()

MIN_OHLCV_REFRESH_SEC = 60
MIN_BALANCE_REFRESH_SEC = 120
MIN_DERIV_REFRESH_SEC = 60
MIN_FEES_REFRESH_SEC = 300


def _nearest_level(levels: list[float], price: float) -> tuple[float | None, float]:
    if not levels or price == 0:
        return None, 0.0
    closest = min(levels, key=lambda lvl: abs(price - lvl))
    dist_pct = abs(price - closest) / closest if closest else 0.0
    return closest, dist_pct


def _nearest_level_from_groups(level_groups: dict[str, list[float]], price: float) -> tuple[str | None, float | None, float]:
    best_tf = None
    best_level = None
    best_dist = 9999.0
    for timeframe, levels in level_groups.items():
        level, dist = _nearest_level(levels, price)
        if level is None:
            continue
        if dist < best_dist:
            best_tf = timeframe
            best_level = level
            best_dist = dist
    return best_tf, best_level, best_dist


def _nearest_level_below_from_groups(
    level_groups: dict[str, list[float]], price: float
) -> tuple[str | None, float | None, float]:
    best_tf = None
    best_level = None
    best_dist = 9999.0
    for timeframe, levels in level_groups.items():
        below = [float(lvl) for lvl in levels if float(lvl) <= price]
        if not below:
            continue
        level = max(below)
        dist = abs(price - level) / level if level else 0.0
        if dist < best_dist:
            best_tf = timeframe
            best_level = level
            best_dist = dist
    return best_tf, best_level, best_dist


def _compute_indicators(
    df: pd.DataFrame,
    ema_fast_period: int,
    ema_slow_period: int,
    rsi_period: int,
    atr_period: int,
    volume_sma_period: int,
    ema_slope_bars: int,
    include_vwap: bool,
) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast_period)
    df["ema_slow"] = ema(df["close"], ema_slow_period)
    df["ema_slope"] = ema_slope(df["ema_fast"], ema_slope_bars)
    df["rsi"] = rsi(df["close"], rsi_period)
    df["atr"] = atr(df, atr_period)
    if include_vwap:
        df["vwap"] = vwap_intraday(df)
    df["volume_sma"] = df["volume"].rolling(volume_sma_period).mean()
    return df


def _should_refresh(last_ts: float, min_interval: int) -> bool:
    return time.time() - last_ts >= min_interval


def _normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace("-", "/").replace("_", "/")
    if "/" in raw:
        return raw
    for quote in ("USDC", "USDT"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[: -len(quote)]}/{quote}"
    return raw


def _load_sr_overrides(path: str = "config/sr_overrides.json") -> Dict[str, Dict[str, list[float]]]:
    p = Path(path)
    cache = _CACHE["sr_overrides"]
    if not p.exists():
        cache["mtime"] = 0.0
        cache["data"] = {}
        return {}
    mtime = p.stat().st_mtime
    if cache["data"] and cache["mtime"] == mtime:
        return cache["data"]
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.warning("SR overrides parse failed: %s", exc)
        return cache["data"] or {}
    normalized: Dict[str, Dict[str, list[float]]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        symbol_key = _normalize_symbol(str(key))
        one_d = value.get("1d") or []
        four_h = value.get("4h") or []
        normalized[symbol_key] = {
            "1d": [float(v) for v in one_d if isinstance(v, (int, float))],
            "4h": [float(v) for v in four_h if isinstance(v, (int, float))],
        }
    cache["mtime"] = mtime
    cache["data"] = normalized
    return normalized


def _auto_levels_from_df(
    df: pd.DataFrame,
    tolerance_pct: float,
    max_levels: int = 14,
) -> list[float]:
    if df is None or df.empty:
        return []

    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()
    closes = df["close"].astype(float)
    n = len(df)
    if n < 12:
        return []

    window = 3 if n >= 80 else 2
    candidates: list[tuple[float, float]] = []

    for i in range(window, n - window):
        h_slice = highs[i - window : i + window + 1]
        l_slice = lows[i - window : i + window + 1]
        recency_boost = 1.0 + (i / n) * 0.7
        if highs[i] >= h_slice.max():
            candidates.append((float(highs[i]), recency_boost))
        if lows[i] <= l_slice.min():
            candidates.append((float(lows[i]), recency_boost))

    # Add broad distribution anchors to stabilize sparse pivot sets.
    for q in (0.15, 0.3, 0.5, 0.7, 0.85):
        try:
            candidates.append((float(closes.quantile(q)), 0.45))
        except Exception:
            continue

    clusters: list[dict[str, float]] = []
    tol = max(0.001, float(tolerance_pct))
    for level, strength in sorted(candidates, key=lambda item: item[0]):
        if level <= 0:
            continue
        if not clusters:
            clusters.append({"level": level, "strength": strength})
            continue
        prev = clusters[-1]
        dist = abs(level - prev["level"]) / max(prev["level"], 1e-9)
        if dist <= tol:
            total = prev["strength"] + strength
            prev["level"] = (prev["level"] * prev["strength"] + level * strength) / total
            prev["strength"] = total
        else:
            clusters.append({"level": level, "strength": strength})

    if not clusters:
        return []

    strongest = sorted(clusters, key=lambda c: c["strength"], reverse=True)[:max_levels]
    return sorted(round(float(c["level"]), 8) for c in strongest)


def _get_cached_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int,
    exchange: str,
    fallback_exchange: str,
    include_vwap: bool,
    cfg,
) -> pd.DataFrame:
    cache = _CACHE["ohlcv"]
    cache_key = (symbol, exchange, timeframe)
    last_ts, last_df = cache.get(cache_key, (0.0, None))
    if last_df is not None and not _should_refresh(last_ts, MIN_OHLCV_REFRESH_SEC):
        return last_df
    df = fetch_ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        limit=limit,
        exchange_name=exchange,
        fallback_exchange=fallback_exchange,
    )
    df = _compute_indicators(
        df=df,
        ema_fast_period=cfg.ema_fast,
        ema_slow_period=cfg.ema_slow,
        rsi_period=cfg.rsi,
        atr_period=cfg.atr,
        volume_sma_period=cfg.volume_sma,
        ema_slope_bars=cfg.ema_slope_bars,
        include_vwap=include_vwap,
    )
    cache[cache_key] = (time.time(), df)
    return df


def _get_kraken_balance(api_key: str, api_secret: str) -> float:
    ts, cached = _CACHE["kraken_balance"]
    if cached and not _should_refresh(ts, MIN_BALANCE_REFRESH_SEC):
        return cached
    if not api_key or not api_secret:
        logging.warning("Kraken API credentials missing. Capital set to 0.")
        return 0.0
    usdc = KrakenAccountClient(api_key=api_key, api_secret=api_secret).get_usdc_equity()
    if usdc <= 0:
        logging.warning("Kraken returned 0 USD stable balance.")
    _CACHE["kraken_balance"] = (time.time(), usdc)
    return usdc


def _get_fees(api_key: str, api_secret: str, cfg) -> Tuple[float, float]:
    ts, cached = _CACHE["fees"]
    if cached and not _should_refresh(ts, MIN_FEES_REFRESH_SEC):
        return cached
    maker_fee = cfg.fallback_fee_maker
    taker_fee = cfg.fallback_fee_taker
    if api_key and api_secret:
        try:
            fee_client = KrakenFeeClient(api_key=api_key, api_secret=api_secret)
            maker_fee, taker_fee = fee_client.get_pair_fees(
                pair=cfg.kraken_pair,
                fallback=(cfg.fallback_fee_maker, cfg.fallback_fee_taker),
            )
        except Exception as exc:
            logging.warning("Kraken fee fetch failed, using fallback: %s", exc)
    _CACHE["fees"] = (time.time(), (maker_fee, taker_fee))
    return maker_fee, taker_fee


def _get_derivatives(cfg):
    ts, cached = _CACHE["derivatives"]
    if cached and not _should_refresh(ts, MIN_DERIV_REFRESH_SEC):
        return cached
    derivatives_snapshot = None
    try:
        client = BinanceFuturesDerivativesClient()
        derivatives_snapshot = client.fetch_snapshot(
            oi_symbol=cfg.futures_oi_symbol,
            funding_symbol=cfg.futures_funding_symbol,
        )
    except Exception as exc:
        logging.warning("Binance derivatives fetch failed: %s", exc)
        if cfg.derivatives_fallback_provider == "bybit":
            try:
                client = BybitV5DerivativesClient(base_url=cfg.bybit_base_url)
                derivatives_snapshot = client.fetch_snapshot(
                    category=cfg.bybit_category,
                    symbol=cfg.bybit_symbol,
                )
            except Exception as fallback_exc:
                logging.warning("Bybit derivatives fetch failed: %s", fallback_exc)
    _CACHE["derivatives"] = (time.time(), derivatives_snapshot)
    return derivatives_snapshot


def generate_trading_brief(
    config_path: str = "config.yaml",
    symbol: Optional[str] = None,
    exchange: Optional[str] = None,
) -> Dict[str, Any]:
    cfg = load_config(config_path)

    symbol = symbol or cfg.symbol
    exchange = exchange or cfg.exchange
    symbol_norm = _normalize_symbol(symbol)
    default_norm = _normalize_symbol(cfg.symbol)
    sr_overrides = _load_sr_overrides()
    levels_mode = "config"
    if symbol_norm in sr_overrides:
        override_1d = sr_overrides[symbol_norm].get("1d", [])
        override_4h = sr_overrides[symbol_norm].get("4h", [])
        # Ignore empty overrides to avoid wiping configured HTF levels.
        if override_1d or override_4h:
            cfg.levels = {
                "1d": override_1d,
                "4h": override_4h,
            }
            levels_mode = "manual_override"
    elif symbol_norm != default_norm:
        # Auto-build levels for non-default symbols when no manual override is provided.
        cfg.levels = {"1d": [], "4h": []}
        levels_mode = "auto_pending"

    metrics = {}
    dfs = {}
    used_exchange = exchange
    for timeframe in cfg.timeframes:
        limit = cfg.lookback.get(timeframe, 500)
        df = _get_cached_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            exchange=exchange,
            fallback_exchange=cfg.fallback_exchange,
            include_vwap=timeframe == "15m",
            cfg=cfg,
        )
        used_exchange = df["exchange"].iloc[-1]
        dfs[timeframe] = df
        metrics[timeframe] = build_metrics(timeframe=timeframe, df=df)

    if levels_mode == "auto_pending":
        lvl_1d = _auto_levels_from_df(
            dfs.get("1d"),
            tolerance_pct=float(cfg.levels_tolerance_pct.get("1d", 0.01)),
            max_levels=14,
        )
        lvl_4h = _auto_levels_from_df(
            dfs.get("4h"),
            tolerance_pct=float(cfg.levels_tolerance_pct.get("4h", 0.006)),
            max_levels=14,
        )
        cfg.levels = {"1d": lvl_1d, "4h": lvl_4h}
        levels_mode = "auto_generated"

    triggers = {}
    if "15m" in dfs:
        df15 = dfs["15m"]
        last = df15.iloc[-1]
        recent_high = df15["high"].iloc[-21:-1].max()
        recent_low = df15["low"].iloc[-21:-1].min()
        atr_val = float(last["atr"])
        buffer = atr_val * 0.1 if atr_val > 0 else 0.0
        breakout_level = float(recent_high)
        sweep_level = float(recent_low)
        breakout_now = float(last["close"]) > breakout_level + buffer
        retest_now = float(last["low"]) <= breakout_level and float(last["close"]) >= breakout_level
        sweep_reclaim_now = float(last["low"]) < sweep_level - buffer and float(last["close"]) > sweep_level
        vwap_retest = False
        if "vwap" in df15.columns and pd.notna(last["vwap"]):
            vwap_val = float(last["vwap"])
            vwap_retest = float(last["low"]) <= vwap_val <= float(last["high"])
        volume_breakout = False
        if float(last["volume_sma"]) > 0:
            volume_breakout = float(last["volume"]) > float(last["volume_sma"]) * 1.5

        triggers = {
            "breakout_level": breakout_level,
            "sweep_level": sweep_level,
            "breakout_now": breakout_now,
            "retest_now": retest_now,
            "sweep_reclaim_now": sweep_reclaim_now,
            "vwap_retest": vwap_retest,
            "volume_breakout": volume_breakout,
            "levels_mode": levels_mode,
        }

        h1_metrics = metrics.get("1h")
        critical_level_short = (h1_metrics.recent_low if h1_metrics and h1_metrics.recent_low else float(last["close"]))
        htf_levels = {
            "1d": cfg.levels.get("1d", []),
            "4h": cfg.levels.get("4h", []),
        }
        level_tf, nearest_htf_level, nearest_htf_dist_pct = _nearest_level_from_groups(
            htf_levels, float(last["close"])
        )
        critical_level_short_source = "1h"
        if nearest_htf_level is not None and nearest_htf_dist_pct <= cfg.critical_level_daily_threshold_pct:
            critical_level_short = nearest_htf_level
            critical_level_short_source = level_tf or "1h"

        # In bullish breakout regime, anchor longs to the closest reclaimed HTF support below price.
        critical_level_long = breakout_level
        critical_level_long_source = "15m_breakout"
        level_tf_below, nearest_htf_below, nearest_htf_below_dist_pct = _nearest_level_below_from_groups(
            htf_levels, float(last["close"])
        )
        if nearest_htf_below is not None and nearest_htf_below_dist_pct <= cfg.critical_level_daily_threshold_pct:
            critical_level_long = nearest_htf_below
            critical_level_long_source = level_tf_below or "15m_breakout"

        daily_metrics = metrics.get("1d")
        h4_metrics = metrics.get("4h")
        daily_up = bool(
            daily_metrics
            and daily_metrics.price > daily_metrics.ema_fast > daily_metrics.ema_slow
        )
        h4_up = bool(
            h4_metrics
            and h4_metrics.price > h4_metrics.ema_fast > h4_metrics.ema_slow
        )
        bullish_breakout_regime = daily_up and h4_up and (
            breakout_now or retest_now or float(last["close"]) >= critical_level_long
        )
        critical_level = critical_level_long if bullish_breakout_regime else critical_level_short
        critical_level_source = (
            critical_level_long_source if bullish_breakout_regime else critical_level_short_source
        )
        critical_regime = "bullish_breakout" if bullish_breakout_regime else "range_pullback"
        atr_val = float(last["atr"]) if "atr" in df15.columns else 0.0
        reclaim = None
        sweep = None
        confirm_bars = max(1, cfg.sweep_reclaim_confirmation_bars)
        if len(df15) >= confirm_bars + 1:
            sweep = detect_sweep_support(
                df15.iloc[: -confirm_bars],
                critical_level,
                atr_val,
                min_sweep_pct=cfg.sweep_min_sweep_pct,
                atr_multiplier=cfg.sweep_atr_multiplier,
            )
            if sweep:
                reclaim = detect_reclaim(
                    df15.tail(confirm_bars),
                    critical_level,
                    reclaim_confirmation_bars=confirm_bars,
                )
        break_confirmed = detect_break_support(
            df15,
            critical_level,
            atr=atr_val,
            min_sweep_pct=cfg.sweep_min_sweep_pct,
            atr_multiplier=cfg.sweep_atr_multiplier,
            breakout_volume_multiplier=cfg.sweep_breakout_volume_multiplier,
            volume_avg=float(df15["volume_sma"].iloc[-1]) if "volume_sma" in df15.columns else None,
        )

        active_event = "none"
        if sweep and reclaim:
            active_event = "sweep_reclaim"
        elif break_confirmed:
            active_event = "break"

        inversion_bars = max(1, cfg.sweep_inversion_confirmation_bars)
        long_inversion_confirmed = False
        short_inversion_confirmed = False
        if len(df15) >= inversion_bars:
            recent_closes = df15["close"].tail(inversion_bars).astype(float)
            long_inversion_confirmed = bool((recent_closes > critical_level).all())
            short_inversion_confirmed = bool((recent_closes < critical_level).all())

        triggers.update(
            {
                "critical_level": critical_level,
                "critical_level_source": critical_level_source,
                "critical_level_long": critical_level_long,
                "critical_level_long_source": critical_level_long_source,
                "critical_level_short": critical_level_short,
                "critical_level_short_source": critical_level_short_source,
                "critical_regime": critical_regime,
                "sweep_detected": bool(sweep),
                "reclaim_confirmed": bool(reclaim),
                "break_confirmed": bool(break_confirmed),
                "active_event": active_event,
                "inversion_confirmation_bars": inversion_bars,
                "long_inversion_confirmed": long_inversion_confirmed,
                "short_inversion_confirmed": short_inversion_confirmed,
            }
        )

    api_key = os.getenv("KRAKEN_API_KEY", "")
    api_secret = os.getenv("KRAKEN_API_SECRET", "")
    usdc_equity = _get_kraken_balance(api_key, api_secret)
    capital_plan = compute_capital_plan(usdc_equity)

    maker_fee, taker_fee = _get_fees(api_key, api_secret, cfg)
    if cfg.fee_mode == "maker":
        entry_fee = maker_fee
        exit_fee = maker_fee
    elif cfg.fee_mode == "mixed":
        entry_fee = maker_fee
        exit_fee = taker_fee
    else:
        entry_fee = taker_fee
        exit_fee = taker_fee

    costs = ExecutionCostAssumptions(
        entry_fee_rate=entry_fee,
        exit_fee_rate=exit_fee,
        slippage_rate=cfg.slippage_rate,
        mode=cfg.fee_mode,
    )

    derivatives_snapshot = _get_derivatives(cfg)

    report = BriefReport(
        symbol=symbol,
        exchange=used_exchange,
        metrics=metrics,
        capital=capital_plan,
        levels=cfg.levels,
        levels_tolerance_pct=cfg.levels_tolerance_pct,
        heatmap_name=cfg.heatmap_name,
        heatmap_note=cfg.heatmap_note,
        triggers=triggers,
        costs=costs,
        max_cost_to_stop_ratio=cfg.max_cost_to_stop_ratio,
        min_rr_net=cfg.min_rr_net,
        cost_gate_enabled=cfg.cost_gate_enabled,
        vwap_gate_enabled=cfg.vwap_gate_enabled,
        probability_gate_enabled=cfg.probability_gate_enabled,
        probability_gate_trigger_min=cfg.probability_gate_trigger_min,
        probability_gate_heads_up_min=cfg.probability_gate_heads_up_min,
        level_source_weight_enabled=cfg.level_source_weight_enabled,
        level_source_weights=cfg.level_source_weights,
        liquidity_gate_enabled=cfg.liquidity_gate_enabled,
        liquidity_gate_max_distance_pct=cfg.liquidity_gate_max_distance_pct,
        probability_engine_enabled=cfg.probability_engine_enabled,
        probability_engine_weights=cfg.probability_engine_weights,
        probability_engine_adjustments=cfg.probability_engine_adjustments,
        setup_preset_name=cfg.setup_preset_name,
        setup_entry_mode=cfg.setup_entry_mode,
        setup_retest_buffer_atr=cfg.setup_retest_buffer_atr,
        setup_stop_atr_mult=cfg.setup_stop_atr_mult,
        setup_target_timeframe=cfg.setup_target_timeframe,
        derivatives=derivatives_snapshot,
    )

    return {
        "text": format_brief(report),
        "data": build_brief_data(report, dfs),
        "timestamp": time.time(),
    }
