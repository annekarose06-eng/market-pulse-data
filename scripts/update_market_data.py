from __future__ import annotations

import io
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf


# =========================================================
# CONFIGURATION
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

OUT = ROOT / "market_data.json"
HISTORY = ROOT / "market_history.json"

TZ = ZoneInfo("Australia/Sydney")

HEADERS = {
    "User-Agent": "Mozilla/5.0 market-pulse-data/3.0"
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def download_file(url):

    response = requests.get(
        url,
        headers=HEADERS,
        timeout=60
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

            value = float(
                row["value"]
            )

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

        "name":
            name,

        "value":
            round(current, 6),

        "unit":
            unit,

        "observation_date":
            latest["date"],

        "change_1d":
            pct_change(
                current,
                previous_value(
                    clean_rows,
                    1
                )
            ),

        "change_1w":
            pct_change(
                current,
                previous_value(
                    clean_rows,
                    5
                )
            ),

        "change_1m":
            pct_change(
                current,
                previous_value(
                    clean_rows,
                    22
                )
            ),

        "source":
            source,

        "source_url":
            source_url,

        "status":
            "ok"
    }


def add_basis_point_changes(
    record,
    rows
):

    current = record["value"]

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

            record[field] = None

        else:

            record[field] = round(
                (
                    current
                    - float(previous)
                ) * 100,
                1
            )

    return record


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


# =========================================================
# YAHOO FINANCE
#
# Used for:
# S&P 500
# Nasdaq Composite
# S&P/ASX 200
# AUD/USD
# EUR/USD
# USD/JPY
# Gold
# WTI
# Brent
# Copper
# =========================================================

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

    # yfinance may return a DataFrame
    # even for one instrument.

    if isinstance(
        close,
        pd.DataFrame
    ):

        close = close.iloc[:, 0]

    rows = []

    for index, value in close.dropna().items():

        date = (
            pd.Timestamp(index)
            .date()
            .isoformat()
        )

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


# =========================================================
# U.S. DEPARTMENT OF THE TREASURY
#
# Used for:
# US 2Y
# US 10Y
# =========================================================

def treasury_yields():

    year = datetime.now(
        TZ
    ).year

    url = (
        "https://home.treasury.gov/"
        "resource-center/data-chart-center/"
        "interest-rates/pages/xml"
        "?data=daily_treasury_yield_curve"
        f"&field_tdr_date_value={year}"
    )

    response = download_file(
        url
    )

    root = ET.fromstring(
        response.content
    )

    namespaces = {

        "atom":
            "http://www.w3.org/2005/Atom",

        "m":
            "http://schemas.microsoft.com/ado/2007/08/"
            "dataservices/metadata",

        "d":
            "http://schemas.microsoft.com/ado/2007/08/"
            "dataservices"
    }

    rows_2y = []
    rows_10y = []

    entries = root.findall(
        "atom:entry",
        namespaces
    )

    for entry in entries:

        properties = entry.find(
            "atom:content/m:properties",
            namespaces
        )

        if properties is None:
            continue

        date_node = properties.find(
            "d:NEW_DATE",
            namespaces
        )

        two_year_node = properties.find(
            "d:BC_2YEAR",
            namespaces
        )

        ten_year_node = properties.find(
            "d:BC_10YEAR",
            namespaces
        )

        if (
            date_node is None
            or not date_node.text
        ):
            continue

        date = pd.to_datetime(
            date_node.text,
            errors="coerce"
        )

        if pd.isna(date):
            continue

        date_string = (
            date.date().isoformat()
        )

        # -------------------------
        # US 2 YEAR
        # -------------------------

        if (
            two_year_node is not None
            and two_year_node.text
        ):

            value = pd.to_numeric(
                two_year_node.text,
                errors="coerce"
            )

            if not pd.isna(value):

                rows_2y.append({
                    "date": date_string,
                    "value": float(value)
                })

        # -------------------------
        # US 10 YEAR
        # -------------------------

        if (
            ten_year_node is not None
            and ten_year_node.text
        ):

            value = pd.to_numeric(
                ten_year_node.text,
                errors="coerce"
            )

            if not pd.isna(value):

                rows_10y.append({
                    "date": date_string,
                    "value": float(value)
                })

    if not rows_2y:

        raise ValueError(
            "No US 2Y observations returned by Treasury"
        )

    if not rows_10y:

        raise ValueError(
            "No US 10Y observations returned by Treasury"
        )

    us2 = build_record(
        "US 2Y",
        rows_2y,
        "%",
        "U.S. Department of the Treasury",
        "https://home.treasury.gov/"
        "resource-center/data-chart-center/"
        "interest-rates"
    )

    us10 = build_record(
        "US 10Y",
        rows_10y,
        "%",
        "U.S. Department of the Treasury",
        "https://home.treasury.gov/"
        "resource-center/data-chart-center/"
        "interest-rates"
    )

    add_basis_point_changes(
        us2,
        rows_2y
    )

    add_basis_point_changes(
        us10,
        rows_10y
    )

    return us2, us10


# =========================================================
# RESERVE BANK OF AUSTRALIA
#
# Used for:
# AU 2Y
# AU 10Y
# =========================================================

def rba_f2():

    urls = [

        "https://www.rba.gov.au/"
        "statistics/tables/xls/f02d.xlsx",

        "https://www.rba.gov.au/"
        "statistics/tables/xls/f02hist.xlsx"

    ]

    last_error = None

    for url in urls:

        try:

            content = (
                download_file(url)
                .content
            )

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
                        min(
                            15,
                            raw.shape[0]
                        )
                    ):

                        value = str(
                            raw.iat[
                                row,
                                column
                            ]
                        )

                        if value != "nan":

                            parts.append(
                                value
                            )

                    labels.append(
                        " ".join(parts)
                    )

                # -------------------------
                # FIND 2Y COLUMN
                # -------------------------

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

                # -------------------------
                # FIND 10Y COLUMN
                # -------------------------

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
                    or
                    column_10y is None
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

                        (
                            column_2y,
                            rows_2y
                        ),

                        (
                            column_10y,
                            rows_10y
                        )

                    ]:

                        value = pd.to_numeric(
                            raw.iat[
                                row,
                                column
                            ],
                            errors="coerce"
                        )

                        if not pd.isna(value):

                            target.append({
                                "date":
                                    date
                                    .date()
                                    .isoformat(),

                                "value":
                                    float(value)
                            })

                if (
                    rows_2y
                    and
                    rows_10y
                ):

                    au2 = build_record(
                        "AU 2Y",
                        rows_2y,
                        "%",
                        "Reserve Bank of Australia — F2",
                        "https://www.rba.gov.au/"
                        "statistics/tables/"
                    )

                    au10 = build_record(
                        "AU 10Y",
                        rows_10y,
                        "%",
                        "Reserve Bank of Australia — F2",
                        "https://www.rba.gov.au/"
                        "statistics/tables/"
                    )

                    add_basis_point_changes(
                        au2,
                        rows_2y
                    )

                    add_basis_point_changes(
                        au10,
                        rows_10y
                    )

                    return au2, au10

        except Exception as error:

            last_error = error

    raise RuntimeError(
        "Could not parse RBA F2: "
        f"{last_error}"
    )


