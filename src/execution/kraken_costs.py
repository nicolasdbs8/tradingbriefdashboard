from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlencode

import requests


@dataclass
class KrakenFeeClient:
    api_key: str
    api_secret: str
    base_url: str = "https://api.kraken.com"
    timeout_sec: int = 10
    retries: int = 2

    def _sign(self, url_path: str, data: dict) -> str:
        postdata = urlencode(data)
        message = (data["nonce"] + postdata).encode("utf-8")
        sha256 = hashlib.sha256(message).digest()
        mac = hmac.new(
            base64.b64decode(self.api_secret),
            url_path.encode("utf-8") + sha256,
            hashlib.sha512,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _post_private(self, url_path: str, data: dict) -> dict:
        url = f"{self.base_url}{url_path}"
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._sign(url_path, data),
        }
        last_exc: Optional[Exception] = None
        for _ in range(self.retries + 1):
            try:
                resp = requests.post(url, data=data, headers=headers, timeout=self.timeout_sec)
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                return payload
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"POST failed: {url_path}") from last_exc

    def get_pair_fees(self, pair: str, fallback: Tuple[float, float]) -> Tuple[float, float]:
        nonce = str(int(time.time() * 1000))
        payload = self._post_private("/0/private/TradeVolume", {"nonce": nonce, "pair": pair})
        result = payload.get("result", {})
        fees = result.get("fees", {})
        fees_maker = result.get("fees_maker", {})
        if pair in fees and pair in fees_maker:
            try:
                taker_fee = float(fees[pair]["fee"]) / 100.0
                maker_fee = float(fees_maker[pair]["fee"]) / 100.0
                return maker_fee, taker_fee
            except Exception as exc:
                logging.warning("Fee parsing failed, using fallback: %s", exc)
        return fallback
