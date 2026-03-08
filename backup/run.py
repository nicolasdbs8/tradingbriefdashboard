from __future__ import annotations

import argparse
import logging
import os

import pandas as pd

from .broker.kraken_account import KrakenAccountClient
from .capital import compute_capital_plan
from .config import load_config
from .data import fetch_ohlcv
from .derivatives.binance_futures import BinanceFuturesDerivativesClient
from .derivatives.bybit_v5 import BybitV5DerivativesClient
from .indicators import atr, ema, ema_slope, rsi, vwap_intraday
from .report import BriefReport, build_metrics, format_brief


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trading Brief Engine (MVP)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--symbol", help="Override symbol (e.g., BTC/USDC)")
    parser.add_argument("--exchange", help="Override exchange (e.g., binance)")
    return parser.parse_args()


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


def main() -> int:
    args = _parse_args()
    cfg = load_config(args.config)

    logging.basicConfig(level=cfg.log_level, format="%(levelname)s %(message)s")

    symbol = args.symbol or cfg.symbol
    exchange = args.exchange or cfg.exchange
    metrics = {}
    dfs = {}
    used_exchange = exchange
    for timeframe in cfg.timeframes:
        limit = cfg.lookback.get(timeframe, 500)
        logging.info("Fetching %s %s on %s", symbol, timeframe, exchange)
        df = fetch_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
            exchange_name=exchange,
            fallback_exchange=cfg.fallback_exchange,
        )
        used_exchange = df["exchange"].iloc[-1]
        df = _compute_indicators(
            df=df,
            ema_fast_period=cfg.ema_fast,
            ema_slow_period=cfg.ema_slow,
            rsi_period=cfg.rsi,
            atr_period=cfg.atr,
            volume_sma_period=cfg.volume_sma,
            ema_slope_bars=cfg.ema_slope_bars,
            include_vwap=timeframe == "15m",
        )
        dfs[timeframe] = df
        metrics[timeframe] = build_metrics(timeframe=timeframe, df=df)

    # Triggers (15m)
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

    api_key = os.getenv("KRAKEN_API_KEY", "")
    api_secret = os.getenv("KRAKEN_API_SECRET", "")
    usdc_equity = 0.0
    if api_key and api_secret:
        usdc_equity = KrakenAccountClient(
            api_key=api_key,
            api_secret=api_secret,
        ).get_usdc_equity()
    else:
        logging.warning("Kraken API keys not set; USDC equity set to 0.")
    capital_plan = compute_capital_plan(usdc_equity)

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
        derivatives=derivatives_snapshot,
    )
    print(format_brief(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
