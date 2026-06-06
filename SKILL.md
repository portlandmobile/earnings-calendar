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

#### Step 1 — Get today's reporting list
- Read the latest weekly report: `~/MyVault/Projects/Trading/Screeners/earnings_week_YYYY-MM-DD.md`
- Filter to companies reporting **today** (match the date column)
- If no weekly report exists for this week, fall back to AInvest to find today's reporters
- Save the ticker list for reference

#### Step 2 — Process each stock individually (context-safe)
For **each** company in today's list, do NOT batch them:

1. **Pull current quarter actuals** via the Google Finance script:
   ```bash
   python3 {skillDir}/gfinance_earnings.py TICKER:EXCHANGE
   ```
   - `TICKER` = stock ticker, `EXCHANGE` = `NYSE`, `NASDAQ`, or `AMEX`
   - Output is saved as a JSON file. Store in a temp folder (e.g., `{skillDir}/.temp_earnings/`)
   - Parse the JSON to extract: Estimate EPS, Actual EPS, EPS Surprise %, Estimate Revenue, Actual Revenue, Revenue Surprise %
   - If the script fails or returns an error, flag the ticker as `❌ Error` and move on

2. **Pull prior quarter actuals** from the weekly report (`earnings_weekly_YYYY-MM-DD.md`) already loaded in Step 1:
   - Use the Q4 Prior EPS and Q4 Prior Revenue columns for the prior quarter comparison

3. **Build the earnings block** for that single ticker using a SINGLE consolidated table:
   - One table with columns: `Metric | Prior Quarter | Estimate | Actual | Surprise | QoQ Change`
   - Two rows: EPS and Revenue
   - Calculate QoQ as `(actual − prior) / prior` for both EPS and Revenue
   - If prior quarter EPS or Revenue is N/A, show N/A for that metric
   - Add metadata line below the table: `**Price Δ:** +/-X% | **Timing:** Before Market / After Close / TAS | **Mkt Cap:** $X.XXB/M` (from weekly report)
   - Add `**Brief commentary:** 1-2 sentences on the headline takeaway`
   - Separator `---` after each ticker block

4. **Append the block to a temp file**: `~/MyVault/Projects/Trading/Screeners/earnings_temp_{YYYY-MM-DD}_morning.md` or `earnings_temp_{YYYY-MM-DD}_afternoon.md`

5. **Clean up** — delete the temp JSON files after parsing

**Processing scope:** Only process companies with market cap > $500M ("Major" companies from the weekly report). Limit to US-exchange listed companies (NYSE/NASDAQ/AMEX). Skip OTC ADRs. This keeps the run manageable (~25-30 companies max instead of 150+).

#### Step 3 — Stitch the full report + handle pending data
After all tickers are processed:
1. Read the temp file and assemble the full report
2. **Handle errors/incomplete data:** Any ticker marked `❌ Error` should be flagged with a note explaining the data couldn't be retrieved
3. Save as: `~/MyVault/Projects/Trading/Screeners/earnings_full_{YYYY-MM-DD}.md`

#### Step 4 — Handle error carry-forward
**Before ranking**, check if a previous day's temp file exists with error tickers:
- If `earnings_temp_{YYYY-MM-1}_afternoon.md` or `_morning.md` has `❌ Error` tickers, attempt to re-fetch their data
- If successful, update with actual data
- If still errored, carry forward with the error flag

#### Step 5 — Select top 15 and deliver
Rank companies by these priorities (in order):
1. **US-listed** (prioritize over ADRs/international)
2. **Market cap** (larger first)
3. **Positive EPS surprise %** (bigger beats rank higher)
4. **Positive revenue surprise %**
5. **Both EPS and revenue beat** (strongest signal)

- **Error tickers:** Include in the full report with `❌ Error` flag and a note. Do NOT include in the top 15 delivery unless no other options exist.
- Save top 15 as: `~/MyVault/Projects/Trading/Screeners/earnings_top15_{YYYY-MM-DD}.md`
- Deliver the **full report** to the group chat (same format as the file, including the Quick Summary table with all columns)
- The full report is also saved as a reference file
- If any tickers from the top 15 were errored, add a note: "❌ {N} ticker(s) from this list had data retrieval errors — will be updated in the next check"

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

- **Exchange format required** — Always use `TICKER:EXCHANGE` format (e.g., `AAPL:NASDAQ`, `BRC:NYSE`). Supported exchanges: `NYSE`, `NASDAQ`, `AMEX`.
- **Process scope** — Only major companies (market cap > $1B) from the weekly report. Limit to NYSE/NASDAQ/AMEX. Skip OTC ADRs.
- **Context safety** — Never hold more than one ticker's data in context at a time. Write to file after each ticker.
- **Error handling** — If `gfinance_earnings.py` fails for a ticker, flag as `❌ Error` and move on. Don't let one failure block the rest.
- **Error carry-forward** — Re-attempt errored tickers from the previous day before ranking. Don't include errored tickers in the top 15 delivery unless no other options exist.
- **Temp file cleanup** — Delete JSON files from the temp folder after parsing to avoid clutter.

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
