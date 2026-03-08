from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from .models import DerivativesSnapshot


@dataclass
class BybitV5DerivativesClient:
    base_url: str = "https://api.bybit.com"
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

    def fetch_snapshot(self, category: str, symbol: str) -> DerivativesSnapshot:
        tickers = self._get(
            "/v5/market/tickers",
            {"category": category, "symbol": symbol},
        )
        funding_hist = self._get(
            "/v5/market/funding/history",
            {"category": category, "symbol": symbol, "limit": 3},
        )
        oi_hist = self._get(
            "/v5/market/open-interest",
            {"category": category, "symbol": symbol, "intervalTime": "1h", "limit": 30},
        )

        ticker = tickers["result"]["list"][0]
        mark_price = float(ticker["markPrice"])
        funding_current_pct = float(ticker.get("fundingRate", 0.0)) * 100

        funding_1d_pct = 0.0
        for item in funding_hist["result"]["list"]:
            funding_1d_pct += float(item["fundingRate"]) * 100

        oi_contracts = float(oi_hist["result"]["list"][-1]["openInterest"])
        oi_usd = oi_contracts * mark_price

        oi_change_1h_pct = None
        oi_change_4h_pct = None
        oi_change_24h_pct = None
        try:
            if len(oi_hist["result"]["list"]) >= 25:
                latest = float(oi_hist["result"]["list"][-1]["openInterest"])
                h1 = float(oi_hist["result"]["list"][-2]["openInterest"])
                h4 = float(oi_hist["result"]["list"][-5]["openInterest"])
                h24 = float(oi_hist["result"]["list"][-25]["openInterest"])
                oi_change_1h_pct = ((latest - h1) / h1) * 100 if h1 != 0 else None
                oi_change_4h_pct = ((latest - h4) / h4) * 100 if h4 != 0 else None
                oi_change_24h_pct = ((latest - h24) / h24) * 100 if h24 != 0 else None
        except Exception:
            pass

        ts = datetime.now(timezone.utc)
        return DerivativesSnapshot(
            provider="bybit",
            symbol=symbol,
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
