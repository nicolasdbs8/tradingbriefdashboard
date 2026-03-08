from __future__ import annotations

import logging
from typing import Optional

import ccxt
import pandas as pd


def _make_exchange(name: str) -> ccxt.Exchange:
    exchange_class = getattr(ccxt, name)
    return exchange_class({"enableRateLimit": True})


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
            df["exchange"] = name
            return df
        except Exception as exc:
            logging.warning("Fetch OHLCV failed on %s: %s", name, exc)
            last_error = exc

    raise RuntimeError("Fetch OHLCV failed") from last_error
