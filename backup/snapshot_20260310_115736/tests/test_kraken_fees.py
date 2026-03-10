from unittest.mock import MagicMock, patch

from src.execution.kraken_costs import KrakenFeeClient


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


@patch("src.execution.kraken_costs.requests.post")
def test_get_pair_fees_parsing(mock_post):
    payload = {
        "error": [],
        "result": {
            "fees": {"XBTUSDC": {"fee": "0.26"}},
            "fees_maker": {"XBTUSDC": {"fee": "0.16"}},
        },
    }
    mock_post.return_value = _mock_response(payload)
    client = KrakenFeeClient(api_key="k", api_secret="s")
    maker, taker = client.get_pair_fees("XBTUSDC", fallback=(0.01, 0.02))
    assert maker == 0.0016
    assert taker == 0.0026
