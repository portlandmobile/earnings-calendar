#!/usr/bin/env python3
"""
Daily Post-Earnings Report Generator

Reads today's tickers from the weekly earnings calendar report, fetches
actual EPS/Revenue from Google Finance, and generates a full earnings report
with top-15 ranking — in a single pass with no intermediate files.

Usage:
    python3 earnings_daily.py [--date YYYY-MM-DD] [--period morning|afternoon]
    python3 earnings_daily.py --weekly-report /path/to/earnings_week_YYYY-MM-DD.md
"""

import argparse
import datetime
import glob
import os
import re
import sys
import requests
from bs4 import BeautifulSoup

OUTPUT_DIR = os.path.expanduser("~/MyVault/Projects/Trading/Screeners")

GFINANCE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Daily post-earnings report generator")
    p.add_argument("--date", default=None, help="Date to process (YYYY-MM-DD, default: today)")
    p.add_argument("--period", choices=["morning", "afternoon"], default="afternoon")
    p.add_argument("--weekly-report", default=None, help="Path to weekly report (auto-detected if omitted)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Weekly report parsing
# ---------------------------------------------------------------------------

def find_weekly_report(date_str):
    """Find the weekly report for the week containing date_str."""
    dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    monday = dt - datetime.timedelta(days=dt.weekday())
    output_dir = os.path.expanduser(OUTPUT_DIR)

    for delta_weeks in [0, -1, 1]:
        week_start = monday + datetime.timedelta(weeks=delta_weeks)
        path = os.path.join(output_dir, f"earnings_week_{week_start}.md")
        if os.path.exists(path):
            return path

    candidates = sorted(glob.glob(os.path.join(output_dir, "earnings_week_*.md")), reverse=True)
    return candidates[0] if candidates else None


def parse_weekly_report(report_path, target_date):
    """
    Parse the weekly report and return tickers reporting on target_date.

    Expects the Major Companies table format from earnings_weekly.py:
    | Company | Symbol | Date | Mkt Cap | Q4 Prior EPS | Q4 Prior Rev | EPS Est | Rev Est | EPS %Δ | Rev %Δ | Price Δ | Timing |

    Returns list of dicts with keys:
        company, symbol, date, mkt_cap_str, prior_eps_str, prior_rev_str,
        eps_est_str, rev_est_str, timing
    """
    with open(report_path) as f:
        lines = f.readlines()

    tickers = []
    for line in lines:
        line = line.rstrip()
        if not line.startswith("|") or "---|" in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c != ""]
        # Major table has 12 columns; skip header and short rows
        if len(cols) < 12 or cols[1] in ("Symbol", ""):
            continue
        if cols[2] != target_date:
            continue
        tickers.append({
            "company":       cols[0],
            "symbol":        cols[1],
            "date":          cols[2],
            "mkt_cap_str":   cols[3],
            "prior_eps_str": cols[4],
            "prior_rev_str": cols[5],
            "eps_est_str":   cols[6],
            "rev_est_str":   cols[7],
            "timing":        cols[11],
        })
    return tickers


# ---------------------------------------------------------------------------
# Google Finance fetching
# ---------------------------------------------------------------------------

def _find_label_value_with_currency(soup, label_prefix):
    pattern = re.compile(r"^" + re.escape(label_prefix) + r"(\s*\(([A-Z]+)\))?$")
    for div in soup.find_all("div"):
        m = pattern.match(div.get_text(strip=True))
        if m:
            currency = m.group(2) or "USD"
            sib = div.find_next_sibling("div")
            if sib:
                return sib.get_text(" ", strip=True), currency
    return None, None


def _find_label_value(soup, label_prefix):
    val, _ = _find_label_value_with_currency(soup, label_prefix)
    return val


def _parse_eps(raw):
    values = re.findall(r"[^\d\s]*[\d]+\.[\d]+", raw)
    values = [v for v in values if "%" not in v]
    surprise_m = re.search(r"([+-][\d.]+%)", raw)
    result = "beat" if "beat" in raw.lower() else ("miss" if "miss" in raw.lower() else "")
    return {
        "actual":   values[0] if len(values) > 0 else "N/A",
        "estimate": values[1] if len(values) > 1 else "N/A",
        "surprise": surprise_m.group(1) if surprise_m else "N/A",
        "result":   result,
    }


