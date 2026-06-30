"""
EDGAR ingestion client.

SEC EDGAR requires a descriptive User-Agent header (name + email) on every
request, or it will start throttling/blocking you. Set EDGAR_USER_AGENT
as an env var before running, e.g.:

    export EDGAR_USER_AGENT="Your Name your_email@example.com"
"""

import os
import time
import json
import requests
from pathlib import Path

EDGAR_BASE = "https://www.sec.gov"
EDGAR_DATA = "https://data.sec.gov"

USER_AGENT = os.environ.get(
    "EDGAR_USER_AGENT", "FinanceRAG Project research@example.com"
)
HEADERS = {"User-Agent": USER_AGENT}

# A handful of well-known CIKs (Central Index Key) to start with.
# Full mapping is available at https://www.sec.gov/files/company_tickers.json
SEED_COMPANIES = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "TSLA": "0001318605",
    "AMZN": "0001018724",
    "NVDA": "0001045810",
    "META": "0001326801",
    "JPM": "0000019617",
    "GS": "0000886982",
    "BAC": "0000070858",
}


def _get(url: str, params: dict | None = None) -> requests.Response:
    """Wrapper around requests.get that respects EDGAR rate limits (~10 req/sec)."""
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(0.15)  # stay comfortably under SEC's rate limit
    return resp


def get_submissions(cik: str) -> dict:
    """Fetch a company's full filing history metadata."""
    cik_padded = cik.zfill(10)
    url = f"{EDGAR_DATA}/submissions/CIK{cik_padded}.json"
    return _get(url).json()


def get_recent_10k_filings(cik: str, count: int = 2) -> list[dict]:
    """Return metadata for the most recent N annual reports (10-K) for a company."""
    data = get_submissions(cik)
    recent = data["filings"]["recent"]
    results = []
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            results.append(
                {
                    "accessionNumber": recent["accessionNumber"][i],
                    "filingDate": recent["filingDate"][i],
                    "primaryDocument": recent["primaryDocument"][i],
                    "reportDate": recent["reportDate"][i],
                }
            )
        if len(results) >= count:
            break
    return results


def download_filing_document(cik: str, accession_number: str, primary_document: str) -> str:
    """Download the raw HTML of a filing's primary document."""
    accession_nodash = accession_number.replace("-", "")
    cik_int = str(int(cik))  # strip leading zeros for the Archives path
    url = f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_document}"
    return _get(url).text


def fetch_company_10ks(ticker: str, cik: str, num_filings: int = 2, out_dir: str = "data/raw") -> list[str]:
    """End-to-end: fetch metadata, download N recent 10-Ks, save raw HTML to disk."""
    out_path = Path(out_dir) / ticker
    out_path.mkdir(parents=True, exist_ok=True)

    filings = get_recent_10k_filings(cik, count=num_filings)
    saved_paths = []

    for f in filings:
        html = download_filing_document(cik, f["accessionNumber"], f["primaryDocument"])
        filename = f"{ticker}_10K_{f['reportDate']}.html"
        filepath = out_path / filename
        filepath.write_text(html, encoding="utf-8")

        # Save filing metadata alongside it
        meta_path = out_path / f"{ticker}_10K_{f['reportDate']}_meta.json"
        meta_path.write_text(json.dumps(f, indent=2))

        saved_paths.append(str(filepath))
        print(f"Saved {ticker} 10-K ({f['reportDate']}) -> {filepath}")

    return saved_paths


if __name__ == "__main__":
    # Start small: pull Apple's last 2 annual reports as a smoke test.
    ticker = "AAPL"
    cik = SEED_COMPANIES[ticker]
    fetch_company_10ks(ticker, cik, num_filings=2)
