#!/usr/bin/env python3
"""
Google Finance Earnings Scraper

Extracts EPS (actual/estimate) and Revenue (actual/estimate)
from the Google Finance earnings tab for a given ticker.

Usage:
    python gfinance_earnings.py BRC:NYSE
    python gfinance_earnings.py AAPL:NASDAQ
"""

import json
import re
import sys
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_page(ticker_exchange: str) -> BeautifulSoup:
    url = f"https://www.google.com/finance/beta/quote/{ticker_exchange}?tab=earnings"
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def find_label_value(soup: BeautifulSoup, label_prefix: str) -> tuple[str | None, str | None]:
    """
    Find a label div whose text is exactly label_prefix, or label_prefix
    followed by a currency code like '(USD)' or '(CNY)'.
    Returns (sibling_value_text, currency).
    """
    pattern = re.compile(
        r"^" + re.escape(label_prefix) + r"(\s*\(([A-Z]+)\))?$"
    )
    for div in soup.find_all("div"):
        text = div.get_text(strip=True)
        m = pattern.match(text)
        if m:
            currency = m.group(2) or "USD"
            sibling = div.find_next_sibling("div")
            if sibling:
                return sibling.get_text(" ", strip=True), currency
    return None, None


def parse_eps(raw: str) -> dict:
    """
    Raw looks like: '$1.50 / $1.34 +11.52% beat'  (USD)
                 or 'CN¥12.06 / CN¥11.43 +5.52% beat'  (CNY)
    Extracts numeric values regardless of currency symbol.
    """
    # Match numbers optionally preceded by a currency symbol (e.g. $, CN¥, €, ¥)
    values = re.findall(r"[^\d\s]*[\d]+\.[\d]+", raw)
    # Keep only values that are currency amounts (not percentages)
    values = [v for v in values if "%" not in v]
    surprise = re.search(r"([+-][\d.]+%)", raw)
    result = "beat" if "beat" in raw.lower() else ("miss" if "miss" in raw.lower() else "")
    return {
        "actual":   values[0] if len(values) > 0 else "N/A",
        "estimate": values[1] if len(values) > 1 else "N/A",
        "surprise": surprise.group(1) if surprise else "N/A",
        "result":   result,
    }


def parse_revenue(raw: str) -> dict:
    """
    Raw looks like: '435.24M / 406.07M +7.18% beat'
    Returns {'actual': '435.24M', 'estimate': '406.07M', 'surprise': '+7.18%', 'result': 'beat'}
    """
    # Match values like 435.24M, 1.2B, 900K
    values = re.findall(r"[\d.]+[MBK]?(?=\s|/|$)", raw)
    # Filter out the percentage
    values = [v for v in values if not v.endswith("%") and re.match(r"[\d.]+[MBK]?$", v)]
    surprise = re.search(r"([+-][\d.]+%)", raw)
    result = "beat" if "beat" in raw.lower() else ("miss" if "miss" in raw.lower() else "")
    return {
        "actual":   values[0] if len(values) > 0 else "N/A",
        "estimate": values[1] if len(values) > 1 else "N/A",
        "surprise": surprise.group(1) if surprise else "N/A",
        "result":   result,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python gfinance_earnings.py TICKER:EXCHANGE")
        print("Example: python gfinance_earnings.py BRC:NYSE")
        sys.exit(1)

    ticker_exchange = sys.argv[1].upper()
    print(f"Fetching earnings data for {ticker_exchange}...\n")

    soup = fetch_page(ticker_exchange)

    period, _        = find_label_value(soup, "Fiscal Period")
    raw_eps, eps_cur = find_label_value(soup, "EPS / Est.")
    raw_rev, rev_cur = find_label_value(soup, "Revenue / Est.")

    if not raw_eps and not raw_rev:
        print("No earnings data found. The ticker may be wrong or the page structure changed.")
        sys.exit(1)

    eps     = parse_eps(raw_eps or "")
    revenue = parse_revenue(raw_rev or "")

    result = {
        "ticker":   ticker_exchange,
        "period":   period or "N/A",
        "currency": eps_cur or rev_cur or "USD",
        "eps":      eps,
        "revenue":  revenue,
    }

    out_file = f"{ticker_exchange.replace(':', '_')}_earnings.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))
    print(f"\nSaved to: {out_file}")


if __name__ == "__main__":
    main()
