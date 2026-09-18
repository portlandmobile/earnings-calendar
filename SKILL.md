---
name: earnings-calendar
description: >
  Weekly earnings calendar generator. Pulls earnings data from Yahoo Finance
  for the upcoming week, enriches with prior quarter EPS/Revenue, and outputs
  a formatted markdown report.
user-invocable: true
requires:
  bins:
    - python3
  packages:
    - yfinance
    - pandas
---

# Earnings Calendar Skill

Generates a weekly earnings calendar report from Yahoo Finance data.

---

## Running the Report

### Generate for the current/upcoming week

```bash
python3 {skillDir}/earnings_weekly.py
```

This will:
1. Detect the current week (Monday–Friday)
2. Pull all companies reporting earnings from Yahoo Finance (~400-600/week)
3. Show top 50/day as "Major" (full 8-column table with prior quarter data)
4. Show next 20/day as "Other" (simplified table)
5. Save to `~/MyVault/Projects/Trading/Screeners/earnings_week_YYYY-MM-DD.md`

### Custom date range

```bash
python3 {skillDir}/earnings_weekly.py --start 2026-05-18 --end 2026-05-22
```

### Other options

```bash
# Higher market cap filter (e.g., $100M+)
python3 {skillDir}/earnings_weekly.py --min-cap 100000000

# Custom output path
python3 {skillDir}/earnings_weekly.py --output /path/to/report.md
```

---

## Output Format

The report is saved to `~/MyVault/Projects/Trading/Screeners/earnings_week_YYYY-MM-DD.md`

Format (matches Yahoo Finance display):
- **Major Companies** (top 50/day by market cap): Full 11-column table
  - Company | Symbol | Date | Mkt Cap | Q4 Prior EPS | Q4 Prior Rev | EPS Est | Rev Est | EPS %Δ | Rev %Δ | Price Δ | Timing
  - EPS %Δ: % change from prior quarter EPS to estimate
  - Rev %Δ: % change from prior quarter revenue to estimate
  - Price Δ: stock price change from ~3 months ago to day before earnings
- **Other Companies** (next 20/day by market cap): Simplified table
  - Company | Symbol | Date | Mkt Cap | EPS Est | Timing
- **Key Takeaways** section at bottom with total count and per-day breakdown
- Note: Shows ~200-300 of ~400-600 total companies per week (top 70/day)

---

## Data Sources

- **Earnings calendar:** Yahoo Finance (yfinance Calendars API)
- **Prior quarter EPS/Revenue:** yfinance quarterly income statements
- **Revenue estimates:** yfinance ticker calendar data

---

## Post-Earnings Analysis (After Release)

**When to use:** Triggered by cron at two times each trading day:
- **Morning:** ~1 hour before market open (8:30 AM ET) — captures pre-market earnings
- **Afternoon:** ~4 hours after market close (6:00 PM PST / 9:00 PM ET) — captures after-hours earnings

### Process

#### Step 1 — Run the daily pipeline script

```bash
python3 {skillDir}/earnings_daily.py --date YYYY-MM-DD --period morning|afternoon
```

This single command does everything in one pass:
- Auto-detects the weekly report for the current week
- Fetches EPS/Revenue actuals from Google Finance for each ticker (no temp files)
- Ranks results by: mkt cap → EPS surprise → rev surprise → both-beat
- Saves three files to `~/MyVault/Projects/Trading/Screeners/`:
  - `earnings_full_{YYYY-MM-DD}.md` — full report, all tickers
  - `earnings_temp_{YYYY-MM-DD}_{period}.md` — same as full (reference copy)
  - `earnings_top15_{YYYY-MM-DD}.md` — top 15 by ranking

The script prints a summary of fetch results (ok vs error count) and lists any tickers that failed.

**Processing scope:** Only "Major" companies from the weekly report (market cap > $1B, NYSE/NASDAQ/AMEX listed). This is ~25–30 companies per run.

#### Step 2 — Deliver
- Read `earnings_top15_{YYYY-MM-DD}.md` and deliver to the group chat
- If any tickers errored, the file already includes the `❌` note at the bottom

### Report Format

