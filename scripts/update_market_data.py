from __future__ import annotations
import csv, io, json, math, re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "market_data.json"
HISTORY = ROOT / "market_history.json"
TZ = ZoneInfo("Australia/Sydney")
UA = {"User-Agent": "market-pulse-data/1.0 (+https://github.com/annekarose06-eng/market-pulse-data)"}

def get(url, **kwargs):
    r = requests.get(url, headers=UA, timeout=30, **kwargs)
    r.raise_for_status()
    return r

def pct(a, b):
    return None if b in (None, 0) or a is None else round((a / b - 1) * 100, 2)

def nearest_back(rows, n):
    return rows[max(0, len(rows)-1-n)]["value"] if rows else None

def market_record(name, rows, unit, source, source_url):
    rows = [r for r in rows if r.get("value") is not None and math.isfinite(float(r["value"]))]
    rows.sort(key=lambda x: x["date"])
    if not rows:
        raise ValueError(f"No observations for {name}")
    last = rows[-1]
    return {
        "name": name, "value": round(float(last["value"]), 6), "unit": unit,
        "observation_date": last["date"],
        "change_1d": pct(float(last["value"]), nearest_back(rows, 1)),
        "change_1w": pct(float(last["value"]), nearest_back(rows, 5)),
        "change_1m": pct(float(last["value"]), nearest_back(rows, 22)),
        "source": source, "source_url": source_url, "status": "ok"
    }

def stooq(symbol, name, unit="$"):
    url = f"https://stooq.com/q/d/l/?s={symbol}&d1=20260101&i=d"
    text = get(url).text
    rows=[]
    for r in csv.DictReader(io.StringIO(text)):
        try: rows.append({"date": r["Date"], "value": float(r["Close"])})
        except: pass
    return market_record(name, rows, unit, "Stooq", url)

def fred(series, name):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    text = get(url).text
    rows=[]
    for r in csv.DictReader(io.StringIO(text)):
        try: rows.append({"date": r["DATE"], "value": float(r[series])})
        except: pass
    rec = market_record(name, rows, "%", "Federal Reserve / FRED", f"https://fred.stlouisfed.org/series/{series}")
    # For yields, changes are more intuitive in basis points.
    vals=[x for x in rows if x.get("value") is not None]
    vals.sort(key=lambda x:x["date"])
    cur=float(vals[-1]["value"])
    for key,n in [("change_1d_bp",1),("change_1w_bp",5),("change_1m_bp",22)]:
        old=nearest_back(vals,n)
        rec[key]=None if old is None else round((cur-float(old))*100,1)
    return rec

def rba_f2():
    # RBA's canonical F2 spreadsheet. The parser searches headers so it is resilient
    # to modest column-layout changes.
    urls = [
        "https://www.rba.gov.au/statistics/tables/xls/f02d.xlsx",
        "https://www.rba.gov.au/statistics/tables/xls/f02hist.xlsx",
    ]
    last_err=None
    for url in urls:
        try:
            content=get(url).content
            xls=pd.ExcelFile(io.BytesIO(content))
            for sheet in xls.sheet_names:
                raw=pd.read_excel(io.BytesIO(content), sheet_name=sheet, header=None)
                # find header rows mentioning Australian Government and 2/10 year
                header_text = raw.astype(str).apply(lambda row: " | ".join(row.values), axis=1)
                candidates=[i for i,s in enumerate(header_text) if "Australian Government" in s]
                if not candidates: continue
                # Build labels from several top rows and find date/value columns.
                top=min(candidates)
                data_start=top+1
                labels=[]
                for c in range(raw.shape[1]):
                    parts=[]
                    for rr in range(max(0,top-4), min(raw.shape[0],top+3)):
                        v=str(raw.iat[rr,c])
                        if v!="nan": parts.append(v)
                    labels.append(" ".join(parts))
                date_col=0
                c2=next((i for i,s in enumerate(labels) if re.search(r"\b2\s*year\b",s,re.I) and "Australian Government" in s),None)
                c10=next((i for i,s in enumerate(labels) if re.search(r"\b10\s*year\b",s,re.I) and "Australian Government" in s and "inflation" not in s.lower()),None)
                if c2 is None or c10 is None: continue
                rows2=[]; rows10=[]
                for i in range(data_start,raw.shape[0]):
                    d=pd.to_datetime(raw.iat[i,date_col],errors="coerce")
                    if pd.isna(d): continue
                    for col, arr in [(c2,rows2),(c10,rows10)]:
                        v=pd.to_numeric(raw.iat[i,col],errors="coerce")
                        if not pd.isna(v): arr.append({"date":d.date().isoformat(),"value":float(v)})
                if rows2 and rows10:
                    return (
                        market_record("AU 2Y", rows2, "%", "Reserve Bank of Australia — F2", "https://www.rba.gov.au/statistics/tables/"),
                        market_record("AU 10Y", rows10, "%", "Reserve Bank of Australia — F2", "https://www.rba.gov.au/statistics/tables/")
                    )
        except Exception as e:
            last_err=e
    raise RuntimeError(f"Could not parse RBA F2: {last_err}")