def _parse_revenue(raw):
    values = re.findall(r"[\d.]+[MBK]?(?=\s|/|$)", raw)
    values = [v for v in values if not v.endswith("%") and re.match(r"[\d.]+[MBK]?$", v)]
    surprise_m = re.search(r"([+-][\d.]+%)", raw)
    result = "beat" if "beat" in raw.lower() else ("miss" if "miss" in raw.lower() else "")
    return {
        "actual":   values[0] if len(values) > 0 else "N/A",
        "estimate": values[1] if len(values) > 1 else "N/A",
        "surprise": surprise_m.group(1) if surprise_m else "N/A",
        "result":   result,
    }


def fetch_gfinance_earnings(symbol):
    """
    Fetch earnings actuals for a ticker from Google Finance.
    Tries NASDAQ, NYSE, AMEX in order until one returns data.
    Returns dict or None on failure.
    """
    for exchange in ["NASDAQ", "NYSE", "AMEX"]:
        ticker_exchange = f"{symbol}:{exchange}"
        url = f"https://www.google.com/finance/beta/quote/{ticker_exchange}?tab=earnings"
        try:
            resp = requests.get(url, headers=GFINANCE_HEADERS, timeout=20)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            period = _find_label_value(soup, "Fiscal Period")
            raw_eps, eps_cur = _find_label_value_with_currency(soup, "EPS / Est.")
            raw_rev, _ = _find_label_value_with_currency(soup, "Revenue / Est.")

            if not raw_eps and not raw_rev:
                continue

            return {
                "ticker_exchange": ticker_exchange,
                "period":   period or "N/A",
                "currency": eps_cur or "USD",
                "eps":      _parse_eps(raw_eps or ""),
                "revenue":  _parse_revenue(raw_rev or ""),
            }
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Number helpers
# ---------------------------------------------------------------------------

def parse_num(s):
    """Parse strings like '$0.55', '3.35B', '($0.75)', 'N/A' to float or None."""
    if not s or s == "N/A":
        return None
    s = str(s).strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace("$", "").replace(",", "")
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
    if s and s[-1] in mult:
        try:
            return float(s[:-1]) * mult[s[-1]]
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def fmt_qoq(actual_str, prior_str):
    """Calculate QoQ % change from string representations."""
    actual = parse_num(actual_str)
    prior = parse_num(prior_str)
    if actual is None or prior is None or prior == 0:
        return "N/A"
    pct = (actual - prior) / abs(prior) * 100
    return f"{pct:+.1f}%"


def result_icon(result):
    return "✅" if result == "beat" else ("❌" if result == "miss" else "")


# ---------------------------------------------------------------------------
# Report building
# ---------------------------------------------------------------------------

def _commentary(symbol, eps, rev):
    """Generate 1-2 sentence commentary. Surprise strings already include sign and %."""
    eps_surp = eps["surprise"]  # e.g. "+4.66%" or "-16.88%" — already formatted
    rev_surp = rev["surprise"]
    eps_beat = eps["result"] == "beat"
    rev_beat = rev["result"] == "beat"
    eps_miss = eps["result"] == "miss"
    rev_miss = rev["result"] == "miss"

    if eps_beat and rev_beat:
        return (f"Strong quarter with {symbol} beating on both EPS ({eps_surp}) and "
                f"revenue ({rev_surp}), showing solid demand across the business.")
    if eps_beat and rev_miss:
        return (f"{symbol} beat EPS by {eps_surp} but missed revenue ({rev_surp}), "
                f"indicating cost management but slower top-line growth.")
    if eps_miss and rev_beat:
        return (f"{symbol} beat revenue by {rev_surp} but missed on EPS ({eps_surp}), "
                f"suggesting margin pressure despite strong topline growth.")
    if eps_miss and rev_miss:
        return (f"Weak quarter for {symbol}, missing both EPS ({eps_surp}) and "
                f"revenue ({rev_surp}).")
    return (f"{symbol} reported {eps['actual']} EPS vs {eps['estimate']} estimate; "
            f"revenue {rev['actual']} vs {rev['estimate']} estimate.")


