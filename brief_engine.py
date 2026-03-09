from __future__ import annotations

import logging
import os
import time
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
    "ohlcv": {},  # key: timeframe -> (ts, df)
    "kraken_balance": (0.0, 0.0),
    "fees": (0.0, None),
    "derivatives": (0.0, None),
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
    last_ts, last_df = cache.get(timeframe, (0.0, None))
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
    cache[timeframe] = (time.time(), df)
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
        }

        h1_metrics = metrics.get("1h")
        critical_level = (h1_metrics.recent_low if h1_metrics and h1_metrics.recent_low else float(last["close"]))
        daily_levels = cfg.levels.get("1d", [])
        daily_level, daily_dist_pct = _nearest_level(daily_levels, float(last["close"]))
        if daily_level is not None and daily_dist_pct <= cfg.critical_level_daily_threshold_pct:
            critical_level = daily_level
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

        triggers.update(
            {
                "critical_level": critical_level,
                "sweep_detected": bool(sweep),
                "reclaim_confirmed": bool(reclaim),
                "break_confirmed": bool(break_confirmed),
                "active_event": active_event,
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