def load_json(path, default):
    try: return json.loads(path.read_text())
    except: return default

previous=load_json(OUT, {"markets":{}})
markets={}
errors={}

# Public daily-market sources. If a symbol ever changes upstream, the updater
# preserves the previous successful value and records the error.
feeds = [
    ("sp500", lambda: stooq("^spx", "S&P 500", "index")),
    ("nasdaq", lambda: stooq("^ndq", "Nasdaq Composite", "index")),
    ("asx200", lambda: stooq("^aord", "Australian equities (All Ordinaries)", "index")),
    ("audusd", lambda: stooq("audusd", "AUD/USD", "USD")),
    ("eurusd", lambda: stooq("eurusd", "EUR/USD", "USD")),
    ("usdjpy", lambda: stooq("usdjpy", "USD/JPY", "JPY")),
    ("gold", lambda: stooq("xauusd", "Gold", "USD/oz")),
    ("wti", lambda: stooq("cl.f", "WTI crude", "USD/bbl")),
    ("brent", lambda: stooq("cb.f", "Brent crude", "USD/bbl")),
    ("copper", lambda: stooq("hg.f", "Copper", "USD")),
    ("us2y", lambda: fred("DGS2", "US 2Y")),
    ("us10y", lambda: fred("DGS10", "US 10Y")),
]

for key, fn in feeds:
    try:
        markets[key]=fn()
    except Exception as e:
        errors[key]=str(e)
        if key in previous.get("markets",{}):
            markets[key]=previous["markets"][key] | {"status":"stale","update_error":str(e)}

try:
    au2, au10 = rba_f2()

    # For government bond yields, show movements in basis points
    # rather than percentage changes in the yield itself.
    for rec, key in [(au2, "au2y"), (au10, "au10y")]:
        current = rec["value"]

        # Convert the existing percentage changes back into prior yields,
        # then express the movement in basis points.
        for period, bp_key in [
            ("change_1d", "change_1d_bp"),
            ("change_1w", "change_1w_bp"),
            ("change_1m", "change_1m_bp")
        ]:
            pct_change = rec.get(period)

            if pct_change is not None:
                previous = current / (1 + pct_change / 100)
                rec[bp_key] = round((current - previous) * 100, 1)

        markets[key] = rec
except Exception as e:
    errors["rba_f2"]=str(e)
    for key in ("au2y","au10y"):
        if key in previous.get("markets",{}):
            markets[key]=previous["markets"][key] | {"status":"stale","update_error":str(e)}

now=datetime.now(TZ)
payload={
    "schema_version":1,
    "updated_at":now.isoformat(timespec="seconds"),
    "timezone":"Australia/Sydney",
    "markets":markets,
    "errors":errors,
    "notes":[
        "This feed is for a personal market-learning dashboard, not trading or valuation.",
        "Australian government yields use the latest RBA F2 observation available; F2 is published weekly with a lag.",
        "The Australian equity public fallback is All Ordinaries until a dependable no-key public ASX 200 feed is validated."
    ]
}
OUT.write_text(json.dumps(payload,indent=2,ensure_ascii=False)+"\n")

history=load_json(HISTORY,[])
history.append({"updated_at":payload["updated_at"],"markets":markets})
history=history[-90:]
HISTORY.write_text(json.dumps(history,indent=2,ensure_ascii=False)+"\n")
print(f"Wrote {OUT.name}: {len(markets)} instruments; {len(errors)} errors")
if errors:
    print(json.dumps(errors,indent=2))
