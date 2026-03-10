from __future__ import annotations

from datetime import datetime, timezone

from src.derivatives.models import DerivativesSnapshot
from src.probability.engine import compute_directional_probability
from src.report import TimeframeMetrics


def _metrics(
    *,
    price: float,
    ema_fast: float,
    ema_slow: float,
    rsi: float,
    atr: float = 100.0,
    volume: float = 100.0,
    volume_sma: float = 100.0,
    vwap: float | None = None,
    recent_high: float | None = None,
    recent_low: float | None = None,
) -> TimeframeMetrics:
    return TimeframeMetrics(
        timeframe="na",
        price=price,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_slope=0.0,
        rsi=rsi,
        atr=atr,
        volume=volume,
        volume_sma=volume_sma,
        vwap=vwap,
        vwap_series=None,
        recent_high=recent_high,
        recent_low=recent_low,
        atr_series=None,
    )


def _snapshot(funding: float, oi_change_24h: float | None = None) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        provider="test",
        symbol="BTCUSDT",
        ts=datetime.now(timezone.utc),
        oi_contracts=1.0,
        oi_usd=1.0,
        mark_price=1.0,
        funding_current_pct=funding,
        funding_1d_pct=funding * 2,
        oi_change_24h_pct=oi_change_24h,
    )


def test_probability_bullish_strong():
    daily = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h4 = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h1 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=62, recent_low=90, recent_high=120)
    m15 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=55, vwap=90)
    weights = {"htf_trend": 3, "location": 2, "liquidity": 2, "momentum": 1, "derivatives": 1}

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=True,
        derivatives=_snapshot(-0.05),
        weights=weights,
        event_flags={"sweep_detected": True},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    assert result["long_probability_pct"] > result["short_probability_pct"]
    assert result["confidence"] == "high"


def test_probability_bearish_strong():
    daily = _metrics(price=90, ema_fast=100, ema_slow=110, rsi=40)
    h4 = _metrics(price=90, ema_fast=100, ema_slow=110, rsi=40)
    h1 = _metrics(price=115, ema_fast=110, ema_slow=120, rsi=38, recent_low=90, recent_high=120)
    m15 = _metrics(price=115, ema_fast=110, ema_slow=120, rsi=45, vwap=120)
    weights = {"htf_trend": 3, "location": 2, "liquidity": 2, "momentum": 1, "derivatives": 1}

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=False,
        derivatives=_snapshot(0.06),
        weights=weights,
        event_flags={"break_confirmed": True},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    assert result["short_probability_pct"] > result["long_probability_pct"]
    assert result["confidence"] == "high"


def test_probability_balanced_case():
    daily = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h4 = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h1 = _metrics(price=110, ema_fast=110, ema_slow=110, rsi=50, recent_low=90, recent_high=130)
    m15 = _metrics(price=110, ema_fast=110, ema_slow=110, rsi=50, vwap=110)
    weights = {"htf_trend": 1, "location": 1, "liquidity": 1, "momentum": 1, "derivatives": 1}

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=False,
        derivatives=_snapshot(0.0),
        weights=weights,
        event_flags={},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    assert result["long_probability_pct"] == 50.0
    assert result["short_probability_pct"] == 50.0
    assert result["confidence"] == "low"


def test_probability_neutral_weights():
    daily = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h4 = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h1 = _metrics(price=100, ema_fast=100, ema_slow=100, rsi=50, recent_low=90, recent_high=110)
    m15 = _metrics(price=100, ema_fast=100, ema_slow=100, rsi=50, vwap=100)

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=False,
        derivatives=None,
        weights={"htf_trend": 0, "location": 0, "liquidity": 0, "momentum": 0, "derivatives": 0},
        event_flags={},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    assert result["long_probability_pct"] == 50.0
    assert result["short_probability_pct"] == 50.0
    assert result["edge_strength"] == 0.0


def test_probability_sums_to_100():
    daily = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h4 = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h1 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=62, recent_low=90, recent_high=120)
    m15 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=55, vwap=90)
    weights = {"htf_trend": 3, "location": 2, "liquidity": 2, "momentum": 1, "derivatives": 1}

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=True,
        derivatives=_snapshot(-0.05),
        weights=weights,
        event_flags={"sweep_detected": True},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    assert result["long_probability_pct"] + result["short_probability_pct"] == 100.0


def test_factor_contributions():
    daily = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h4 = _metrics(price=110, ema_fast=100, ema_slow=90, rsi=60)
    h1 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=62, recent_low=90, recent_high=120)
    m15 = _metrics(price=95, ema_fast=100, ema_slow=90, rsi=55, vwap=90)
    weights = {"htf_trend": 3, "location": 2, "liquidity": 2, "momentum": 1, "derivatives": 1}

    result = compute_directional_probability(
        daily=daily,
        h4=h4,
        h1=h1,
        m15=m15,
        volume_breakout=True,
        derivatives=_snapshot(-0.05),
        weights=weights,
        event_flags={"sweep_detected": True},
        adjustments={"sweep_detected_long_pct": 8, "break_confirmed_short_pct": 8},
    )

    factors = {f["name"]: f for f in result["factors"]}
    assert factors["htf_trend"]["signed_score"] == 3
    assert factors["location"]["signed_score"] == 2
    assert factors["liquidity"]["signed_score"] == -2
