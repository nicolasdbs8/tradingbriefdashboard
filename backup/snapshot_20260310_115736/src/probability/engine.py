from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

from ..derivatives.models import DerivativesSnapshot

if TYPE_CHECKING:
    from ..report import TimeframeMetrics
from .factors import (
    score_derivatives,
    score_htf_trend,
    score_liquidity,
    score_location,
    score_momentum,
)


def _round_pct(value: float) -> float:
    return round(value, 1)


def _format_confidence(edge_strength: float) -> str:
    if edge_strength < 10:
        return "low"
    if edge_strength <= 20:
        return "moderate"
    return "high"


def _round_probabilities(long_pct: float) -> tuple[float, float]:
    long_rounded = _round_pct(long_pct)
    short_rounded = _round_pct(100 - long_rounded)
    return long_rounded, short_rounded


def compute_directional_probability(
    *,
    daily: TimeframeMetrics,
    h4: TimeframeMetrics,
    h1: TimeframeMetrics,
    m15: TimeframeMetrics,
    volume_breakout: bool,
    derivatives: Optional[DerivativesSnapshot],
    weights: Dict[str, float],
    event_flags: Optional[Dict[str, bool]] = None,
    adjustments: Optional[Dict[str, float]] = None,
) -> dict:
    factor_scores: List[dict] = [
        score_htf_trend(daily, h4, weights.get("htf_trend", 0)),
        score_location(h1, weights.get("location", 0)),
        score_liquidity(h1, weights.get("liquidity", 0)),
        score_momentum(h1, m15, volume_breakout, weights.get("momentum", 0)),
        score_derivatives(derivatives, weights.get("derivatives", 0)),
    ]

    active_factors = [f for f in factor_scores if f["weight"] > 0]
    total_weight = sum(f["weight"] for f in active_factors)
    signed_total_score = sum(f["signed_score"] for f in active_factors)

    if total_weight <= 0:
        ratio = 0.0
        long_pct_raw = 50.0
    else:
        ratio = signed_total_score / total_weight
        ratio = max(-1.0, min(1.0, ratio))
        long_pct_raw = (1 + ratio) / 2 * 100

    event_flags = event_flags or {}
    adjustments = adjustments or {}
    conditional_adjustments: list[str] = []
    long_adjust = 0.0
    if event_flags.get("sweep_detected"):
        delta = float(adjustments.get("sweep_detected_long_pct", 0))
        if delta:
            long_adjust += delta
            conditional_adjustments.append(f"+{delta:.0f} long (sweep detected)")
    if event_flags.get("break_confirmed"):
        delta = float(adjustments.get("break_confirmed_short_pct", 0))
        if delta:
            long_adjust -= delta
            conditional_adjustments.append(f"+{delta:.0f} short (break confirmed)")

    long_pct_raw = max(0.0, min(100.0, long_pct_raw + long_adjust))

    long_pct, short_pct = _round_probabilities(long_pct_raw)
    edge_strength = _round_pct(abs(long_pct - short_pct))
    edge = "bullish" if long_pct > short_pct else "bearish"

    return {
        "long_probability_pct": long_pct,
        "short_probability_pct": short_pct,
        "edge": edge,
        "edge_strength": edge_strength,
        "confidence": _format_confidence(edge_strength),
        "note": "Bias estimate based on current factors, not a prediction.",
        "conditional_adjustments": conditional_adjustments,
        "ratio": ratio,
        "total_weight": total_weight,
        "signed_total_score": signed_total_score,
        "factors": factor_scores,
    }
