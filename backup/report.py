from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

import pandas as pd

from .capital import CapitalPlan
from .derivatives.models import DerivativesSnapshot
from .derivatives.interpret import synthese

@dataclass
class TimeframeMetrics:
    timeframe: str
    price: float
    ema_fast: float
    ema_slow: float
    ema_slope: float
    rsi: float
    atr: float
    volume: float
    volume_sma: float
    vwap: Optional[float] = None
    recent_high: Optional[float] = None
    recent_low: Optional[float] = None
    atr_series: Optional[pd.Series] = None


@dataclass
class BriefReport:
    symbol: str
    exchange: str
    metrics: Dict[str, TimeframeMetrics]
    capital: CapitalPlan
    levels: Dict[str, list[float]]
    levels_tolerance_pct: Dict[str, float]
    heatmap_name: str
    heatmap_note: str
    triggers: Dict[str, object]
    derivatives: Optional[DerivativesSnapshot] = None


def _vol_state(volume: float, volume_sma: float) -> str:
    if volume_sma <= 0:
        return "n/a"
    if volume > volume_sma * 1.5:
        return "fort"
    if volume < volume_sma * 0.7:
        return "faible"
    return "normal"


def _ema_relation(price: float, ema_fast: float, ema_slow: float) -> str:
    if price > ema_fast > ema_slow:
        return "prix > EMA50 > EMA200"
    if price < ema_fast < ema_slow:
        return "prix < EMA50 < EMA200"
    return "mixte"


def _conclusion(price: float, ema_fast: float, ema_slow: float, rsi: float) -> str:
    if price > ema_fast > ema_slow and rsi >= 55:
        return "bullish"
    if price < ema_fast < ema_slow and rsi <= 45:
        return "bearish"
    return "neutre"


def _structure(recent_high: float, recent_low: float, atr: float) -> str:
    if atr <= 0:
        return "n/a"
    rng = recent_high - recent_low
    if rng <= atr * 2.0:
        return "range"
    return "trend"


def _compression(atr_series: pd.Series) -> str:
    if len(atr_series) < 20:
        return "n/a"
    recent = atr_series.tail(10).mean()
    prev = atr_series.tail(20).head(10).mean()
    if prev == 0:
        return "n/a"
    return "oui" if recent < prev * 0.9 else "non"


def _atr_trend(atr_series: pd.Series) -> str:
    if len(atr_series) < 20:
        return "n/a"
    recent = atr_series.tail(10).mean()
    prev = atr_series.tail(20).head(10).mean()
    if prev == 0:
        return "n/a"
    return "hausse" if recent > prev * 1.1 else "baisse" if recent < prev * 0.9 else "stable"


def _bias(daily: TimeframeMetrics, h4: TimeframeMetrics) -> str:
    d = _conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)
    h = _conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)
    if d == "bullish" and h == "bullish":
        return "LONG"
    if d == "bearish" and h == "bearish":
        return "SHORT"
    return "RANGE"


def _scenario_levels(price: float, atr_val: float) -> Tuple[float, float]:
    if atr_val <= 0:
        return price, price
    return price + atr_val * 0.8, price - atr_val * 0.8


def _sizing(risk_amount: float, entry: float, stop: float) -> Tuple[float, float]:
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0, risk_amount
    position_size = risk_amount / stop_dist
    return position_size, risk_amount


def build_metrics(
    timeframe: str,
    df: pd.DataFrame,
    recent_window: int = 20,
) -> TimeframeMetrics:
    last = df.iloc[-1]
    recent = df.tail(recent_window)
    return TimeframeMetrics(
        timeframe=timeframe,
        price=float(last["close"]),
        ema_fast=float(last["ema_fast"]),
        ema_slow=float(last["ema_slow"]),
        ema_slope=float(last["ema_slope"]),
        rsi=float(last["rsi"]),
        atr=float(last["atr"]),
        volume=float(last["volume"]),
        volume_sma=float(last["volume_sma"]),
        vwap=float(last["vwap"]) if "vwap" in df.columns else None,
        recent_high=float(recent["high"].max()),
        recent_low=float(recent["low"].min()),
        atr_series=df["atr"],
    )


