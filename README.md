# Tournament Tracker

Fetches League of Legends player data from OP.GG and writes rankings to Google Sheets.

## Features
- Fetches current rank and peak rank for players
- Calculates team scores based on player rankings
- Auto-triggers via webhook on Google Form submissions

## Setup

1. Create a Google Cloud service account with Sheets API access
2. Save credentials as `credentials.json`
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python opgg_tracker.py`

## Webhook (Optional)
Deploy `webhook_server.py` with gunicorn for automatic triggers on form submissions.

## Environment Variables
- `WEBHOOK_SECRET` - Bearer token for webhook authentication
