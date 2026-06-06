#!/usr/bin/env python3
"""
Weekly Earnings Calendar Generator

Pulls earnings data from Yahoo Finance for the upcoming week,
enriches with prior quarter EPS/Revenue from Google Finance financials tab
(standalone quarterly values), and outputs a formatted markdown report.

Usage:
    python3 earnings_weekly.py [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--output PATH] [--min-cap FLOAT] [--top-only]

Defaults:
    - Start: Monday of current week
    - End: Friday of current week
    - Min market cap: $1B (filters out micro-caps)
    - Output: ~/MyVault/Projects/Trading/Screeners/earnings_week_YYYY-MM-DD.md
"""

import argparse
import datetime
import os
import sys
import time

import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup
from yfinance.calendars import Calendars

GFINANCE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Cache resolved TICKER:EXCHANGE strings to avoid redundant HTTP requests
_GFINANCE_EXCHANGE_CACHE = {}

# Output directory
OUTPUT_DIR = os.path.expanduser("~/MyVault/Projects/Trading/Screeners")

# Companies to skip (index funds, ETFs, known noise)
SKIP_SYMBOLS = {
    'SPY', 'QQQ', 'IWM', 'DIA', 'VTI', 'VOO', 'VEA', 'VWO', 'BND', 'TLT',
    'GLD', 'SLV', 'USO', 'UNG', 'XLF', 'XLK', 'XLE', 'XLV', 'XLI', 'XLP',
    'XLU', 'XLRE', 'XLC', 'VIS', 'VCR', 'VDC', 'VGT', 'VHT', 'VAI',
    'VDE', 'VFH', 'VNQ', 'VOX', 'VPU', 'VPL', 'VNM', 'VWO', 'VTV', 'VUG',
    'IWF', 'IWM', 'IJH', 'IJR', 'IVV', 'ITOT', 'SCHB', 'SCHF', 'SCHG',
    'SCHD', 'SCHX', 'SCHA', 'SCHM', 'SCHV', 'SCHW', 'SPTM', 'SPYG', 'SPYV',
}

SKIP_NAMES = [
    'iShares', 'SPDR', 'Invesco', 'Vanguard', 'ARK', 'Global X',
    'WisdomTree', 'First Trust', 'VanEck', 'Direxion', 'ProShares',
    'Market Vectors', 'PowerShares', 'Xtrackers', 'iShares',
    'Innovator', 'Defiance', 'JPMorgan', 'Goldman Sachs',
]

# Ticker mapping: some companies have multiple listings (e.g., BAIDF vs BIDU)
# Use the primary US-listed ticker for data lookups to get consistent results
# Only include mappings that are verified to work
TICKER_MAP = {
    'BAIDF': 'BIDU',
}


def parse_args():
    parser = argparse.ArgumentParser(description="Weekly Earnings Calendar Generator")
    parser.add_argument("--start", type=str, default=None,
                        help="Start date (YYYY-MM-DD). Defaults to Monday of current week.")
    parser.add_argument("--end", type=str, default=None,
                        help="End date (YYYY-MM-DD). Defaults to Friday of current week.")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path. Defaults to Screeners/earnings_week_YYYY-MM-DD.md")
    parser.add_argument("--min-cap", type=float, default=10_000_000,
                        help="Minimum market cap in USD (default: $10M)")
    parser.add_argument("--top-only", action="store_true",
                        help="Only include top N companies by market cap (default: 50)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print progress during prior quarter data fetch")
    return parser.parse_args()


def get_week_dates(start_str=None, end_str=None):
    """Get Monday-Friday of the current or next week, or use provided dates.

    If it is Friday after 4pm EST (or Saturday/Sunday), returns next week.
    """
    if start_str and end_str:
        return start_str, end_str

    from zoneinfo import ZoneInfo
    now_est = datetime.datetime.now(ZoneInfo("America/New_York"))
    today = now_est.date()

    monday = today - datetime.timedelta(days=today.weekday())
    friday = monday + datetime.timedelta(days=4)

    # Advance to next week if past Friday, or if it's Friday after 4pm EST
    past_friday = today > friday
    friday_evening = (today == friday and now_est.hour >= 16)

    if past_friday or friday_evening:
        monday = monday + datetime.timedelta(weeks=1)
        friday = monday + datetime.timedelta(days=4)

    return monday.strftime("%Y-%m-%d"), friday.strftime("%Y-%m-%d")


