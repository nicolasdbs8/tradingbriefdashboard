# Trading Brief Engine (MVP)

Objectif : générer un brief d’analyse + checklist décisionnelle **sans exécuter de trading**.  
Univers : **crypto uniquement**, MVP sur **BTC/USDC**.

## Structure

```
src/
  __init__.py
tests/
config.yaml
requirements.txt
README.md
```

## Prérequis

- Python 3.10+

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## DÃ©marrage rapide (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python server.py
```

## Configuration

Le fichier `config.yaml` contient les paramètres par défaut (exchange, timeframes, risk%, lookback…).

## Variables d’environnement (local)

Crée un fichier `.env` à la racine du projet (copie de `.env.example`) et renseigne tes clés :

```
KRAKEN_API_KEY=your_kraken_api_key
KRAKEN_API_SECRET=your_kraken_api_secret_base64
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

## Clés Kraken (read-only)

Les clés API **ne doivent pas** être stockées dans le repo.  
Utilise des variables d’environnement :

```
KRAKEN_API_KEY=...
KRAKEN_API_SECRET=...
```

Un fichier `.env.example` est fourni comme modèle.

## Fees & Slippage

- Les indicateurs (EMA/RSI/ATR/VWAP) ne dépendent pas des fees.
- Les fees/slippage impactent **le sizing** et le **RR net**.
- Le moteur calcule `RR_net` et `size_net` via l’API Kraken `TradeVolume` (fallback si indispo).

## Exécution (MVP étape 2)

Commande MVP (1D/4H/1H/15m) :

```bash
python -m src.run --symbol BTC/USDC --exchange binance
```

## Lancer le brief (tous formats)

### 1) Terminal (CLI)

```bash
python -m src.run --symbol BTC/USDC --exchange binance
```

### 1b) Alertes Telegram (local)

Après avoir créé ton `.env` (voir section Variables d’environnement) :

```bash
python -m src.alerts.check --dry-run
python -m src.alerts.check
```

Types d'alertes Telegram :
- `HEADS-UP` : setup en confirmation (pre-alerte)
- `TRIGGER` : setup confirme et actionnable

Les seuils/cooldowns se reglent dans `config.yaml`, section `alerts`.

### 2) Dashboard web local (FastAPI)

Démarrer le serveur (après installation des dépendances) :

```bash
python server.py
```

Ouvrir dans le navigateur :

```
http://127.0.0.1:8000
```

Arrêter le serveur :

```
Ctrl + C
```

### 3) Dashboard HTML standalone (sans serveur)

Générer un JSON :

```bash
python export_brief.py
```

Ouvrir :

```
static/standalone_dashboard.html
```

Puis charger `brief.json` via le bouton “Load brief.json”.

### 4) Import JSON (usage général)

Le fichier `brief.json` est généré par :

```bash
python export_brief.py
```

Tu peux ensuite l’ouvrir dans n’importe quel outil (Excel, pandas, etc.).

## Dashboard (FastAPI)

Lancer le serveur local :

```bash
python server.py
```

Puis ouvrir `http://127.0.0.1:8000`.

### Démarrage rapide avec clés Kraken

Créer un script `start_server.ps1` :

```powershell
$env:KRAKEN_API_KEY="TON_API_KEY"
$env:KRAKEN_API_SECRET="TA_SECRET_BASE64"
python server.py
```

Puis lancer :

```powershell
.\start_server.ps1
```

## Dashboard (Standalone)

Génère un snapshot JSON :

```bash
python export_brief.py
```

Puis ouvre `static/standalone_dashboard.html` et charge `brief.json`.

## GitHub (push du projet)
```bash
git status
git add .
git commit -m "Update project"
git push
```

## Output attendu (exemple, placeholder)

```
TRADING BRIEF — BTC/USDC
Date: 2026-03-05 16:00 UTC

CHECKLIST ANALYSE
- Daily: ...
- 4H: ...
- 1H: ...
- 15m: ...

CHECKLIST DÉCISION
- Bias: ...
- Scénarios: ...
- Paramètres: ...

TRIGGERS
- Breakout + retest: ...
- Sweep + reclaim: ...
- Heatmap: vérifier alternative gratuite si trigger
```

## Roadmap (itérative)

0. Structure repo + README + requirements + config
1. 15m + EMA/RSI/ATR/VWAP + print brief
2. Ajouter 1D/4H/1H
3. Range/Trend + compression
4. Checklist décisionnelle + position sizing
5. Triggers + reminder heatmap
6. (Optionnel) CoinGlass API ou workflow manuel
