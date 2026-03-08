from __future__ import annotations

from typing import Optional

import requests


def send_telegram_message(token: str, chat_id: str, text: str, timeout_sec: int = 10) -> None:
    if not token or not chat_id:
        raise ValueError("Telegram token or chat_id missing.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=timeout_sec)
    resp.raise_for_status()
