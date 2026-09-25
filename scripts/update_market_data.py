from __future__ import annotations

import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "market_data.json"
HISTORY = ROOT / "market_history.json"

TZ = ZoneInfo("Australia/Sydney")

HEADERS = {
    "User-Agent": "Mozilla/5.0 market-pulse-data/2.0"
}


# ---------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------

def download_file(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=40
    )
    response.raise_for_status()
    return response


def pct_change(current, previous):
    if current is None or previous in (None, 0):
        return None

    return round(
        (current / previous - 1) * 100,
        2
    )


def previous_value(rows, trading_days_back):
    if not rows:
        return None

    position = max(
        0,
        len(rows) - 1 - trading_days_back
    )

    return rows[position]["value"]


def build_record(
    name,
    rows,
    unit,
    source,
    source_url
):

    clean_rows = []

    for row in rows:
        try:
            value = float(row["value"])

            if math.isfinite(value):
                clean_rows.append({
                    "date": str(row["date"]),
                    "value": value
                })

        except Exception:
            pass

    clean_rows.sort(
        key=lambda x: x["date"]
    )

    if not clean_rows:
        raise ValueError(
            f"No observations for {name}"
        )

    latest = clean_rows[-1]

    current = latest["value"]

    return {
        "name": name,
        "value": round(current, 6),
        "unit": unit,

        "observation_date": latest["date"],

        "change_1d": pct_change(
            current,
            previous_value(clean_rows, 1)
        ),

        "change_1w": pct_change(
            current,
            previous_value(clean_rows, 5)
        ),

        "change_1m": pct_change(
            current,
            previous_value(clean_rows, 22)
        ),

        "source": source,
        "source_url": source_url,

        "status": "ok"
    }


# ---------------------------------------------------------
# YAHOO FINANCE
# ---------------------------------------------------------

def yahoo_market(
    symbol,
    name,
    unit
):

    data = yf.download(
        symbol,
        period="3mo",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False
    )

    if data is None or data.empty:
        raise ValueError(
            f"No observations for {name}"
        )

    close = data["Close"]

    # yfinance can return a multi-index dataframe,
    # even when requesting a single instrument.
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    rows = []

    for index, value in close.dropna().items():

        date = pd.Timestamp(
            index
        ).date().isoformat()

        rows.append({
            "date": date,
            "value": float(value)
        })

    return build_record(
        name,
        rows,
        unit,
        "Yahoo Finance",
        f"https://finance.yahoo.com/quote/{symbol}"
    )


# ---------------------------------------------------------
# FRED — US GOVERNMENT YIELDS
# ---------------------------------------------------------

def fred_yield(
    series,
    name
):

    url = (
        "https://fred.stlouisfed.org/"
        f"graph/fredgraph.csv?id={series}"
    )

    response = download_file(url)

    df = pd.read_csv(
        io.BytesIO(response.content)
    )

    # FRED currently calls this observation_date.
    # The fallback keeps the parser resilient.
    if "observation_date" in df.columns:
        date_column = "observation_date"
    else:
        date_column = df.columns[0]

    if series in df.columns:
        value_column = series
    else:
        value_column = df.columns[-1]

    rows = []

    for _, row in df.iterrows():

        date = pd.to_datetime(
            row[date_column],
            errors="coerce"
        )

        value = pd.to_numeric(
            row[value_column],
            errors="coerce"
        )

        if (
            not pd.isna(date)
            and not pd.isna(value)
        ):
            rows.append({
                "date": date.date().isoformat(),
                "value": float(value)
            })

    result = build_record(
        name,
        rows,
        "%",
        "Federal Reserve / FRED",
        f"https://fred.stlouisfed.org/series/{series}"
    )

    current = result["value"]

    # Yield movements should be shown in basis points.
    for field, days in [
        ("change_1d_bp", 1),
        ("change_1w_bp", 5),
        ("change_1m_bp", 22)
    ]:

        previous = previous_value(
            rows,
            days
        )

        if previous is None:
            result[field] = None

        else:
            result[field] = round(
                (current - float(previous)) * 100,
                1
            )

    return result