def format_brief(report: BriefReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    daily = report.metrics["1d"]
    h4 = report.metrics["4h"]
    h1 = report.metrics["1h"]
    m15 = report.metrics["15m"]

    h4_structure = _structure(h4.recent_high or 0, h4.recent_low or 0, h4.atr)
    h1_structure = _structure(h1.recent_high or 0, h1.recent_low or 0, h1.atr)
    h1_compression = _compression(h1.atr_series) if h1.atr_series is not None else "n/a"
    atr_trend = _atr_trend(h1.atr_series) if h1.atr_series is not None else "n/a"
    bias = _bias(daily, h4)
    vol_state = _vol_state(h1.volume, h1.volume_sma)
    momentum = f"Volume {vol_state}, ATR {atr_trend}"
    liq_above = h1.recent_high if h1.recent_high is not None else h1.price
    liq_below = h1.recent_low if h1.recent_low is not None else h1.price
    breakout_level, sweep_level = _scenario_levels(h1.price, h1.atr)

    # Simple trade candidates (non exécutés)
    if bias == "LONG":
        entry = h1.price
        stop = h1.price - h1.atr * 1.5
        target = h1.price + h1.atr * 3.0
    elif bias == "SHORT":
        entry = h1.price
        stop = h1.price + h1.atr * 1.5
        target = h1.price - h1.atr * 3.0
    else:
        entry = h1.price
        stop = h1.price - h1.atr * 1.0
        target = h1.price + h1.atr * 2.0

    position_size, risk_amount = _sizing(report.capital.risk_per_trade_usd, entry, stop)
    rr = abs((target - entry) / (entry - stop)) if (entry - stop) != 0 else 0.0

    vwap_side = "au-dessus" if m15.vwap and m15.price > m15.vwap else "en-dessous"
    vwap_retest = report.triggers.get("vwap_retest", False)
    volume_breakout = report.triggers.get("volume_breakout", False)
    breakout_level = report.triggers.get("breakout_level", m15.recent_high or m15.price)
    sweep_level = report.triggers.get("sweep_level", m15.recent_low or m15.price)
    breakout_now = report.triggers.get("breakout_now", False)
    retest_now = report.triggers.get("retest_now", False)
    sweep_reclaim_now = report.triggers.get("sweep_reclaim_now", False)

    daily_levels = report.levels.get("1d", [])
    h4_levels = report.levels.get("4h", [])

    daily_tol = report.levels_tolerance_pct.get("1d", 0.01)
    h4_tol = report.levels_tolerance_pct.get("4h", 0.006)
    h1_tol = report.levels_tolerance_pct.get("1h", 0.003)
    m15_tol = report.levels_tolerance_pct.get("15m", 0.002)

    daily_levels_str = ", ".join([f"{lvl:,.0f}±{daily_tol*100:.1f}%" for lvl in daily_levels])
    h4_levels_str = ", ".join([f"{lvl:,.0f}±{h4_tol*100:.1f}%" for lvl in h4_levels])

    return "\n".join(
        [
            f"TRADING BRIEF — {report.symbol}",
            f"Date: {now}",
            f"Exchange: {report.exchange}",
            "",
            "CHECKLIST ANALYSE",
            f"- Daily: Prix {daily.price:,.2f} | S/R: {daily_levels_str if daily_levels_str else 'niveaux non fournis'}",
            f"  EMA: {_ema_relation(daily.price, daily.ema_fast, daily.ema_slow)} | Pente EMA50 {daily.ema_slope:,.6f}",
            f"  RSI(14): {daily.rsi:,.2f} | ATR(14): {daily.atr:,.2f} | Conclusion: {_conclusion(daily.price, daily.ema_fast, daily.ema_slow, daily.rsi)}",
            f"- 4H: Structure: {h4_structure} | S/R: {h4_levels_str if h4_levels_str else 'niveaux non fournis'}",
            f"  EMA: {_ema_relation(h4.price, h4.ema_fast, h4.ema_slow)} | RSI(14): {h4.rsi:,.2f} | ATR(14): {h4.atr:,.2f} | Conclusion: {_conclusion(h4.price, h4.ema_fast, h4.ema_slow, h4.rsi)}",
            f"- 1H: Structure: {h1_structure} | High/Low récents: {h1.recent_high:,.2f} / {h1.recent_low:,.2f}",
            f"  S/R (réf 1D/4H): tol ±{h1_tol*100:.1f}% (pas de nouveaux niveaux)",
            f"  Compression: {h1_compression} | RSI(14): {h1.rsi:,.2f} | Volume: {_vol_state(h1.volume, h1.volume_sma)} | Conclusion: {_conclusion(h1.price, h1.ema_fast, h1.ema_slow, h1.rsi)}",
            f"- 15m: Prix {m15.price:,.2f} vs VWAP {m15.vwap:,.2f} ({vwap_side})",
            f"  S/R (réf 1D/4H): tol ±{m15_tol*100:.1f}% (pas de nouveaux niveaux)",
            f"  Retest VWAP: {'oui' if vwap_retest else 'non'} | Volume breakout: {'oui' if volume_breakout else 'non'} | ATR(14): {m15.atr:,.2f} | Conclusion: {_conclusion(m15.price, m15.ema_fast, m15.ema_slow, m15.rsi)}",
            "",
            "CHECKLIST DÉCISION",
            f"- Bias marché (Daily+4H): {bias}",
            f"- Liquidité probable: au-dessus {liq_above:,.2f} / sous {liq_below:,.2f}",
            f"- Momentum: {momentum}",
            f"- Scénarios:",
            f"  Si cassure > {breakout_level:,.2f} + retest -> setup",
            f"  Si sweep < {sweep_level:,.2f} + reclaim -> setup",
            f"- Paramètres trade (candidats, non exécutés): entrée {entry:,.2f} | stop {stop:,.2f} | target {target:,.2f}",
            f"- Position sizing: capital actif {report.capital.active_capital_usd:,.2f} | risk/trade {report.capital.risk_per_trade_usd:,.2f} | size {position_size:,.6f}",
            f"- Validation: RR={rr:,.2f} (>=2), éviter milieu range, stop logique",
            "",
            "CAPITAL",
            f"- Kraken USDC equity: {report.capital.total_equity_usd:,.2f}",
            f"- Active (40%): {report.capital.active_capital_usd:,.2f}",
            f"- Reserve (60%): {report.capital.reserve_capital_usd:,.2f}",
            f"- Risk/trade (0.5% of active): {report.capital.risk_per_trade_usd:,.2f}",
            "",
            f"DERIVATIVES ({report.derivatives.provider.upper()})"
            if report.derivatives
            else "DERIVATIVES",
            *(
                _format_derivatives(report.derivatives, report.heatmap_name)
                if report.derivatives
                else ["- Indisponible (API)"]
            ),
            "",
            "TRIGGERS (MVP)",
            f"- Breakout > {breakout_level:,.2f}: {'OUI' if breakout_now else 'non'} | Retest: {'OUI' if retest_now else 'non'}",
            f"- Sweep < {sweep_level:,.2f} + reclaim: {'OUI' if sweep_reclaim_now else 'non'}",
            f"- Heatmap: vérifier {report.heatmap_name} ({report.heatmap_note}) si trigger",
        ]
    )


def _format_derivatives(snapshot: DerivativesSnapshot, heatmap_name: str) -> list[str]:
    oi_change_str = "n/a"
    if snapshot.oi_change_1h_pct is not None:
        oi_change_str = (
            f"1h {snapshot.oi_change_1h_pct:,.2f}% | "
            f"4h {snapshot.oi_change_4h_pct:,.2f}% | "
            f"24h {snapshot.oi_change_24h_pct:,.2f}%"
        )
    return [
        f"- Mark price: {snapshot.mark_price:,.2f}",
        f"- Open interest: {snapshot.oi_contracts:,.2f} contracts (~{snapshot.oi_usd:,.2f} USD)",
        f"- OI change: {oi_change_str}",
        f"- Funding current: {snapshot.funding_current_pct:,.4f}%",
        f"- Funding 1D (approx): {snapshot.funding_1d_pct:,.4f}%",
        f"- Lecture: {synthese(snapshot)}",
        f"- Note: Heatmap liquidation à vérifier manuellement ({heatmap_name})",
    ]
