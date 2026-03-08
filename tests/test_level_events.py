from __future__ import annotations

import pandas as pd

from src.market_structure.level_events import (
    detect_break_support,
    detect_reclaim,
    detect_sweep_support,
)


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_sweep_detected():
    candles = _df(
        [
            {"low": 99.0, "close": 101.0, "volume": 100},
        ]
    )
    event = detect_sweep_support(
        candles,
        level=100.0,
        atr=5.0,
        min_sweep_pct=0.001,
        atr_multiplier=0.2,
    )
    assert event is not None
    assert event["event"] == "sweep"


def test_sweep_reclaim_confirmed():
    candles = _df(
        [
            {"low": 99.0, "close": 101.0, "volume": 100},
            {"low": 100.5, "close": 100.2, "volume": 100},
        ]
    )
    sweep = detect_sweep_support(
        candles.iloc[:1],
        level=100.0,
        atr=5.0,
        min_sweep_pct=0.001,
        atr_multiplier=0.2,
    )
    reclaim = detect_reclaim(
        candles.iloc[1:],
        level=100.0,
        reclaim_confirmation_bars=1,
    )
    assert sweep is not None
    assert reclaim is not None


def test_break_confirmed_two_closes():
    candles = _df(
        [
            {"low": 99.5, "close": 99.8, "volume": 100},
            {"low": 98.0, "close": 99.5, "volume": 100},
        ]
    )
    event = detect_break_support(
        candles,
        level=100.0,
        atr=5.0,
        min_sweep_pct=0.001,
        atr_multiplier=0.2,
        breakout_volume_multiplier=1.2,
        volume_avg=100,
    )
    assert event is not None
    assert event["event"] == "break_confirmed"


def test_small_wick_ignored():
    candles = _df(
        [
            {"low": 99.95, "close": 100.1, "volume": 100},
        ]
    )
    event = detect_sweep_support(
        candles,
        level=100.0,
        atr=10.0,
        min_sweep_pct=0.001,
        atr_multiplier=0.2,
    )
    assert event is None