# ---------------------------------------------------------
# RBA — AUSTRALIAN GOVERNMENT YIELDS
# ---------------------------------------------------------

def rba_f2():

    urls = [
        "https://www.rba.gov.au/statistics/tables/xls/f02d.xlsx",
        "https://www.rba.gov.au/statistics/tables/xls/f02hist.xlsx"
    ]

    last_error = None

    for url in urls:

        try:

            content = download_file(
                url
            ).content

            workbook = pd.ExcelFile(
                io.BytesIO(content)
            )

            for sheet in workbook.sheet_names:

                raw = pd.read_excel(
                    io.BytesIO(content),
                    sheet_name=sheet,
                    header=None
                )

                labels = []

                for column in range(
                    raw.shape[1]
                ):

                    parts = []

                    for row in range(
                        min(15, raw.shape[0])
                    ):

                        value = str(
                            raw.iat[row, column]
                        )

                        if value != "nan":
                            parts.append(value)

                    labels.append(
                        " ".join(parts)
                    )

                column_2y = next(
                    (
                        i
                        for i, text
                        in enumerate(labels)

                        if re.search(
                            r"\b2\s*year\b",
                            text,
                            re.I
                        )

                        and
                        "Australian Government"
                        in text
                    ),
                    None
                )

                column_10y = next(
                    (
                        i
                        for i, text
                        in enumerate(labels)

                        if re.search(
                            r"\b10\s*year\b",
                            text,
                            re.I
                        )

                        and
                        "Australian Government"
                        in text

                        and
                        "inflation"
                        not in text.lower()
                    ),
                    None
                )

                if (
                    column_2y is None
                    or column_10y is None
                ):
                    continue

                rows_2y = []
                rows_10y = []

                for row in range(
                    raw.shape[0]
                ):

                    date = pd.to_datetime(
                        raw.iat[row, 0],
                        errors="coerce"
                    )

                    if pd.isna(date):
                        continue

                    for column, target in [
                        (column_2y, rows_2y),
                        (column_10y, rows_10y)
                    ]:

                        value = pd.to_numeric(
                            raw.iat[row, column],
                            errors="coerce"
                        )

                        if not pd.isna(value):

                            target.append({
                                "date":
                                    date.date().isoformat(),

                                "value":
                                    float(value)
                            })

                if rows_2y and rows_10y:

                    au2 = build_record(
                        "AU 2Y",
                        rows_2y,
                        "%",
                        "Reserve Bank of Australia — F2",
                        "https://www.rba.gov.au/statistics/tables/"
                    )

                    au10 = build_record(
                        "AU 10Y",
                        rows_10y,
                        "%",
                        "Reserve Bank of Australia — F2",
                        "https://www.rba.gov.au/statistics/tables/"
                    )

                    # Convert yield movements into basis points.
                    for result, rows in [
                        (au2, rows_2y),
                        (au10, rows_10y)
                    ]:

                        current = result["value"]

                        for field, days in [
                            ("change_1d_bp", 1),
                            ("change_1w_bp", 5),
                            ("change_1m_bp", 22)
                        ]:

                            previous = previous_value(
                                rows,
                                days
                            )

                            if previous is None:
                                result[field] = None

                            else:
                                result[field] = round(
                                    (
                                        current
                                        - float(previous)
                                    ) * 100,
                                    1
                                )

                    return au2, au10

        except Exception as error:
            last_error = error

    raise RuntimeError(
        f"Could not parse RBA F2: {last_error}"
    )


# ---------------------------------------------------------
# PREVIOUS SNAPSHOT
# ---------------------------------------------------------

def load_json(
    path,
    default
):

    try:
        return json.loads(
            path.read_text()
        )

    except Exception:
        return default


previous = load_json(
    OUT,
    {"markets": {}}
)

markets = {}
errors = {}


# ---------------------------------------------------------
# TRADED MARKETS
# ---------------------------------------------------------