def fetch_earnings_calendar(start, end, min_cap=None):
    """Fetch earnings calendar from yfinance for the date range."""
    cal = Calendars(start=start, end=end)

    all_data = []
    offset = 0
    max_pages = 8  # Safety limit (yfinance returns ~100/page, some weeks have 600+)

    for page in range(max_pages):
        data = cal.get_earnings_calendar(
            limit=100,
            offset=offset,
            filter_most_active=False,
            market_cap=min_cap if min_cap else None,
        )

        if len(data) == 0:
            break

        all_data.append(data)

        # If we got fewer than 100, we're done
        if len(data) < 100:
            break

        offset += 100
        time.sleep(0.5)  # Rate limit

    if not all_data:
        print("ERROR: No earnings data returned")
        sys.exit(1)

    # Symbol is the index, not a column — concat without ignore_index
    # then reset to make Symbol a regular column
    df = pd.concat(all_data)
    df = df.reset_index()
    return df


def _parse_gfinance_value(raw):
    """Parse Google Finance formatted values like '1.33B', '848.24M', '1.16', '-'."""
    if not raw or raw.strip() in ('-', '—', 'N/A', ''):
        return None
    raw = raw.strip().replace(',', '')
    mult = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    if raw[-1] in mult:
        try:
            return float(raw[:-1]) * mult[raw[-1]]
        except ValueError:
            return None
    try:
        return float(raw)
    except ValueError:
        return None


