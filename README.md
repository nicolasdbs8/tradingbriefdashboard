# Trading Brief Engine

Crypto analysis engine (no order execution) that produces a market brief and a decision dashboard.

Main goal: answer quickly "wait, watch, act?" with a consistent risk framework.

## 1) What this project does

- Fetches multi-timeframe OHLCV data (`1d`, `4h`, `1h`, `15m`)
- Computes indicators (EMA, RSI, ATR, VWAP)
- Evaluates:
  - market structure
  - setup score
  - trade gate (trade authorization)
  - directional probability
  - position sizing/exposure
- Exposes results in 3 formats:
  - terminal output
  - web dashboard (FastAPI)
  - JSON export for standalone dashboard

Important: this project does not place broker orders.

## 2) Stack

- Python 3.10+
- FastAPI + Jinja2 (web dashboard)
- Pandas / NumPy (calculations)
- CCXT + derivatives clients (market data)
- Telegram (optional alerts)

## 3) Useful project layout

```text
.
|-- brief_engine.py
|-- server.py
|-- export_brief.py
|-- config.yaml
|-- requirements.txt
|-- templates/
|   `-- dashboard.html
|-- static/
|   |-- dashboard.js
|   |-- style.css
|   `-- standalone_dashboard.html
|-- src/
|   |-- run.py
|   |-- report.py
|   |-- alerts/check.py
|   `-- ...
`-- tests/
```

## 4) Installation

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 5) Configuration

### `config.yaml`

Contains engine parameters:

- symbol/exchange
- timeframes and lookbacks
- setup presets
- filters (cost/vwap/probability/liquidity)
- directional probability settings
- Telegram alert settings
  - `trigger`: full confirmation alert
  - `heads_up`: early warning alert
  - `gate_open`: trade gate open alert (before active setup)

### Environment variables (`.env`)

Create `.env` at repo root (copy from `.env.example`):

```env
KRAKEN_API_KEY=...
KRAKEN_API_SECRET=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Notes:

- Kraken keys are read-only in this workflow (capital/fees lookup)
- if keys are missing, configured fallbacks are used

## 6) Run the project

### A) Terminal brief (CLI)

```bash
python -m src.run --symbol BTC/USDC --exchange binance
```

### B) Web dashboard (recommended)

```bash
python server.py
```

Then open:

```text
http://127.0.0.1:8000
```

### C) JSON export + standalone dashboard

1. Generate `brief.json`:

```bash
python export_brief.py
```

2. Open `static/standalone_dashboard.html`, then load `brief.json`.

### D) Telegram alerts (optional)

Dry run:

```bash
python -m src.alerts.check --dry-run
```

Real send:

```bash
python -m src.alerts.check
```

Useful options:

```bash
python -m src.alerts.check --force
python -m src.alerts.check --config config.yaml --state .alerts_state.json
```

## 7) Local API (web dashboard)

Main routes exposed by `server.py`:

- `GET /` -> dashboard HTML
- `GET /api/brief` -> latest cached brief JSON
- `POST /api/refresh` -> immediate recalculation
- `GET /api/config` -> refresh interval
- `POST /api/config` -> update refresh interval

## 8) Tests

If `pytest` is not installed:

```bash
python -m pip install pytest
```

Run tests:

```bash
python -m pytest -q
```

## 9) Quick troubleshooting

- API/exchange errors:
  - verify network access
  - verify symbol/exchange in `config.yaml`
- Empty dashboard:
  - check `server.py` console logs
  - test `GET /api/brief`
- Alerts not sent:
  - verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`
  - test with `--dry-run` first

## 10) Security

- Do not commit secrets (`.env`, local scripts, screenshots).
- If a key was exposed, revoke/rotate it immediately.

## 11) Disclaimer

This tool is an analysis assistant, not financial advice.
You remain responsible for all trading decisions and risk.

## 12) Git update and push

```bash
git status
git add .
git commit -m "Update project"
git push
```