feeds = [

    (
        "sp500",
        lambda:
        yahoo_market(
            "^GSPC",
            "S&P 500",
            "index"
        )
    ),

    (
        "nasdaq",
        lambda:
        yahoo_market(
            "^IXIC",
            "Nasdaq Composite",
            "index"
        )
    ),

    (
        "asx200",
        lambda:
        yahoo_market(
            "^AXJO",
            "S&P/ASX 200",
            "index"
        )
    ),

    (
        "audusd",
        lambda:
        yahoo_market(
            "AUDUSD=X",
            "AUD/USD",
            "USD"
        )
    ),

    (
        "eurusd",
        lambda:
        yahoo_market(
            "EURUSD=X",
            "EUR/USD",
            "USD"
        )
    ),

    (
        "usdjpy",
        lambda:
        yahoo_market(
            "JPY=X",
            "USD/JPY",
            "JPY"
        )
    ),

    (
        "gold",
        lambda:
        yahoo_market(
            "GC=F",
            "Gold",
            "USD/oz"
        )
    ),

    (
        "wti",
        lambda:
        yahoo_market(
            "CL=F",
            "WTI crude",
            "USD/bbl"
        )
    ),

    (
        "brent",
        lambda:
        yahoo_market(
            "BZ=F",
            "Brent crude",
            "USD/bbl"
        )
    ),

    (
        "copper",
        lambda:
        yahoo_market(
            "HG=F",
            "Copper",
            "USD/lb"
        )
    ),

    (
        "us2y",
        lambda:
        fred_yield(
            "DGS2",
            "US 2Y"
        )
    ),

    (
        "us10y",
        lambda:
        fred_yield(
            "DGS10",
            "US 10Y"
        )
    )
]


# ---------------------------------------------------------
# DOWNLOAD EACH SERIES
# ---------------------------------------------------------

for key, function in feeds:

    try:

        markets[key] = function()

    except Exception as error:

        errors[key] = str(error)

        # Never destroy the previous successful observation.
        if key in previous.get(
            "markets",
            {}
        ):

            old = previous[
                "markets"
            ][key].copy()

            old["status"] = "stale"

            old["update_error"] = str(
                error
            )

            markets[key] = old


# ---------------------------------------------------------
# AUSTRALIAN YIELDS
# ---------------------------------------------------------

try:

    (
        markets["au2y"],
        markets["au10y"]
    ) = rba_f2()

except Exception as error:

    errors["rba_f2"] = str(
        error
    )

    for key in [
        "au2y",
        "au10y"
    ]:

        if key in previous.get(
            "markets",
            {}
        ):

            old = previous[
                "markets"
            ][key].copy()

            old["status"] = "stale"

            old["update_error"] = str(
                error
            )

            markets[key] = old


# ---------------------------------------------------------
# WRITE SNAPSHOT
# ---------------------------------------------------------

now = datetime.now(TZ)

payload = {

    "schema_version": 2,

    "updated_at":
        now.isoformat(
            timespec="seconds"
        ),

    "timezone":
        "Australia/Sydney",

    "markets":
        markets,

    "errors":
        errors,

    "notes": [

        "Daily public-data snapshot for a personal market-learning dashboard; not for trading.",

        "Yahoo Finance is used for traded-market daily closes; FRED for US Treasury yields; RBA F2 for Australian government yields.",

        "Observation dates are preserved because different markets and sources close or publish at different times."
    ]
}


OUT.write_text(
    json.dumps(
        payload,
        indent=2,
        ensure_ascii=False
    )
    + "\n"
)


# ---------------------------------------------------------
# KEEP 90 SNAPSHOTS
# ---------------------------------------------------------

history = load_json(
    HISTORY,
    []
)

history.append({
    "updated_at":
        payload["updated_at"],

    "markets":
        markets
})

history = history[-90:]

HISTORY.write_text(
    json.dumps(
        history,
        indent=2,
        ensure_ascii=False
    )
    + "\n"
)


print(
    f"Wrote {len(markets)} instruments; "
    f"{len(errors)} errors"
)

if errors:

    print(
        json.dumps(
            errors,
            indent=2
        )
    )