def _fetch_gfinance_fin_rows(ticker_exchange):
    """Fetch the financials table rows for TICKER:EXCHANGE. Returns list of <tr> or None."""
    url = f"https://www.google.com/finance/beta/quote/{ticker_exchange}?tab=financials"
    try:
        resp = requests.get(url, headers=GFINANCE_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tbl in soup.find_all("table"):
            rows = tbl.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if cells and cells[0].get_text(strip=True) == "Revenue":
                    return rows
    except Exception:
        pass
    return None


def get_prior_quarter_gfinance(ticker_symbol):
    """Get prior quarter EPS and Revenue from Google Finance financials tab.

    Returns the most recent standalone quarterly values directly — no YTD math needed.
    Tries NASDAQ, NYSE, NYSEARCA, NYSEAMERICAN in order; caches the hit exchange.
    Returns (eps, revenue) floats or (None, None) on failure.
    """
    lookup = TICKER_MAP.get(ticker_symbol, ticker_symbol)

    if lookup in _GFINANCE_EXCHANGE_CACHE:
        exchanges = [_GFINANCE_EXCHANGE_CACHE[lookup]]
    else:
        exchanges = ['NASDAQ', 'NYSE', 'NYSEARCA', 'NYSEAMERICAN']

    for exchange in exchanges:
        rows = _fetch_gfinance_fin_rows(f"{lookup}:{exchange}")
        if rows is None:
            continue

        _GFINANCE_EXCHANGE_CACHE[lookup] = exchange

        eps = None
        revenue = None
        for row in rows[1:]:  # skip header row
            cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
            if not cells:
                continue
            label = cells[0]
            if label == "Earnings per share" and len(cells) > 1:
                eps = _parse_gfinance_value(cells[-1])
            elif label == "Revenue" and len(cells) > 1:
                revenue = _parse_gfinance_value(cells[-1])
            if eps is not None and revenue is not None:
                break

        if eps is not None or revenue is not None:
            return eps, revenue

    return None, None


def get_prior_quarter_data(ticker_symbol):
    """Get prior quarter EPS and Revenue.

    Uses Google Finance financials tab as primary source (standalone quarterly values),
    falls back to yfinance quarterly_income_stmt.
    """
    # Primary: Google Finance (standalone quarterly, no YTD math needed)
    eps, rev = get_prior_quarter_gfinance(ticker_symbol)
    if eps is not None or rev is not None:
        return eps, rev

    # Fallback: yfinance
    lookup_symbol = TICKER_MAP.get(ticker_symbol, ticker_symbol)
    try:
        tk = yf.Ticker(lookup_symbol)
        qis = tk.quarterly_income_stmt

        if qis is None or len(qis) < 2:
            return None, None

        cols = qis.columns
        prior = cols[0] if len(cols) > 0 else None
        if prior is None:
            return None, None

        eps = None
        rev = None

        if 'Diluted EPS' in qis.index:
            eps = qis.loc['Diluted EPS', prior]
            if pd.isna(eps) and len(cols) > 1:
                eps = qis.loc['Diluted EPS', cols[1]]
        elif 'Basic EPS' in qis.index:
            eps = qis.loc['Basic EPS', prior]
            if pd.isna(eps) and len(cols) > 1:
                eps = qis.loc['Basic EPS', cols[1]]

        if 'Total Revenue' in qis.index:
            rev = qis.loc['Total Revenue', prior]

        return eps, rev

    except Exception:
        return None, None


def get_revenue_estimate(ticker_symbol):
    """Get revenue estimate from ticker calendar data."""
    # Map to primary ticker for consistent data
    lookup_symbol = TICKER_MAP.get(ticker_symbol, ticker_symbol)
    try:
        tk = yf.Ticker(lookup_symbol)
        cal = tk.calendar
        if cal and 'Revenue Average' in cal:
            return cal['Revenue Average']
        return None
    except Exception:
        return None


def get_eps_estimate(ticker_symbol):
    """Get EPS estimate from ticker calendar data."""
    try:
        tk = yf.Ticker(ticker_symbol)
        cal = tk.calendar
        if cal and 'Earnings Average' in cal:
            val = cal['Earnings Average']
            if val is not None and not pd.isna(val):
                return float(val)
        return None
    except Exception:
        return None


def get_stock_price_change(symbol, earnings_date_str):
    """Get stock price change from last quarter close to day before earnings.
    
    Returns: (price_change_pct, start_price, end_price)
    """
    try:
        tk = yf.Ticker(symbol)
        hist = tk.history(period='3mo')  # Get ~3 months of data
        
        if hist is None or len(hist) < 2:
            return None, None, None
        
        # Parse earnings date as timezone-aware
        try:
            earnings_dt = datetime.datetime.strptime(earnings_date_str, '%Y-%m-%d').replace(tzinfo=hist.index.tzinfo or datetime.timezone.utc)
        except:
            return None, None, None
        
        # Last trading day before earnings
        before_earnings = hist[hist.index < earnings_dt]
        if len(before_earnings) == 0:
            return None, None, None
        end_price = before_earnings['Close'].iloc[-1]
        
        # Last trading day of last quarter (~3 months before earnings)
        # Use the earliest available price as proxy for quarter start
        start_price = hist['Close'].iloc[0]
        
        if start_price == 0 or pd.isna(start_price) or pd.isna(end_price):
            return None, None, None
        
        pct_change = ((end_price - start_price) / start_price) * 100
        return pct_change, start_price, end_price
    except Exception:
        return None, None, None


def format_number(val, is_eps=False):
    """Format a number for display."""
    if val is None or pd.isna(val):
        return "N/A"

    if is_eps:
        if val < 0:
            return f"(${abs(val):.2f})"
        return f"${val:.2f}"

    # Revenue - format in billions or millions
    if abs(val) >= 1e12:
        return f"${val/1e12:.2f}T"
    elif abs(val) >= 1e9:
        return f"${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"${val/1e6:.2f}M"
    elif abs(val) >= 1e3:
        return f"${val/1e3:.2f}K"
    return f"${val:.2f}"


def format_timing(timing):
    """Format earnings timing."""
    if pd.isna(timing):
        return "N/A"
    timing = str(timing).strip()
    if timing == 'BMO':
        return "Before Market"
    elif timing == 'AMC':
        return "After Close"
    elif timing == 'TNS':
        return "Not Specified"
    return timing


def format_date_short(date_str):
    """Format date as 'May 18'."""
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%b %d")
    except:
        return date_str


def format_date_full(date_str):
    """Format date as 'Monday, May 18'."""
    try:
        d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%A, %b %d")
    except:
        return date_str


def generate_report(df, output_path, top_n=50, verbose=False):
    """Generate the markdown report."""
    # Clean data
    df = df.dropna(subset=['Symbol'])
    df = df[~df['Symbol'].isin(SKIP_SYMBOLS)]
    df = df[~df['Company'].str.contains('|'.join(SKIP_NAMES), case=False, na=False)]

    # Sort by market cap descending
    df = df.sort_values('Marketcap', ascending=False)

    # Remove entries with no market cap data (junk)
    if 'Marketcap' in df.columns:
        df = df[df['Marketcap'].notna()]
        df = df[df['Marketcap'] > 0]

    # Parse date strings and group by date
    df['DateParsed'] = pd.to_datetime(df['Event Start Date'], utc=True).dt.strftime('%Y-%m-%d')
    dates = sorted(df['DateParsed'].unique())

    # Group by date
    groups = {}
    for date in dates:
        day_df = df[df['DateParsed'] == date].copy()
        groups[date] = day_df

    # Build report
    lines = []
    lines.append(f"# Earnings Calendar — Week of {format_date_short(dates[0])}–{format_date_short(dates[-1])}, {datetime.datetime.strptime(dates[-1], '%Y-%m-%d').strftime('%Y')}")
    lines.append("")
    lines.append(f"> Compiled: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} PDT")
    lines.append(f"> Source: Yahoo Finance (primary) + investing.com (cross-check for missing tickers)")
    lines.append(f"> Prior quarter data: Google Finance financials tab (with yfinance fallback)")
    lines.append("")

    # Track all tickers for key takeaways
    major_tickers = []
    total_count = 0

    for date_key in sorted(groups.keys()):
        day_df = groups[date_key]
        total_count += len(day_df)

        # Top 50 per day as "Major" (full data), next 20 as "Other", rest hidden
        major = day_df.nlargest(min(top_n, len(day_df)), 'Marketcap').copy()
        others = day_df[~day_df['Symbol'].isin(major['Symbol'])].copy()

        major_tickers.extend(major['Symbol'].tolist())

        day_name = format_date_full(date_key)

        lines.append(f"## {day_name} ({len(day_df)} companies)")
        lines.append("")

        if len(major) > 0:
            lines.append(f"### 🔥 Major Companies ({len(major)})")
            lines.append("")
            lines.append("| Company | Symbol | Date | Mkt Cap | Q4 Prior EPS | Q4 Prior Rev | EPS Est | Rev Est | EPS %Δ | Rev %Δ | Price Δ | Timing |")
            lines.append("|---------|--------|------|---------|--------------|--------------|---------|---------|--------|--------|---------|--------|")

            for rank, (_, row) in enumerate(major.iterrows()):
                symbol = row['Symbol']
                company = row['Company']
                marketcap = row.get('Marketcap')
                # Use ticker map for consistent data lookups
                lookup_symbol = TICKER_MAP.get(symbol, symbol)
                # Get EPS estimate from the primary ticker for accuracy
                eps_est = get_eps_estimate(lookup_symbol)
                timing = format_timing(row.get('Timing'))

                # Only fetch prior quarter data for top 20 per day (keeps it fast)
                if rank < 20:
                    prior_eps, prior_rev = get_prior_quarter_data(symbol)
                    rev_est = get_revenue_estimate(symbol)
                else:
                    prior_eps, prior_rev = None, None
                    rev_est = get_revenue_estimate(symbol)

                # Calculate % changes (cap at ±500% to avoid noise from near-zero denominators)
                eps_pct = None
                rev_pct = None
                if prior_eps is not None and eps_est is not None and not pd.isna(prior_eps) and not pd.isna(eps_est) and prior_eps != 0:
                    eps_pct = ((eps_est - prior_eps) / abs(prior_eps)) * 100
                    eps_pct = max(-500, min(500, eps_pct))  # Cap at ±500%
                if prior_rev is not None and rev_est is not None and not pd.isna(prior_rev) and not pd.isna(rev_est) and prior_rev != 0:
                    rev_pct = ((rev_est - prior_rev) / abs(prior_rev)) * 100
                    rev_pct = max(-500, min(500, rev_pct))  # Cap at ±500%

                # Stock price change
                price_pct, start_p, end_p = get_stock_price_change(symbol, date_key)

                lines.append(
                    f"| {company} | {symbol} | {date_key} | "
                    f"{format_number(marketcap)} | "
                    f"{format_number(prior_eps, is_eps=True)} | "
                    f"{format_number(prior_rev)} | "
                    f"{format_number(eps_est, is_eps=True)} | "
                    f"{format_number(rev_est)} | "
                    f"{f'{eps_pct:+.1f}%' if eps_pct is not None else 'N/A'} | "
                    f"{f'{rev_pct:+.1f}%' if rev_pct is not None else 'N/A'} | "
                    f"{f'{price_pct:+.1f}%' if price_pct is not None else 'N/A'} | "
                    f"{timing} |"
                )

            lines.append("")

        if len(others) > 0:
            # Show top 20 "Other" by market cap to keep it manageable
            others_limited = others.nlargest(min(20, len(others)), 'Marketcap')
            lines.append(f"### Other Companies ({len(others_limited)})")
            lines.append("")
            lines.append("| Company | Symbol | Date | Mkt Cap | EPS Est | Timing |")
            lines.append("|---------|--------|------|---------|---------|--------|")

            for _, row in others_limited.iterrows():
                symbol = row['Symbol']
                company = row['Company']
                marketcap = row.get('Marketcap')
                eps_est = row.get('EPS Estimate')
                timing = format_timing(row.get('Timing'))

                lines.append(
                    f"| {company} | {symbol} | {date_key} | "
                    f"{format_number(marketcap)} | "
                    f"{format_number(eps_est, is_eps=True)} | "
                    f"{timing} |"
                )

            lines.append("")

    # Key Takeaways
    lines.append("## Key Takeaways")
    lines.append("")

    lines.append(f"- **Total companies reporting:** {total_count}")
    lines.append(f"- **Shown:** {total_count - sum(len(groups[d]) - min(top_n, len(groups[d])) - min(20, max(0, len(groups[d]) - top_n)) for d in groups)} (top 50/day Major + top 20/day Other)")
    lines.append(f"- **Top names:** {', '.join(sorted(set(major_tickers))[:15])}")
    lines.append("")

    # Group by day
    for date_key in sorted(groups.keys()):
        count = len(groups[date_key])
        day_major_count = min(top_n, count)
        if day_major_count > 0:
            day_major = groups[date_key].nlargest(day_major_count, 'Marketcap')
            major_names = ', '.join(day_major['Symbol'].tolist())
            lines.append(f"- **{format_date_full(date_key)}:** {count} companies — {major_names}")
        else:
            lines.append(f"- **{format_date_full(date_key)}:** {count} companies")

    lines.append("")
    lines.append("")

    report = '\n'.join(lines)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, 'w') as f:
        f.write(report)

    print(f"Report saved to: {output_path}")
    print(f"Total companies: {total_count}")
    print(f"Major companies: {len(major_tickers)}")

    return report


