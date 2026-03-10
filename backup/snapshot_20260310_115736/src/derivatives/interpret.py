from __future__ import annotations

from .models import DerivativesSnapshot


def interpret_funding(snapshot: DerivativesSnapshot) -> str:
    f = snapshot.funding_current_pct
    if abs(f) < 0.005:
        return "neutre"
    if f > 0.03:
        return "trop long"
    if f < -0.03:
        return "trop short"
    return "léger biais"


def interpret_oi(snapshot: DerivativesSnapshot) -> str:
    if snapshot.oi_change_4h_pct is None:
        return "OI (delta non dispo)"
    return "OI monte" if snapshot.oi_change_4h_pct > 0 else "OI baisse"


def interpret_squeeze_risk(snapshot: DerivativesSnapshot) -> str:
    funding_bias = interpret_funding(snapshot)
    oi_trend = interpret_oi(snapshot)
    if funding_bias in {"trop long", "trop short"} and oi_trend == "OI monte":
        return "élevé"
    if funding_bias in {"trop long", "trop short"}:
        return "moyen"
    return "faible"


def synthese(snapshot: DerivativesSnapshot) -> str:
    levier = interpret_funding(snapshot)
    oi = interpret_oi(snapshot)
    squeeze = interpret_squeeze_risk(snapshot)
    return f"Levier: {levier} | {oi} | Risque de squeeze: {squeeze}"