# =========================================================
# LOAD PREVIOUS SNAPSHOT
#
# If a source temporarily fails in future,
# retain the previous successful value and mark it stale.
# =========================================================

previous = load_json(
    OUT,
    {
        "markets": {}
    }
)

markets = {}
errors = {}


# =========================================================
# YAHOO MARKET FEEDS
# =========================================================

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
    )

]


# =========================================================
# DOWNLOAD YAHOO FEEDS
# =========================================================

for key, function in feeds:

    try:

        markets[key] = function()

    except Exception as error:

        errors[key] = str(
            error
        )

        # Preserve last successful value
        # rather than deleting an instrument.

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


# =========================================================
# U.S. TREASURY YIELDS
# =========================================================

try:

    (
        markets["us2y"],
        markets["us10y"]
    ) = treasury_yields()

except Exception as error:

    errors["us_treasury"] = str(
        error
    )

    for key in [
        "us2y",
        "us10y"
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


# =========================================================
# AUSTRALIAN GOVERNMENT YIELDS
# =========================================================

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


# =========================================================
# BUILD FINAL SNAPSHOT
# =========================================================

now = datetime.now(
    TZ
)

payload = {

    "schema_version":
        3,

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

        "Yahoo Finance is used for traded-market daily closes.",

        "U.S. Department of the Treasury is used for US 2Y and US 10Y government yields.",

        "Reserve Bank of Australia F2 is used for Australian 2Y and 10Y government yields.",

        "Observation dates are preserved because different markets and sources close or publish at different times.",

        "If a source temporarily fails, the previous successful observation is retained and marked stale."
    ]
}


# =========================================================
# WRITE MARKET_DATA.JSON
# =========================================================

OUT.write_text(

    json.dumps(
        payload,
        indent=2,
        ensure_ascii=False
    )

    + "\n"
)


# =========================================================
# UPDATE 90-SNAPSHOT HISTORY
# =========================================================

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


# =========================================================
# GITHUB ACTION LOG
# =========================================================

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
