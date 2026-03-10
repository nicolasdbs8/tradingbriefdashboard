from __future__ import annotations

from typing import Optional

import pandas as pd


def _get_last(candles: pd.DataFrame) -> pd.Series:
    return candles.iloc[-1]


def _get_prev(candles: pd.DataFrame) -> Optional[pd.Series]:
    if len(candles) < 2:
        return None
    return candles.iloc[-2]


def detect_sweep_support(
    candles: pd.DataFrame,
    level: float,
    atr: float,
    *,
    min_sweep_pct: float,
    atr_multiplier: float,
) -> Optional[dict]:
    if candles.empty or level <= 0:
        return None
    last = _get_last(candles)
    low = float(last["low"])
    close = float(last["close"])
    if low >= level:
        return None
    if close <= level:
        return None
    depth_pct = (level - low) / level
    depth_abs = level - low
    if depth_pct < min_sweep_pct and depth_abs < atr * atr_multiplier:
        return None
    return {
        "event": "sweep",
        "direction": "below",
        "level": level,
        "depth_pct": depth_pct,
    }


def detect_reclaim(
    candles: pd.DataFrame,
    level: float,
    *,
    reclaim_confirmation_bars: int,
) -> Optional[dict]:
    if candles.empty or level <= 0:
        return None
    bars = max(1, int(reclaim_confirmation_bars))
    if len(candles) < bars:
        return None
    closes = candles["close"].tail(bars)
    if (closes >= level).all():
        return {"event": "reclaim_confirmed", "level": level}
    return None


def detect_break_support(
    candles: pd.DataFrame,
    level: float,
    *,
    atr: float,
    min_sweep_pct: float,
    atr_multiplier: float,
    breakout_volume_multiplier: float,
    volume_avg: Optional[float] = None,
) -> Optional[dict]:
    if candles.empty or level <= 0:
        return None
    last = _get_last(candles)
    close = float(last["close"])
    if close >= level:
        return None
    depth_pct = (level - close) / level
    depth_abs = level - close
    if depth_pct < min_sweep_pct and depth_abs < atr * atr_multiplier:
        return None
    prev = _get_prev(candles)
    if prev is not None and float(prev["close"]) < level:
        return {"event": "break_confirmed", "direction": "below", "level": level}
    if volume_avg is None:
        volume_avg = float(candles["volume"].tail(20).mean()) if len(candles) >= 5 else None
    if volume_avg and volume_avg > 0:
        if float(last["volume"]) >= volume_avg * breakout_volume_multiplier:
            return {"event": "break_confirmed", "direction": "below", "level": level}
    return None
