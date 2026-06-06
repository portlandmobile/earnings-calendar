#!/usr/bin/env python3
"""
Test script: pull quarterly EPS and Revenue from Google Finance ?tab=financials
and cross-check against the existing ?tab=earnings scraper.

Usage:
    python3 test_gfinance_financials.py URBN:NASDAQ
    python3 test_gfinance_financials.py LPG:NYSE
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
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TICKER_EXCHANGE = sys.argv[1].upper() if len(sys.argv) > 1 else "URBN:NASDAQ"


def fetch_page(tab: str) -> tuple[BeautifulSoup, str]:
    url = f"https://www.google.com/finance/beta/quote/{TICKER_EXCHANGE}?tab={tab}"
    print(f"  GET {url}")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser"), resp.text


# ── Section 1: ?tab=earnings (existing approach for reference) ───────────────

def section_earnings_tab(soup: BeautifulSoup):
    print("\n" + "=" * 60)
    print("1. ?tab=earnings  (existing gfinance_earnings.py approach)")
    print("=" * 60)

    def find_label_value(label_prefix):
        pattern = re.compile(r"^" + re.escape(label_prefix) + r"(\s*\(([A-Z]+)\))?$")
        for div in soup.find_all("div"):
            text = div.get_text(strip=True)
            m = pattern.match(text)
            if m:
                currency = m.group(2) or "USD"
                sibling = div.find_next_sibling("div")
                if sibling:
                    return sibling.get_text(" ", strip=True), currency
        return None, None

    period, _        = find_label_value("Fiscal Period")
    raw_eps, eps_cur = find_label_value("EPS / Est.")
    raw_rev, rev_cur = find_label_value("Revenue / Est.")

    print(f"  Fiscal Period : {period}")
    print(f"  EPS / Est.    : {raw_eps}  ({eps_cur})")
    print(f"  Revenue / Est.: {raw_rev}  ({rev_cur})")


# ── Section 2: ?tab=financials — raw HTML inspection ────────────────────────

def section_financials_raw(soup: BeautifulSoup, raw_html: str):
    print("\n" + "=" * 60)
    print("2. ?tab=financials  — page structure inspection")
    print("=" * 60)

    # Check if the page has meaningful content or is a JS shell
    text = soup.get_text(" ", strip=True)
    print(f"  Page text length  : {len(text)} chars")
    print(f"  Raw HTML length   : {len(raw_html)} chars")

    # Look for any JSON blobs embedded in <script> tags
    scripts = soup.find_all("script")
    print(f"  <script> tags     : {len(scripts)}")
    json_scripts = [s for s in scripts if s.string and len(s.string) > 200]
    print(f"  Non-trivial scripts: {len(json_scripts)}")

    # Sniff for EPS / revenue keywords in the raw HTML
    for kw in ["Earnings per share", "EPS", "Revenue", "financials", "quarterly"]:
        count = raw_html.lower().count(kw.lower())
        print(f"  '{kw}' occurrences : {count}")

    # Show first 800 chars of page text so we can see what rendered
    print(f"\n  --- First 800 chars of page text ---")
    print("  " + text[:800].replace("\n", " "))


# ── Section 3: ?tab=financials — try to extract table data ──────────────────

def section_financials_table(soup: BeautifulSoup):
    print("\n" + "=" * 60)
    print("3. ?tab=financials  — table / structured data extraction")
    print("=" * 60)

    # Strategy A: look for <table> elements
    tables = soup.find_all("table")
    print(f"  <table> elements found: {len(tables)}")
    for i, tbl in enumerate(tables[:3]):
        rows = tbl.find_all("tr")
        print(f"  Table {i}: {len(rows)} rows")
        for row in rows[:4]:
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            print(f"    {cells}")

    # Strategy B: look for divs that look like financial row labels
    financial_keywords = [
        "Earnings per share", "Revenue", "Net income", "Operating income",
        "Gross profit", "EPS",
    ]
    print(f"\n  Searching divs for financial row labels...")
    found_any = False
    for div in soup.find_all("div"):
        text = div.get_text(strip=True)
        if any(text.startswith(kw) for kw in financial_keywords):
            # Grab the next few siblings as column values
            siblings = []
            sib = div.find_next_sibling("div")
            for _ in range(6):
                if sib is None:
                    break
                siblings.append(sib.get_text(strip=True))
                sib = sib.find_next_sibling("div")
            print(f"  Label: '{text}'")
            print(f"  Values: {siblings}")
            found_any = True
    if not found_any:
        print("  None found — page likely requires JavaScript to render")

    # Strategy C: look for embedded JSON with financial data
    print(f"\n  Scanning <script> tags for financial data JSON...")
    found_json = False
    for script in soup.find_all("script"):
        src = script.string or ""
        # Google Finance often embeds data in window.google.finance or AF_initDataCallback
        for pattern in [r"AF_initDataCallback\((\{.*?\})\)", r'"EPS"', r'"earningsPerShare"', r'"revenue"']:
            if re.search(pattern, src, re.IGNORECASE | re.DOTALL):
                print(f"  Found pattern '{pattern}' in a script tag (len={len(src)})")
                # Show a snippet around the match
                m = re.search(pattern, src, re.IGNORECASE | re.DOTALL)
                if m:
                    start = max(0, m.start() - 80)
                    end = min(len(src), m.end() + 200)
                    print(f"  Snippet: ...{src[start:end]}...")
                found_json = True
                break

    if not found_json:
        print("  No recognizable financial JSON found in script tags")


# ── Section 4: try alternate Google Finance endpoint ────────────────────────

def section_alternate_endpoints():
    print("\n" + "=" * 60)
    print("4. Alternate endpoints")
    print("=" * 60)

    ticker = TICKER_EXCHANGE.split(":")[0]
    exchange = TICKER_EXCHANGE.split(":")[1] if ":" in TICKER_EXCHANGE else ""

    endpoints = [
        f"https://www.google.com/finance/quote/{TICKER_EXCHANGE}",
        f"https://finance.google.com/finance?q={ticker}&output=json",
    ]

    for url in endpoints:
        try:
            print(f"\n  GET {url}")
            r = requests.get(url, headers=HEADERS, timeout=10)
            print(f"  Status: {r.status_code}  Length: {len(r.text)}")
            snippet = r.text[:300].replace("\n", " ")
            print(f"  Snippet: {snippet}")
        except Exception as e:
            print(f"  Error: {e}")


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Google Finance financials test for: {TICKER_EXCHANGE}")
    print()

    print("Fetching ?tab=earnings ...")
    soup_earnings, _ = fetch_page("earnings")
    section_earnings_tab(soup_earnings)

    print("\nFetching ?tab=financials ...")
    soup_fin, raw_fin = fetch_page("financials")
    section_financials_raw(soup_fin, raw_fin)
    section_financials_table(soup_fin)
    section_alternate_endpoints()
