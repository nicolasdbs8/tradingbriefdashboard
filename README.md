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

### B.0) Windows launch in double-click mode

Files at project root:

- `launch_dashboard.ps1` -> robust launcher (starts server, waits, opens browser, clear errors)
- `launch_dashboard.bat` -> simple double-click launcher
- `launch_dashboard_silent.vbs` -> cleaner double-click launcher (hidden window)
- launcher loads environment variables from `.env` before starting server (Kraken/Telegram keys included), with support for `KEY=value` and `$env:KEY="value"` lines

Default URL opened:

```text
http://127.0.0.1:8000
```

Desktop shortcut setup:

1. Right click `launch_dashboard.bat` (or `launch_dashboard_silent.vbs`) -> **Send to > Desktop (create shortcut)**.
2. Rename shortcut to: `Trading Brief Dashboard`.
3. Optional icon: shortcut **Properties > Change Icon...** and choose any `.ico` file.
4. Double-click shortcut to launch dashboard.

Error behavior:

- If startup fails, launcher prints a clear error and points to logs:
  - `logs/dashboard-server.out.log`
  - `logs/dashboard-server.err.log`

### B.1) Access from tablet/phone on local network (LAN)

`python server.py` binds to localhost only (`127.0.0.1`), so it is not reachable from other devices.

Use this command instead:

```bash
python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Then:

1. Get your PC IPv4 address (`ipconfig` on Windows).
2. Open on tablet/phone: `http://<PC_IPV4>:8000`
3. If needed, allow Python/Uvicorn in Windows Firewall (private network).

### C) JSON export + standalone dashboard

1. Generate `brief.json`:

```bash
python export_brief.py
```

2. Open `static/standalone_dashboard.html`, then load `brief.json`.

### D) Free remote dashboard via GitHub Pages (static + auto-regenerated JSON)

This repo includes `.github/workflows/static-dashboard-pages.yml`.

What it does:

- Generates `brief.json`
- Publishes `static/standalone_dashboard.html` as `index.html`
- Deploys both files to GitHub Pages
- Runs hourly (`cron`)

Setup once:

1. Push the repo to GitHub.
2. In repository settings, open **Pages**.
3. Set **Source** to **GitHub Actions**.
4. Run workflow **Static Dashboard Pages** once (`workflow_dispatch`).
5. Open the URL shown in the workflow output.

Notes:

- If Kraken secrets are missing in GitHub, the build still works with fallbacks.
- The standalone page auto-refreshes `brief.json` every 5 minutes client-side.

### D.1) Cloud Run deployment (full Python backend, accessible from iPad)

This repo includes:

- `Dockerfile`
- `.dockerignore`
- `deploy_cloudrun.ps1`
- `cloudrun.env.yaml.example`

One-time setup:

1. Install Google Cloud SDK (`gcloud`) and login:

```powershell
gcloud auth login
gcloud auth application-default login
```

2. Enable required APIs (replace `YOUR_PROJECT_ID`):

```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project YOUR_PROJECT_ID
```

3. Create env file from template at repo root:

```powershell
Copy-Item cloudrun.env.yaml.example cloudrun.env.yaml
```

Then fill `cloudrun.env.yaml` with your real values (`KRAKEN_API_KEY`, `KRAKEN_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).

Deploy:

```powershell
.\deploy_cloudrun.ps1 -ProjectId YOUR_PROJECT_ID -Region europe-west1 -ServiceName trading-brief-dashboard
```

After deploy:

- script prints and opens service URL
- open same URL on iPad (Safari)
- optional: Safari -> Share -> Add to Home Screen

Notes:

- Current deploy settings are cost-safe by default (`min-instances=0`, `max-instances=1`).
- Cloud Run free tier is usage-based; monitor billing/usage in GCP console.

### E) Telegram alerts (optional)

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

## 10) GitHub Actions quota (to avoid monthly limit)

- Public repo: GitHub-hosted Actions is usually not a practical minute-cap concern.
- Private repo (GitHub Free): 2,000 Linux minutes/month included.
- Hourly job: about 720 runs/month.
- If one run takes ~1 minute, usage is ~720 minutes/month (safe buffer).
- Existing `telegram-alerts.yml` runs every 15 minutes and can consume much more quota.

## 11) Security

- Do not commit secrets (`.env`, local scripts, screenshots).
- If a key was exposed, revoke/rotate it immediately.

## 12) Disclaimer

This tool is an analysis assistant, not financial advice.
You remain responsible for all trading decisions and risk.

## 13) Git update and push

```bash
git status
git add .
git commit -m "Update project"
git push
```
