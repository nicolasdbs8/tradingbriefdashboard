from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlencode

import requests


@dataclass
class KrakenAccountClient:
    api_key: str
    api_secret: str
    base_url: str = "https://api.kraken.com"
    timeout_sec: int = 10
    retries: int = 2

    def _sign(self, url_path: str, data: Dict[str, str]) -> str:
        postdata = urlencode(data)
        message = (data["nonce"] + postdata).encode("utf-8")
        sha256 = hashlib.sha256(message).digest()
        mac = hmac.new(
            base64.b64decode(self.api_secret),
            url_path.encode("utf-8") + sha256,
            hashlib.sha512,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _post_private(self, url_path: str, data: Dict[str, str]) -> dict:
        url = f"{self.base_url}{url_path}"
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._sign(url_path, data),
        }
        last_exc: Optional[Exception] = None
        for _ in range(self.retries + 1):
            try:
                resp = requests.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=self.timeout_sec,
                )
                resp.raise_for_status()
                payload = resp.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                return payload
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"POST failed: {url_path}") from last_exc

    def get_balances(self) -> Dict[str, str]:
        nonce = str(int(time.time() * 1000))
        payload = self._post_private("/0/private/Balance", {"nonce": nonce})
        return payload.get("result", {})

    def get_usdc_equity(self) -> float:
        try:
            balances = self.get_balances()
            usdc = balances.get("USDC")
            if usdc is None:
                logging.warning("USDC balance not found on Kraken.")
                return 0.0
            return float(usdc)
        except Exception as exc:
            logging.warning("Kraken balance fetch failed: %s", exc)
            return 0.0
