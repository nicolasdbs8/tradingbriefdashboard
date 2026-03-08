from unittest.mock import MagicMock, patch

from src.broker.kraken_account import KrakenAccountClient


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


@patch("src.broker.kraken_account.requests.post")
def test_get_balances_ok(mock_post):
    mock_post.return_value = _mock_response({"error": [], "result": {"USDC": "123.45"}})
    client = KrakenAccountClient(api_key="k", api_secret="s")
    balances = client.get_balances()
    assert balances["USDC"] == "123.45"


@patch("src.broker.kraken_account.requests.post")
def test_get_usdc_equity_missing(mock_post):
    mock_post.return_value = _mock_response({"error": [], "result": {"USD": "10"}})
    client = KrakenAccountClient(api_key="k", api_secret="s")
    equity = client.get_usdc_equity()
    assert equity == 0.0