def build_ticker_block(idx, td, actuals):
    """Build the markdown block for a single ticker with actuals."""
    symbol = td["symbol"]
    eps = actuals["eps"]
    rev = actuals["revenue"]
    period = actuals.get("period", "N/A")

    eps_surp_display = f"{eps['surprise']} {result_icon(eps['result'])}".strip()
    rev_surp_display = f"{rev['surprise']} {result_icon(rev['result'])}".strip()
    eps_qoq = fmt_qoq(eps["actual"], td["prior_eps_str"])
    rev_qoq = fmt_qoq(rev["actual"], td["prior_rev_str"])

    lines = [
        f"### {idx}. **{symbol}** — {td['company']} ({period})",
        "| Metric | Prior Quarter | Estimate | Actual | Surprise | QoQ Change |",
        "|---|---|---|---|---|---|",
        f"| **EPS** | {td['prior_eps_str']} | {eps['estimate']} | {eps['actual']} | {eps_surp_display} | {eps_qoq} |",
        f"| **Revenue** | {td['prior_rev_str']} | {rev['estimate']} | {rev['actual']} | {rev_surp_display} | {rev_qoq} |",
        "",
        f"**Price Δ:** N/A | **Timing:** {td['timing']} | **Mkt Cap:** {td['mkt_cap_str']}",
        "",
        f"**Brief commentary:** {_commentary(symbol, eps, rev)}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def build_error_block(idx, td):
    lines = [
        f"### {idx}. **{td['symbol']}** — {td['company']}",
        "",
        "❌ *Data could not be retrieved from Google Finance. Will retry in the next check.*",
        "",
        f"**Timing:** {td['timing']} | **Mkt Cap:** {td['mkt_cap_str']}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def build_quick_summary(items, top_n=15):
    lines = [
        "### 📌 Quick Summary",
        "",
        "| Ticker | EPS Beat/Miss | Rev Beat/Miss | QoQ EPS | QoQ Rev | QoQ Price |",
        "|---|---|---|---|---|---|",
    ]
    for td, act in items[:top_n]:
        if act is None:
            lines.append(f"| **{td['symbol']}** | ❌ Error | ❌ Error | N/A | N/A | N/A |")
            continue
        eps_cell = result_icon(act["eps"]["result"]) or act["eps"]["surprise"]
        rev_cell = result_icon(act["revenue"]["result"]) or act["revenue"]["surprise"]
        eps_qoq = fmt_qoq(act["eps"]["actual"], td["prior_eps_str"])
        rev_qoq = fmt_qoq(act["revenue"]["actual"], td["prior_rev_str"])
        lines.append(f"| **{td['symbol']}** | {eps_cell} | {rev_cell} | {eps_qoq} | {rev_qoq} | N/A |")
    lines.append("")
    return "\n".join(lines)


def build_standout(items):
    best = None
    best_val = None
    for td, act in items:
        if act is None or act["eps"]["surprise"] in ("N/A", "In-line"):
            continue
        # surprise is already like "+4.66%" — strip sign and %
        raw = act["eps"]["surprise"].replace("%", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if val > 0 and (best_val is None or val > best_val):
            best_val = val
            best = (td, act)

    if best is None:
        return "**Standout:** No tickers with valid positive EPS surprise data."
    td, act = best
    surp = act["eps"]["surprise"]
    return (f"**Standout:** {td['symbol']} — Extraordinary {surp} EPS beat "
            f"with {act['eps']['actual']} vs {act['eps']['estimate']} estimate.")


def rank_tickers(results):
    """
    Rank by: success first, then mkt cap desc, EPS surprise desc, rev surprise desc, both-beat flag.
    """
    def sort_key(item):
        td, act = item
        mkt_cap = parse_num(td["mkt_cap_str"]) or 0
        if act is None:
            return (0, mkt_cap, 0.0, 0.0, 0)
        surp_raw = act["eps"]["surprise"]
        eps_surp = 0.0
        if surp_raw not in ("N/A", "In-line"):
            try:
                eps_surp = float(surp_raw.replace("%", ""))
            except ValueError:
                pass
        rev_surp_raw = act["revenue"]["surprise"]
        rev_surp = 0.0
        if rev_surp_raw not in ("N/A", "In-line"):
            try:
                rev_surp = float(rev_surp_raw.replace("%", ""))
            except ValueError:
                pass
        both_beat = 1 if act["eps"]["result"] == "beat" and act["revenue"]["result"] == "beat" else 0
        return (1, mkt_cap, eps_surp, rev_surp, both_beat)

    return sorted(results, key=sort_key, reverse=True)


def _report_header(date_str, period):
    date_fmt = datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime("%b %d, %Y")
    period_label = "Morning Check — Pre-Market" if period == "morning" else "Afternoon Check — After-Hours"
    return f"## 📊 Earnings — {date_fmt} ({period_label})"


def build_full_report(date_str, period, ranked):
    lines = [_report_header(date_str, period), ""]
    for idx, (td, act) in enumerate(ranked, 1):
        lines.append(build_ticker_block(idx, td, act) if act is not None else build_error_block(idx, td))

    error_items = [(td, act) for td, act in ranked if act is None]
    if error_items:
        lines += [
            "### ❌ Error / No Data Tickers",
            "",
            "| Ticker | Company | Timing | Mkt Cap |",
            "|---|---|---|---|",
        ]
        for td, _ in error_items:
            lines.append(f"| {td['symbol']} | {td['company']} | {td['timing']} | {td['mkt_cap_str']} |")
        lines += ["", "*Data could not be retrieved from Google Finance for these tickers.*", ""]

    lines.append(build_quick_summary(ranked, top_n=15))
    lines.append(build_standout(ranked))
    lines.append("")
    if error_items:
        lines.append(f"❌ {len(error_items)} ticker(s) had data retrieval errors — will be updated in the next check.")
        lines.append("")
    lines.append("⚠️ *Data sourced from Google Finance. Verify with official earnings release.*")
    return "\n".join(lines)


def build_top15_report(date_str, period, ranked):
    # Top 15 = best 15 successful results; if fewer, pad with errors
    success = [(td, act) for td, act in ranked if act is not None][:15]
    if len(success) < 15:
        errors = [(td, act) for td, act in ranked if act is None]
        success += errors[:15 - len(success)]
    top15 = success

    lines = [_report_header(date_str, period), ""]
    for idx, (td, act) in enumerate(top15, 1):
        lines.append(build_ticker_block(idx, td, act) if act is not None else build_error_block(idx, td))

    lines.append(build_quick_summary(top15, top_n=15))
    lines.append(build_standout(top15))
    lines.append("")

    error_items = [(td, act) for td, act in top15 if act is None]
    if error_items:
        lines.append(f"❌ {len(error_items)} ticker(s) had data retrieval errors — will be updated in the next check.")
        lines.append("")
    lines.append("⚠️ *Data sourced from Google Finance. Verify with official earnings release.*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    date_str = args.date or datetime.date.today().strftime("%Y-%m-%d")
    period = args.period

    print(f"Earnings daily report — {date_str} ({period})")

    # Find weekly report
    report_path = args.weekly_report or find_weekly_report(date_str)
    if not report_path:
        print("ERROR: No weekly report found. Run earnings_weekly.py first.")
        sys.exit(1)
    print(f"Weekly report: {report_path}")

    # Parse today's tickers
    tickers = parse_weekly_report(report_path, date_str)
    if not tickers:
        print(f"No tickers found for {date_str} in the weekly report.")
        sys.exit(0)
    print(f"Tickers for {date_str}: {[t['symbol'] for t in tickers]} ({len(tickers)} total)")

    # Fetch actuals from Google Finance — single pass, no temp files
    results = []
    ok = 0
    err = 0
    for td in tickers:
        sym = td["symbol"]
        print(f"  {sym}...", end=" ", flush=True)
        actuals = fetch_gfinance_earnings(sym)
        if actuals:
            print(f"ok ({actuals['ticker_exchange']})")
            ok += 1
        else:
            print("error")
            err += 1
        results.append((td, actuals))

    print(f"\nFetch: {ok} ok, {err} errors")

    # Rank and build reports
    ranked = rank_tickers(results)
    full_report = build_full_report(date_str, period, ranked)
    top15_report = build_top15_report(date_str, period, ranked)

    # Save
    output_dir = os.path.expanduser(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)

    full_path  = os.path.join(output_dir, f"earnings_full_{date_str}.md")
    temp_path  = os.path.join(output_dir, f"earnings_temp_{date_str}_{period}.md")
    top15_path = os.path.join(output_dir, f"earnings_top15_{date_str}.md")

    with open(full_path,  "w") as f: f.write(full_report)
    with open(temp_path,  "w") as f: f.write(full_report)
    with open(top15_path, "w") as f: f.write(top15_report)

    print(f"\nSaved:")
    print(f"  {full_path} ({len(full_report)} chars)")
    print(f"  {temp_path} ({len(full_report)} chars)")
    print(f"  {top15_path} ({len(top15_report)} chars)")

    if err:
        error_syms = [td["symbol"] for td, act in results if act is None]
        print(f"\n❌ Errors: {error_syms}")

    print("\nDone.")


if __name__ == "__main__":
    main()
