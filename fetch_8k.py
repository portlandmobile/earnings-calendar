#!/usr/bin/env python3
"""
SEC EDGAR 8-K Earnings Report Downloader

Navigates SEC EDGAR to find the latest 8-K filing for a company,
then extracts and saves the EX-99.1 (earnings press release) HTML.

Usage:
    python fetch_8k.py "Tesla"
    python fetch_8k.py "Apple"
"""

import re
import sys
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from urllib.parse import urljoin, urlparse

EDGAR_BASE = "https://www.sec.gov"
EDGAR_SEARCH = "https://www.sec.gov/cgi-bin/browse-edgar"

# SEC requires a descriptive User-Agent with contact info
HEADERS = {
    "User-Agent": "8K-Downloader-Script luv2whitewater@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}


def get(url, **kwargs):
    resp = requests.get(url, headers=HEADERS, timeout=30, **kwargs)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Step 1 — Search EDGAR for the company's 8-K filings
# ---------------------------------------------------------------------------
def search_8k_filings(company_name: str) -> tuple[str, BeautifulSoup]:
    """
    Replicates: entering the company name on the EDGAR search page
    and clicking "Retrieve Filings" with type=8-K.
    """
    params = {
        "company": company_name,
        "CIK": "",
        "type": "8-K",
        "dateb": "",
        "owner": "include",
        "count": "10",
        "search_text": "",
        "action": "getcompany",
    }
    print(f"[1] Searching EDGAR for '{company_name}' 8-K filings...")
    resp = get(EDGAR_SEARCH, params=params)
    return resp.url, BeautifulSoup(resp.text, "html.parser")


# ---------------------------------------------------------------------------
# Step 2 — If multiple companies matched, pick the first one
# ---------------------------------------------------------------------------
def resolve_company(base_url: str, soup: BeautifulSoup) -> BeautifulSoup:
    """
    When EDGAR returns a company list (multiple matches), follow the first
    result to get that company's actual 8-K filing list.
    """
    # Look for the company results table (class="companyInfo" or tableFile)
    company_table = soup.find("table", {"class": "tableFile"})
    if company_table:
        first_row = company_table.find("tr", class_=lambda c: c != "tableFileHeader")
        if first_row:
            link = first_row.find("a", href=True)
            if link:
                name = link.text.strip()
                href = link["href"]
                # Build URL for that company's 8-K list
                company_url = urljoin(EDGAR_BASE, href)
                if "action=getcompany" not in company_url:
                    company_url += "&type=8-K&dateb=&owner=include&count=10&action=getcompany"
                print(f"    Multiple matches found — using first: '{name}'")
                resp = get(company_url)
                return BeautifulSoup(resp.text, "html.parser")
    return soup


# ---------------------------------------------------------------------------
# Step 3 — From the filing list, get the [html] link for the latest 8-K
# ---------------------------------------------------------------------------
def get_filing_index_url(soup: BeautifulSoup) -> str:
    """
    Finds the 'html' format link for the most recent 8-K filing.
    This is the filing index page that lists all documents in the filing.
    """
    # The filing results table has class="tableFile2"
    table = soup.find("table", {"class": "tableFile2"})
    if not table:
        raise ValueError("No filing results table found. Company may have no 8-K filings.")

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        # Look for the [html] format link in this row
        for link in row.find_all("a", href=True):
            text = link.text.strip().lower()
            if text in ("html", "[html]", "documents"):
                href = link["href"]
                url = urljoin(EDGAR_BASE, href)
                print(f"[2] Found latest 8-K filing index: {url}")
                return url

    raise ValueError("No HTML-format 8-K filing found for this company.")


# ---------------------------------------------------------------------------
# Step 4 — From the filing index, get the main Document htm link
# ---------------------------------------------------------------------------
def get_document_urls(filing_index_url: str) -> tuple[str | None, str | None]:
    """
    Loads the filing index page and returns:
      - main_doc_url : the main 8-K document (Type=8-K)
      - ex99_url     : EX-99.1 if directly listed in the index
    """
    print(f"[3] Loading filing index page...")
    resp = get(filing_index_url)
    soup = BeautifulSoup(resp.text, "html.parser")

    # The document table on the filing index page
    table = soup.find("table", {"class": "tableFile"})
    if not table:
        raise ValueError("No document table found on the filing index page.")

    main_doc_url = None
    ex99_url = None

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 4:
            continue

        doc_link = cells[2].find("a", href=True) if len(cells) > 2 else None
        doc_type = cells[3].text.strip() if len(cells) > 3 else ""

        if not doc_link:
            continue

        href = doc_link["href"]
        if not href.lower().endswith(".htm"):
            continue

        full_url = urljoin(EDGAR_BASE, href)

        if doc_type == "8-K" and main_doc_url is None:
            main_doc_url = full_url
            print(f"[4] Found main 8-K document: {full_url}")

        if re.search(r"EX-99\.?1", doc_type, re.IGNORECASE) and ex99_url is None:
            ex99_url = full_url
            print(f"    Found EX-99.1 in filing index: {full_url}")

    return main_doc_url, ex99_url


# ---------------------------------------------------------------------------
# Step 5 — Inside the main 8-K doc, follow the link to the 99-1 page
# ---------------------------------------------------------------------------
def find_ex99_in_document(main_doc_url: str) -> str | None:
    """
    Opens the main 8-K document and searches for a hyperlink to the
    EX-99.1 (press release / earnings release).
    """
    print(f"[5] Scanning main 8-K document for EX-99.1 link...")
    resp = get(main_doc_url)
    soup = BeautifulSoup(resp.text, "html.parser")
    base = main_doc_url

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.text.strip().lower()
        href_lower = href.lower()

        is_htm = href_lower.endswith(".htm") or href_lower.endswith(".html")
        mentions_99 = (
            "99" in href_lower
            or "ex99" in href_lower
            or "99.1" in text
            or "exhibit 99" in text
            or "press release" in text
            or "earnings release" in text
        )

        if is_htm and mentions_99:
            full_url = urljoin(base, href)
            print(f"    Found EX-99.1 link inside main doc: {full_url}")
            return full_url

    return None


# ---------------------------------------------------------------------------
# Step 6 — Download and save the EX-99.1 page
# ---------------------------------------------------------------------------
def save_ex99(url: str, company_name: str) -> str:
    print(f"[6] Downloading EX-99.1 from: {url}")
    resp = get(url)

    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", company_name)
    filename = f"{safe_name}_8K_EX99-1.html"
    Path(filename).write_bytes(resp.content)
    print(f"\n    Saved to: {filename}")
    return filename


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python fetch_8k.py \"Company Name\"")
        print("Example: python fetch_8k.py \"Tesla\"")
        sys.exit(1)

    company_name = " ".join(sys.argv[1:])

    # Step 1 — Search
    base_url, soup = search_8k_filings(company_name)

    # Step 2 — Resolve if multiple companies matched
    soup = resolve_company(base_url, soup)

    # Step 3 — Get the HTML filing index URL for the latest 8-K
    filing_index_url = get_filing_index_url(soup)

    # Step 4 — Get URLs from the filing index
    main_doc_url, ex99_url = get_document_urls(filing_index_url)

    # Step 5 — If EX-99.1 wasn't in the index, look inside the main doc
    if ex99_url is None:
        if main_doc_url is None:
            raise ValueError("Could not find main 8-K document in filing index.")
        ex99_url = find_ex99_in_document(main_doc_url)

    if ex99_url is None:
        raise ValueError(
            "Could not locate the EX-99.1 document. "
            "This 8-K filing may not include an earnings press release."
        )

    # Step 6 — Save
    filename = save_ex99(ex99_url, company_name)
    print(f"\nDone. Earnings release saved to: {filename}")


if __name__ == "__main__":
    main()
