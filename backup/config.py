from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import yaml


@dataclass
class Config:
    exchange: str
    fallback_exchange: str
    symbol: str
    timeframes: List[str]
    lookback: Dict[str, int]
    ema_fast: int
    ema_slow: int
    rsi: int
    atr: int
    vwap_session: str
    volume_sma: int
    ema_slope_bars: int
    log_level: str
    levels_tolerance_pct: Dict[str, float]
    levels: Dict[str, list[float]]
    heatmap_name: str
    heatmap_note: str
    futures_oi_symbol: str
    futures_funding_symbol: str
    derivatives_fallback_provider: str
    bybit_base_url: str
    bybit_category: str
    bybit_symbol: str


def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    return Config(
        exchange=data["data"]["exchange"],
        fallback_exchange=data["data"]["fallback_exchange"],
        symbol=data["data"]["symbol"],
        timeframes=data["data"]["timeframes"],
        lookback=data["data"]["lookback"],
        ema_fast=data["indicators"]["ema_fast"],
        ema_slow=data["indicators"]["ema_slow"],
        rsi=data["indicators"]["rsi"],
        atr=data["indicators"]["atr"],
        vwap_session=data["indicators"]["vwap_session"],
        volume_sma=data["indicators"]["volume_sma"],
        ema_slope_bars=data["indicators"]["ema_slope_bars"],
        log_level=data["logging"]["level"],
        levels_tolerance_pct=data["report"]["levels_tolerance_pct"],
        levels=data["report"]["levels"],
        heatmap_name=data["report"]["heatmap"]["name"],
        heatmap_note=data["report"]["heatmap"]["note"],
        futures_oi_symbol=data["derivatives"]["binance_futures"]["oi_symbol"],
        futures_funding_symbol=data["derivatives"]["binance_futures"]["funding_symbol"],
        derivatives_fallback_provider=data["derivatives"]["fallback"]["provider"],
        bybit_base_url=data["derivatives"]["fallback"]["bybit"]["base_url"],
        bybit_category=data["derivatives"]["fallback"]["bybit"]["category"],
        bybit_symbol=data["derivatives"]["fallback"]["bybit"]["symbol"],
    )
