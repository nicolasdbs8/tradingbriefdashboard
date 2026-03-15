from __future__ import annotations

from typing import Optional, TYPE_CHECKING

import pandas as pd

from ..derivatives.models import DerivativesSnapshot

if TYPE_CHECKING:
    from ..report import TimeframeMetrics


def _build_factor(name: str, label: str, direction: str, weight: float, reason: str) -> dict:
    signed = 0.0
    if direction == "bullish":
        signed = abs(weight)
    elif direction == "bearish":
        signed = -abs(weight)
    return {
        "name": name,
        "label": label,
        "direction": direction,
        "weight": weight,
        "signed_score": signed,
        "reason": reason,
    }


def _conclusion(price: float, ema_fast: float, ema_slow: float, rsi: float) -> str:
    if price > ema_fast > ema_slow and rsi >= 55:
        return "bullish"
    if price < ema_fast < ema_slow and rsi <= 45:
        return "bearish"
    return "neutral"


def _range_location(price: float, low: float, high: float) -> str:
    if low >= high:
        return "n/a"
    if price < low:
        return "below range"
    if price > high:
        return "above range"
    pct = (price - low) / (high - low)
    if pct < 0.33:
        return "lower third"
    if pct < 0.66:
        return "middle third"
    return "upper third"


def _atr_trend(atr_series: Optional[pd.Series]) -> str:
    if atr_series is None or len(atr_series) < 20:
        return "n/a"
    recent = atr_series.tail(10).mean()
    prev = atr_series.tail(20).head(10).mean()
    if prev == 0:
        return "n/a"
    if recent > prev * 1.1:
        return "up"
    if recent < prev * 0.9:
        return "down"
    return "flat"


def score_htf_trend(daily: TimeframeMetrics, h4: TimeframeMetrics, weight: float) -> dict:
    daily_trend = _conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)
    h4_trend = _conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)
    if daily_trend == "bullish" and h4_trend == "bullish":
        return _build_factor("htf_trend", "HTF Trend", "bullish", weight, "Daily uptrend + 4H uptrend")
    if daily_trend == "bearish" and h4_trend == "bearish":
        return _build_factor("htf_trend", "HTF Trend", "bearish", weight, "Daily downtrend + 4H downtrend")
    if daily_trend != h4_trend:
        return _build_factor(
            "htf_trend",
            "HTF Trend",
            "neutral",
            weight,
            f"Daily {daily_trend} vs 4H {h4_trend}",
        )
    return _build_factor("htf_trend", "HTF Trend", "neutral", weight, "Mixed or neutral trend")


def score_location(h1: TimeframeMetrics, weight: float) -> dict:
    low = h1.recent_low or h1.price
    high = h1.recent_high or h1.price
    location = _range_location(h1.price, low, high)
    if location == "lower third":
        return _build_factor("location", "Location", "bullish", weight, "Lower third of range")
    if location == "upper third":
        return _build_factor("location", "Location", "bearish", weight, "Upper third of range")
    if location == "middle third":
        return _build_factor("location", "Location", "neutral", weight, "Middle of range")
    return _build_factor("location", "Location", "neutral", weight, "Range location unavailable")


def score_liquidity(h1: TimeframeMetrics, weight: float) -> dict:
    low = h1.recent_low or h1.price
    high = h1.recent_high or h1.price
    if low >= high:
        return _build_factor("liquidity", "Liquidity", "neutral", weight, "Range unavailable")
    dist_below = abs(h1.price - low)
    dist_above = abs(high - h1.price)
    if dist_below < dist_above:
        return _build_factor("liquidity", "Liquidity", "bearish", weight, "Liquidity closer below")
    if dist_above < dist_below:
        return _build_factor("liquidity", "Liquidity", "bullish", weight, "Liquidity closer above")
    return _build_factor("liquidity", "Liquidity", "neutral", weight, "Liquidity balanced")


def score_momentum(
    h1: TimeframeMetrics,
    m15: TimeframeMetrics,
    volume_breakout: bool,
    weight: float,
) -> dict:
    vwap_side = None
    if m15.vwap:
        vwap_side = "above" if m15.price > m15.vwap else "below"
    atr_trend = _atr_trend(h1.atr_series)
    if h1.rsi >= 60 and vwap_side == "above":
        reason = "RSI strong + price above VWAP"
        if volume_breakout:
            reason += " + volume breakout"
        if atr_trend == "up":
            reason += " + rising ATR"
        return _build_factor("momentum", "Momentum", "bullish", weight, reason)
    if h1.rsi <= 40 and vwap_side == "below":
        reason = "RSI weak + price below VWAP"
        if not volume_breakout:
            reason += " + weak volume"
        if atr_trend == "up":
            reason += " + rising ATR"
        return _build_factor("momentum", "Momentum", "bearish", weight, reason)
    return _build_factor("momentum", "Momentum", "neutral", weight, "Mixed momentum signals")


def score_derivatives(snapshot: Optional[DerivativesSnapshot], weight: float) -> dict:
    if snapshot is None:
        return _build_factor("derivatives", "Derivatives", "neutral", weight, "Derivatives data unavailable")
    if snapshot.funding_current_pct > 0.03:
        return _build_factor("derivatives", "Derivatives", "bearish", weight, "Funding very positive")
    if snapshot.funding_current_pct < -0.03:
        return _build_factor("derivatives", "Derivatives", "bullish", weight, "Funding very negative")
    oi1 = snapshot.oi_change_1h_pct
    oi4 = snapshot.oi_change_4h_pct
    if oi1 is not None and oi4 is not None:
        if oi1 > 0 and oi4 > 0:
            return _build_factor("derivatives", "Derivatives", "bullish", weight, "OI releveraging on 1h and 4h")
        if oi1 < 0 and oi4 < 0:
            return _build_factor("derivatives", "Derivatives", "neutral", weight, "OI deleveraging on 1h and 4h")
        return _build_factor("derivatives", "Derivatives", "neutral", weight, "OI mixed across 1h and 4h")
    if snapshot.oi_change_24h_pct is not None and snapshot.oi_change_24h_pct < 0:
        return _build_factor("derivatives", "Derivatives", "neutral", weight, "OI down on 24h, short-term trend unavailable")
    return _build_factor("derivatives", "Derivatives", "neutral", weight, "Neutral positioning")
