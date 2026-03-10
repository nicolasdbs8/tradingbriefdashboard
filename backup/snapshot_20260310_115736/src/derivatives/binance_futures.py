from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import DerivativesSnapshot


@dataclass
class BinanceFuturesDerivativesClient:
    base_url: str = "https://fapi.binance.com"
    timeout_sec: int = 10
    retries: int = 2

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None
        for _ in range(self.retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout_sec)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"GET failed: {url}") from last_exc

    def fetch_snapshot(self, oi_symbol: str, funding_symbol: str) -> DerivativesSnapshot:
        oi = self._get("/fapi/v1/openInterest", {"symbol": oi_symbol})
        premium = self._get("/fapi/v1/premiumIndex", {"symbol": funding_symbol})
        funding_hist = self._get(
            "/fapi/v1/fundingRate", {"symbol": funding_symbol, "limit": 3}
        )
        oi_hist = None
        try:
            oi_hist = self._get(
                "/futures/data/openInterestHist",
                {"symbol": oi_symbol, "period": "1h", "limit": 30},
            )
        except Exception:
            oi_hist = None

        oi_contracts = float(oi["openInterest"])
        mark_price = float(premium["markPrice"])
        oi_usd = oi_contracts * mark_price
        funding_current_pct = float(premium["lastFundingRate"]) * 100

        funding_1d_pct = 0.0
        for item in funding_hist:
            funding_1d_pct += float(item["fundingRate"]) * 100

        oi_change_1h_pct = None
        oi_change_4h_pct = None
        oi_change_24h_pct = None
        if isinstance(oi_hist, list) and len(oi_hist) >= 25:
            try:
                latest = float(oi_hist[-1]["sumOpenInterest"])
                h1 = float(oi_hist[-2]["sumOpenInterest"])
                h4 = float(oi_hist[-5]["sumOpenInterest"])
                h24 = float(oi_hist[-25]["sumOpenInterest"])
                oi_change_1h_pct = ((latest - h1) / h1) * 100 if h1 != 0 else None
                oi_change_4h_pct = ((latest - h4) / h4) * 100 if h4 != 0 else None
                oi_change_24h_pct = ((latest - h24) / h24) * 100 if h24 != 0 else None
            except Exception:
                pass

        ts = datetime.now(timezone.utc)
        return DerivativesSnapshot(
            provider="binance",
            symbol=funding_symbol,
            ts=ts,
            oi_contracts=oi_contracts,
            oi_usd=oi_usd,
            mark_price=mark_price,
            funding_current_pct=funding_current_pct,
            funding_1d_pct=funding_1d_pct,
            oi_change_1h_pct=oi_change_1h_pct,
            oi_change_4h_pct=oi_change_4h_pct,
            oi_change_24h_pct=oi_change_24h_pct,
        )