**Full report** (`earnings_full_{YYYY-MM-DD}.md`):
```
## 📊 Earnings — {Date}

### 1. **{TICKER}** — {Company Name} ({Quarter})
| Metric | Prior Quarter | Estimate | Actual | Surprise | QoQ Change |
|---|---|---|---|---|---|
| **EPS** | $X.XX | $X.XX | $X.XX | +/-% ✅/❌ | +/-X% |
| **Revenue** | $X.XXB/M | $X.XXB/M | $X.XXB/M | +/-% ✅/❌ | +/-X% |

**Price Δ:** +/-X% | **Timing:** Before Market / After Close / TAS | **Mkt Cap:** $X.XXB/M

**Brief commentary:** 1-2 sentences on the headline takeaway.

---

### 📌 Quick Summary

| Ticker | EPS Beat/Miss | Rev Beat/Miss | QoQ EPS | QoQ Rev | QoQ Price |
|---|---|---|---|---|---|
| **X** | ✅/❌/+X% | ✅/❌/+X% | +/-X% | +/-X% | +/-X% |

**Standout:** Highlight the clearest beat or miss.

⚠️ *Data sourced from {sources}. Verify with official earnings release.*
```

**Telegram delivery** (same as full report file):
```
## 📊 Earnings — {Date}

### 1. **{TICKER}** — {Company Name} ({Quarter})
| Metric | Prior Quarter | Estimate | Actual | Surprise | QoQ Change |
|---|---|---|---|---|---|
| **EPS** | $X.XX | $X.XX | $X.XX | +/-% ✅/❌ | +/-X% |
| **Revenue** | $X.XXB/M | $X.XXB/M | $X.XXB/M | +/-% ✅/❌ | +/-X% |

**Price Δ:** +/-X% | **Timing:** Before Market / After Close / TAS | **Mkt Cap:** $X.XXB/M

**Brief commentary:** 1-2 sentences.

---

### 📌 Quick Summary

| Ticker | EPS Beat/Miss | Rev Beat/Miss | QoQ EPS | QoQ Rev | QoQ Price |
|---|---|---|---|---|---|
| **X** | ✅/❌/+X% | ✅/❌/+X% | +/-X% | +/-X% | +/-X% |

**Standout:** {Ticker} — {1-sentence reason}

❌ *{N} ticker(s) had data retrieval errors — will be updated in the next check.*
⚠️ *Data sourced from gfinance_earnings.py. Verify with official earnings release.*
```

### Tools & Sources

- **Weekly calendar report** (`~/MyVault/Projects/Trading/Screeners/earnings_week_YYYY-MM-DD.md`) — Authoritative source for today's reporting list and prior quarter data; read first before any data pull
- **gfinance_earnings.py** (`{skillDir}/gfinance_earnings.py`) — Pulls current quarter actuals (EPS + Revenue) from Google Finance; output is JSON
- **Temp folder** (`{skillDir}/.temp_earnings/`) — Store JSON outputs per ticker, delete after parsing
- **File I/O** — Write each ticker's block to temp file, then stitch; save full + top 15 separately

### ⚠️ Key Constraints

- **Single command** — Run `earnings_daily.py`, do not manually fetch tickers one-by-one or write intermediate files.
- **Process scope** — `earnings_daily.py` reads only "Major" companies (12-column rows) from the weekly report; minor/other companies are skipped automatically.
- **Error handling** — The script flags failed tickers as `❌ Error` and continues. Do not retry in a loop; the next scheduled run will catch them.
- **Exchange resolution** — The script tries NASDAQ → NYSE → AMEX automatically; no manual exchange specification needed.

### Notes

- For companies with no estimate available, note "no estimate" rather than guessing
- Flag data discrepancies between sources (e.g., different revenue figures for the same quarter)
- For micro-caps, note that percentage surprises on tiny bases can be misleading
- Always distinguish between observation (beat/miss) and recommendation
- This is **not** the weekly calendar generation — it's a focused, on-demand pull of actuals after release

---

## Automation

This skill is designed to run weekly via cron job (Friday evenings).
Cron ID: `0877f691-2d83-40f5-ad5b-892efefdc548`
Schedule: Every Friday at 8:00 PM PST
