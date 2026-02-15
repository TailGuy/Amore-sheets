# 🏆 Amore Tournament Tracker

Automated League of Legends tournament tracker that fetches player rank data from the [OP.GG MCP API](https://mcp-api.op.gg/mcp), calculates team scores, and writes everything to a Google Sheets leaderboard.

Designed for the **Amore** tournament — triggered automatically on every new Google Form registration.

---

## How It Works

```
Google Form Submission
        │
        ▼
Google Apps Script (onFormSubmit trigger)
        │
        ▼
POST → Webhook Server (Flask + Gunicorn + Nginx + HTTPS)
        │
        ▼
opgg_tracker.py
  ├── Reads team registrations from Source Sheet (form responses)
  ├── Fetches each player's rank data from OP.GG MCP API
  ├── Calculates LP scores and peak ranks
  ├── Sorts teams by score
  └── Writes results to Target Sheet (leaderboard)
```

---

## Features

- **Automatic triggering** via webhook on new form submissions
- **Multi-region search** — scans EUNE, EUW, TR, RU, NA, KR to find players
- **Peak rank detection** — finds the highest rank across all seasons (current, historical, and current split top tier)
- **LP scoring system** — converts ranks to LP values for fair team comparison
- **Team logos** — supports Google Drive image URLs with `=IMAGE()` formulas
- **OP.GG links** — creates clickable `=HYPERLINK()` links for each player's profile
- **Team sorting** — ranks teams by total LP score (top 5 players = regular, all 7 = total)
- **Logo correction** — supports a secondary logo column if the team uploaded the wrong image

---

## Project Structure

```
├── opgg_tracker.py       # Main script — fetches data and writes to Sheets
├── webhook_server.py     # Flask webhook — triggers the script on POST
├── requirements.txt      # Python dependencies
├── credentials.json      # Google service account credentials (not in repo)
└── logs/                 # Execution logs (created at runtime on server)
```

---

## Scoring System

Each player's score is based on their **current rank + LP**:

| Tier        | Base LP |
|-------------|---------|
| Iron IV     | 0       |
| Bronze IV   | 400     |
| Silver IV   | 800     |
| Gold IV     | 1200    |
| Platinum IV | 1600    |
| Emerald IV  | 2000    |
| Diamond IV  | 2400    |
| Master      | 2800    |
| Grandmaster | 3000    |
| Challenger  | 3300    |

Each division adds 100 LP (e.g., Gold III = 1300). In-game LP is added on top.

**Team scores:**
- **Regular Score** = sum of top 5 players' LP
- **Total Score** = sum of all 7 players' LP

---

## Rank Detection

### Current Rank
Extracted from the OP.GG API response using the `LeagueStat("SOLORANKED", ...)` field.

### Peak Rank
The highest rank found across three data sources:
1. **Current rank** — the player's live Solo Queue rank
2. **Current split top tier** (`RankEntrie1`) — highest rank achieved this split
3. **Historical seasons** (`PreviousSeason`) — all previous season endings

---

## Google Sheets Layout

### Source Sheet (Form Responses)
Contains registration data from Google Forms:
- Team name, short name, logo, description
- 5 main players + 2 fill players
- Each player has: Discord, Tournament account (Riot ID), Main account, Role

### Target Sheet (Leaderboard)
Teams are written in blocks of 7 rows (5 main + 2 fill), starting at row 5:

| Column | Content |
|--------|---------|
| C      | Team name / Short name tag |
| G      | Logo (`=IMAGE()` formula) |
| K      | Discord username |
| L      | Tournament account (`=HYPERLINK()` to OP.GG) |
| M      | Main account (`=HYPERLINK()` to OP.GG) |
| O      | Peak rank |
| Q      | Current rank |
| R      | Total LP score |
| S      | Team score (regular / total) |

---

## Webhook Server

The webhook server (`webhook_server.py`) is a Flask app that:
- Listens for `POST /trigger` requests
- Validates a `Bearer` token from the `Authorization` header
- Runs `opgg_tracker.py` in a background thread
- Saves output to timestamped log files in `/opt/opgg-tracker/logs/`
- Exposes a `GET /health` endpoint for monitoring

### Endpoints

| Method | Path       | Description |
|--------|------------|-------------|
| GET    | `/health`  | Returns `{"status": "ok"}` |
| POST   | `/trigger` | Runs the tracker (requires Bearer token) |

---

## Setup

### Prerequisites
- Python 3.10+
- Google Cloud service account with Sheets API & Drive API access
- The service account must be shared as "Editor" on both the source and target spreadsheets

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set up credentials
# Place your service account JSON file in the project root

# Run
python opgg_tracker.py
```

### Server Deployment (Ubuntu / Linode)

```bash
# 1. Create project directory
mkdir -p /opt/opgg-tracker
cd /opt/opgg-tracker

# 2. Upload files
# scp opgg_tracker.py webhook_server.py requirements.txt credentials.json user@SERVER_IP:~/opgg-tracker/

# 3. Set up Python venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Create systemd service
cat > /etc/systemd/system/opgg-webhook.service << 'EOF'
[Unit]
Description=OP.GG Tournament Tracker Webhook
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/opgg-tracker
Environment="WEBHOOK_SECRET=your-secret-token-here"
ExecStart=/opt/opgg-tracker/venv/bin/gunicorn -w 2 -b 127.0.0.1:5000 webhook_server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 5. Enable and start
systemctl daemon-reload
systemctl enable --now opgg-webhook

# 6. Set up Nginx reverse proxy + HTTPS (with certbot)
# See SETUP_GUIDE.md for full Nginx + SSL setup
```

### Google Apps Script Trigger

Attach this to your Google Form responses spreadsheet:

```javascript
function onFormSubmit(e) {
  var options = {
    'method': 'post',
    'headers': {
      'Authorization': 'Bearer YOUR_SECRET_TOKEN'
    },
    'muteHttpExceptions': true
  };
  UrlFetchApp.fetch('https://YOUR_SERVER_DOMAIN/trigger', options);
}
```

Set the trigger: **Edit → Triggers → Add → onFormSubmit → From spreadsheet → On form submit**

---

## Environment Variables

| Variable         | Used By            | Description |
|------------------|--------------------|-------------|
| `WEBHOOK_SECRET` | `webhook_server.py` | Bearer token for webhook authentication |

---

## Useful Commands

```bash
# Check webhook status
systemctl status opgg-webhook

# View live logs
journalctl -u opgg-webhook -f

# View latest run log
cat /opt/opgg-tracker/logs/$(ls -t /opt/opgg-tracker/logs/ | head -1)

# Restart service
systemctl restart opgg-webhook

# Manual trigger
curl -X POST https://YOUR_DOMAIN/trigger -H "Authorization: Bearer YOUR_TOKEN"
```
