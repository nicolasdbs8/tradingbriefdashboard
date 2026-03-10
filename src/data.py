from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import ccxt
import pandas as pd


def _make_exchange(name: str) -> ccxt.Exchange:
    exchange_class = getattr(ccxt, name)
    return exchange_class({"enableRateLimit": True})


def _timeframe_to_seconds(timeframe: str) -> int:
    unit = timeframe[-1]
    qty = int(timeframe[:-1])
    if unit == "m":
        return qty * 60
    if unit == "h":
        return qty * 3600
    if unit == "d":
        return qty * 86400
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def _drop_open_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    if df.empty:
        return df
    tf_sec = _timeframe_to_seconds(timeframe)
    last_ts = df.index[-1].to_pydatetime()
    now = datetime.now(timezone.utc)
    age_sec = (now - last_ts).total_seconds()
    if age_sec < tf_sec:
        return df.iloc[:-1]
    return df


def fetch_ohlcv(
    symbol: str,
    timeframe: str,
    limit: int,
    exchange_name: str,
    fallback_exchange: Optional[str] = None,
) -> pd.DataFrame:
    exchanges_to_try = [exchange_name]
    if fallback_exchange:
        exchanges_to_try.append(fallback_exchange)

    last_error: Optional[Exception] = None
    for name in exchanges_to_try:
        try:
            ex = _make_exchange(name)
            if not ex.has.get("fetchOHLCV", False):
                raise RuntimeError(f"{name} ne supporte pas fetchOHLCV")
            raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(
                raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df.set_index("timestamp", inplace=True)
            df = df.sort_index()
            df = _drop_open_candle(df, timeframe)
            if df.empty:
                raise RuntimeError(f"{name} returned only open candle for {timeframe}")
            df["exchange"] = name
            return df
        except Exception as exc:
            logging.warning("Fetch OHLCV failed on %s: %s", name, exc)
            last_error = exc

    raise RuntimeError("Fetch OHLCV failed") from last_error