def _parse_inv_number(s):
    """Parse investing.com formatted numbers like '438.3M', '9.09B', '0.2'."""
    if not s:
        return None
    s = str(s).strip().replace(',', '')
    if not s or s in ('-', '--', 'N/A'):
        return None
    mult = {'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}
    if s[-1] in mult:
        try:
            return float(s[:-1]) * mult[s[-1]]
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_inv_tab(start_str):
    """Return 'thisWeek', 'nextWeek', or 'custom' for the investing.com currentTab param."""
    today = datetime.date.today()
    start = datetime.datetime.strptime(start_str, '%Y-%m-%d').date()
    today_monday = today - datetime.timedelta(days=today.weekday())
    start_monday = start - datetime.timedelta(days=start.weekday())
    delta = (start_monday - today_monday).days
    if delta == 0:
        return 'thisWeek'
    elif delta == 7:
        return 'nextWeek'
    return 'custom'


def fetch_earnings_investing_com(start, end):
    """
    Fetch earnings calendar from investing.com to supplement Yahoo Finance.

    Queries each calendar day individually using currentTab=custom — the
    thisWeek/nextWeek tabs only return already-completed days and miss the
    remainder of the current week.

    Returns a DataFrame with the same column schema as fetch_earnings_calendar,
    or an empty DataFrame on failure.
    """
    inv_headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/124.0.0.0 Safari/537.36'
        ),
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://www.investing.com/earnings-calendar/',
    }

    # Build list of weekdays in the range
    start_dt = datetime.datetime.strptime(start, '%Y-%m-%d').date()
    end_dt   = datetime.datetime.strptime(end,   '%Y-%m-%d').date()
    days = []
    d = start_dt
    while d <= end_dt:
        if d.weekday() < 5:  # Mon-Fri only
            days.append(d.strftime('%Y-%m-%d'))
        d += datetime.timedelta(days=1)

    all_rows = []
    for day in days:
        payload = {
            'dateFrom': day,
            'dateTo':   day,
            'currentTab': 'custom',
            'limit_from': 0,
        }
        try:
            resp = requests.post(
                'https://www.investing.com/earnings-calendar/Service/getCalendarFilteredData',
                headers=inv_headers,
                data=payload,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"WARNING: investing.com fetch failed for {day}: {e}")
            continue

        html = data.get('data', '')
        if not html:
            continue

        soup = BeautifulSoup(html, 'html.parser')
        # Use the date from the request (theDay header may be absent for single-day queries)
        current_date = day
        # Override with parsed header if present (keeps format consistent)
        day_td = soup.find('td', class_='theDay')
        if day_td:
            try:
                current_date = datetime.datetime.strptime(
                    day_td.text.strip(), '%A, %B %d, %Y'
                ).strftime('%Y-%m-%d')
            except ValueError:
                pass

        all_rows.append((soup, current_date))
        time.sleep(0.3)

    rows = []
    for soup, current_date in all_rows:
        for tr in soup.find_all('tr'):
            # Skip date-header rows (date already captured above)
            if tr.find('td', class_='theDay'):
                continue

            company_td = tr.find('td', class_='earnCalCompany')
            if not company_td:
                continue

            # Only include US-listed stocks
            flag_td = tr.find('td', class_='flag')
            if flag_td:
                flag_span = flag_td.find('span')
                if flag_span and 'USA' not in flag_span.get('class', []):
                    continue

            ticker_a = company_td.find('a')
            if not ticker_a:
                continue

            symbol = ticker_a.text.strip()
            if not symbol or symbol.isdigit():
                continue

            name_span = company_td.find(class_='earnCalCompanyName')
            company_name = name_span.text.strip() if name_span else symbol

            tds = tr.find_all('td')
            eps_actual = None
            rev_actual = None
            for td in tds:
                cls = ' '.join(td.get('class', []))
                if 'eps_actual' in cls:
                    eps_actual = _parse_inv_number(td.text.strip())
                elif 'rev_actual' in cls:
                    rev_actual = _parse_inv_number(td.text.strip())

            leftstrong = tr.find_all('td', class_='leftStrong')
            eps_est = None
            rev_est = None
            if len(leftstrong) >= 1:
                txt = leftstrong[0].text.strip().lstrip('/').strip()
                if txt and txt != '--':
                    eps_est = _parse_inv_number(txt)
            if len(leftstrong) >= 2:
                txt = leftstrong[1].text.strip().lstrip('/').strip()
                if txt and txt != '--':
                    rev_est = _parse_inv_number(txt)

            market_cap = None
            for td in tds:
                cls = td.get('class', [])
                if 'right' in cls and 'time' not in cls:
                    market_cap = _parse_inv_number(td.text.strip())
                    break

            timing = 'TNS'
            time_td = tr.find('td', class_='time')
            if time_td:
                span = time_td.find('span', attrs={'data-tooltip': True})
                if span:
                    tooltip = span['data-tooltip'].lower()
                    if 'before market' in tooltip:
                        timing = 'BMO'
                    elif 'after market' in tooltip:
                        timing = 'AMC'

            rows.append({
                'Symbol': symbol,
                'Company': company_name,
                'Event Start Date': pd.Timestamp(current_date),
                'Marketcap': market_cap,
                'Timing': timing,
                'EPS Estimate': eps_est,
                '_inv_eps_actual': eps_actual,
                '_inv_rev_actual': rev_actual,
                '_inv_rev_est': rev_est,
            })

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def main():
    args = parse_args()

    start, end = get_week_dates(args.start, args.end)
    print(f"Fetching earnings data: {start} to {end}")

    # Fetch calendar
    df = fetch_earnings_calendar(start, end, min_cap=args.min_cap)
    print(f"Fetched {len(df)} companies from Yahoo Finance")

    # Cross-check with investing.com and add any tickers Yahoo Finance missed
    print("Cross-checking with investing.com...")
    inv_df = fetch_earnings_investing_com(start, end)
    if not inv_df.empty:
        existing = set(df['Symbol'].tolist())
        missing = inv_df[~inv_df['Symbol'].isin(existing)].copy()
        if args.min_cap:
            missing = missing[missing['Marketcap'].notna() & (missing['Marketcap'] >= args.min_cap)]
        if not missing.empty:
            added = missing['Symbol'].tolist()
            print(f"Adding {len(added)} tickers found on investing.com but missing from Yahoo Finance: {', '.join(added)}")
            df = pd.concat([df, missing], ignore_index=True)
        else:
            print("No additional tickers found on investing.com")
    else:
        print("WARNING: investing.com returned no data — using Yahoo Finance only")

    # Generate output path
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join(OUTPUT_DIR, f"earnings_week_{start}.md")

    # Generate report
    top_n = 50  # Top 50 per day as "Major" (matches Yahoo Finance display)
    report = generate_report(df, output_path, top_n=top_n, verbose=args.verbose)

    print("\n--- Report Preview (first 25 lines) ---")
    for line in report.split('\n')[:25]:
        print(line)


if __name__ == "__main__":
    main()
