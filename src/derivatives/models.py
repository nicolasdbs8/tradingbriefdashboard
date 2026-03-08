from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class DerivativesSnapshot:
    provider: str
    symbol: str
    ts: datetime
    oi_contracts: float
    oi_usd: float
    mark_price: float
    funding_current_pct: float
    funding_1d_pct: float
    oi_change_1h_pct: Optional[float] = None
    oi_change_4h_pct: Optional[float] = None
    oi_change_24h_pct: Optional[float] = None
