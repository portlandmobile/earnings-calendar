#!/usr/bin/env python3
"""
Test script: pull URBN's last-quarter EPS and Revenue from SEC EDGAR directly,
compare against what earnings_weekly.py actually gets (via SECClient + yfinance).

Run: python3 test_urbn_eps.py
"""

import sys
import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

TICKER = sys.argv[1].upper() if len(sys.argv) > 1 else "URBN"

# ── 1. SEC EDGAR direct ──────────────────────────────────────────────────────

def resolve_cik(ticker):
    url = "https://efts.sec.gov/LATEST/search-index?q=%22{}%22&dateRange=custom&startdt=2000-01-01&enddt=2099-01-01&forms=10-K".format(ticker)
    # Simpler: use the company tickers JSON
    r = requests.get(
        "https://www.sec.gov/files/company_tickers.json",
        headers={"User-Agent": "test-script luv2whitewater@gmail.com"},
        timeout=15,
    )
    r.raise_for_status()
    for entry in r.json().values():
        if entry["ticker"].upper() == ticker.upper():
            return str(entry["cik_str"]).zfill(10)
    return None


def fetch_companyfacts(cik):
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    r = requests.get(url, headers={"User-Agent": "test-script luv2whitewater@gmail.com"}, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_tag(facts, tag, forms=("10-Q", "10-K"), limit=8):
    """Return the most recent `limit` entries for a tag, sorted newest first."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    if tag not in gaap:
        return []
    units = gaap[tag].get("units", {})
    # EPS is USD/shares; revenue/NI is USD
    unit_key = next((k for k in units if "USD" in k), None)
    if not unit_key:
        return []
    entries = [
        e for e in units[unit_key]
        if e.get("form") in forms
    ]
    entries.sort(key=lambda x: x.get("end", ""), reverse=True)
    # deduplicate on (end, form)
    seen, unique = set(), []
    for e in entries:
        key = (e["end"], e["form"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique[:limit]


def compute_q4(annual_val, q1_val, q2_val, q3_val):
    """Q4 = Annual - Q1 - Q2 - Q3 (all must be non-None)."""
    if any(v is None for v in [annual_val, q1_val, q2_val, q3_val]):
        return None
    return annual_val - q1_val - q2_val - q3_val


def sec_direct_report(facts):
    print("\n" + "=" * 60)
    print("1. SEC EDGAR DIRECT")
    print("=" * 60)

    for label, tag in [
        ("EPS Diluted", "EarningsPerShareDiluted"),
        ("Revenue",     "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("Net Income",  "NetIncomeLoss"),
    ]:
        print(f"\n  {label} ({tag})")
        entries = extract_tag(facts, tag)
        if not entries:
            # Revenue fallback
            entries = extract_tag(facts, "Revenues") if label == "Revenue" else []
        if not entries:
            print("    No data found")
            continue

        print(f"  {'Form':<8} {'Filed':<12} {'Period End':<14} {'FY':<6} {'QTR':<6} {'Value'}")
        print(f"  {'-'*7} {'-'*11} {'-'*13} {'-'*5} {'-'*5} {'-'*15}")
        for e in entries:
            val = e.get("val")
            val_str = f"{val:,.4f}" if label == "EPS Diluted" else (f"${val/1e9:.3f}B" if val and abs(val) >= 1e9 else str(val))
            print(f"  {e.get('form',''):<8} {e.get('filed',''):<12} {e.get('end',''):<14} "
                  f"{str(e.get('fy','')):<6} {str(e.get('fp','')):<6} {val_str}")

        # Highlight what earnings_weekly.py would use (index [0])
        top = entries[0]
        print(f"\n  --> earnings_weekly.py takes [0]: form={top.get('form')}, "
              f"end={top.get('end')}, fp={top.get('fp')}, val={top.get('val')}")
        if top.get("form") == "10-K":
            print("  *** WARNING: [0] is a 10-K (ANNUAL) — not a single quarter ***")

        # Attempt Q4 derivation: 10-Q YTD data is cumulative, so Q4 = Annual - Q3_YTD
        # Match by period_end proximity (not FY, since EDGAR FY labels can differ between 10-K and 10-Q)
        print(f"\n  Attempting Q4 derivation (Annual − Q3_YTD, since 10-Q values are cumulative):")
        tenk = [e for e in entries if e.get("form") == "10-K"]
        tenq = [e for e in entries if e.get("form") == "10-Q"]
        if tenk and tenq:
            annual_end = tenk[0].get("end", "")
            annual_val = tenk[0].get("val")
            # Find the most recent 10-Q that ends before the 10-K period_end
            q3_ytd = next((e for e in tenq if e.get("end", "") < annual_end), None)
            if q3_ytd and annual_val is not None and q3_ytd.get("val") is not None:
                q4_val = annual_val - q3_ytd["val"]
                q4_str = f"{q4_val:.4f}" if label == "EPS Diluted" else f"${q4_val/1e9:.3f}B"
                print(f"    Annual ({annual_end}) {annual_val} − Q3_YTD ({q3_ytd['end']}) {q3_ytd['val']} = Q4: {q4_str}")
            else:
                print("    Could not find matching Q3 YTD entry")
        else:
            print("    Insufficient data for derivation")


# ── 2. SECClient path (what earnings_weekly.py actually calls) ───────────────

def sec_client_report():
    print("\n" + "=" * 60)
    print("2. SECClient PATH (earnings_weekly.py primary source)")
    print("=" * 60)

    SEC_API_PATH = os.path.expanduser("~/.openclaw/skills/OC_stock_analysis_trend")
    if SEC_API_PATH not in sys.path:
        sys.path.insert(0, SEC_API_PATH)
    try:
        from sec_api import SECClient
    except ImportError:
        print("  SECClient not importable — skipping")
        return

    client = SECClient()
    data = client.extract_quarterly_data(TICKER, limit=4)
    if not data:
        print("  No data returned")
        return

    for field in ("eps_diluted", "revenue", "net_income"):
        entries = data.get(field, [])
        print(f"\n  {field.upper()} ({len(entries)} entries):")
        for e in entries:
            val = e.get("val")
            val_str = f"{val:,.4f}" if field == "eps_diluted" else (f"${val/1e9:.3f}B" if val and abs(val) >= 1e9 else str(val))
            fp_val = e.get('fp') or e.get('qf') or '?'
            print(f"    form={e.get('form',''):<6} end={e.get('period_end','')} "
                  f"fp={str(fp_val):<4} val={val_str}")
        if entries:
            top = entries[0]
            print(f"  --> [0] used by script: form={top.get('form')}, val={top.get('val')}")
            if top.get("form") == "10-K":
                print("  *** [0] is 10-K (ANNUAL) ***")


# ── 3. yfinance path (earnings_weekly.py fallback) ───────────────────────────

def yfinance_report():
    print("\n" + "=" * 60)
    print("3. YFINANCE PATH (earnings_weekly.py fallback)")
    print("=" * 60)

    tk = yf.Ticker(TICKER)
    qis = tk.quarterly_income_stmt

    if qis is None or qis.empty:
        print("  No quarterly income statement available")
        return

    print(f"\n  Columns (most recent first): {[str(c.date()) for c in qis.columns[:6]]}")

    for row_label in ("Diluted EPS", "Basic EPS", "Total Revenue", "Net Income"):
        if row_label in qis.index:
            vals = [(str(c.date()), qis.loc[row_label, c]) for c in qis.columns[:4]]
            print(f"\n  {row_label}:")
            for date, val in vals:
                if row_label in ("Diluted EPS", "Basic EPS"):
                    val_str = f"${val:.4f}" if pd.notna(val) else "N/A"
                else:
                    val_str = f"${val/1e9:.3f}B" if pd.notna(val) and val else "N/A"
                print(f"    {date}: {val_str}")
            # What earnings_weekly.py would use
            top_val = qis.loc[row_label, qis.columns[0]]
            top_date = str(qis.columns[0].date())
            print(f"  --> [0] used by script: {top_date} = {top_val}")


# ── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Testing EPS/Revenue for {TICKER}")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    print("\nResolving CIK...")
    cik = resolve_cik(TICKER)
    if not cik:
        print("ERROR: Could not resolve CIK")
        sys.exit(1)
    print(f"CIK: {cik}")

    print("Fetching SEC company facts...")
    facts = fetch_companyfacts(cik)

    sec_direct_report(facts)
    sec_client_report()
    yfinance_report()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print("If [0] above shows form=10-K, the script is returning annual EPS/Revenue,")
    print("not a single quarter. The fix is to skip 10-K entries or derive Q4.")
