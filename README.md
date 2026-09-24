# Market Pulse Data

Daily public-data feed for the Deal Intelligence Market Pulse.

## Schedule
Runs Monday–Friday at **8:00 am Australia/Sydney** and can also be run manually from **Actions → Update Market Pulse → Run workflow**.

## Files
- `market_data.json` — latest normalized snapshot used by the HTML dashboard.
- `market_history.json` — rolling history of updater snapshots.
- `scripts/update_market_data.py` — public-data collector.
- `.github/workflows/update-market-data.yml` — scheduled GitHub Action.

## Important
No API keys are required by this version. It uses public downloadable sources.

The updater is failure-safe: when a source fails, it retains the previous successful observation where available and records the error in the JSON.

The Australian equity fallback is currently **All Ordinaries**, not the S&P/ASX 200. This is intentionally labelled rather than presenting a proxy as the ASX 200.
