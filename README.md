# Finance RAG — Month 1: Ingestion

A RAG-based research assistant over SEC filings (10-Ks, 10-Qs, earnings calls).
This is the Month 1 scaffold: EDGAR ingestion + project structure.

## Project structure

```
finance-rag/
├── ingestion/      # pulling raw filings from SEC EDGAR
├── parsing/        # extracting structured sections (Risk Factors, MD&A, etc.)
├── chunking/        # splitting parsed sections into retrieval-ready chunks
├── storage/        # vector DB client (pgvector / Qdrant)
├── api/            # FastAPI serving layer
├── eval/           # hand-labeled eval set + retrieval/grounding metrics
└── data/
    ├── raw/        # downloaded HTML filings, by ticker
    └── processed/  # parsed + chunked output
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Step 1: Set your EDGAR User-Agent

SEC EDGAR requires a real identifying User-Agent on every request (your name
+ email), or it will return 403s. Set it as an env var:

```bash
export EDGAR_USER_AGENT="Your Name your_email@example.com"
```

## Step 2: Run the ingestion smoke test

```bash
python3 ingestion/edgar_client.py
```

This pulls Apple's (AAPL) two most recent 10-K filings and saves them to
`data/raw/AAPL/`, along with a metadata JSON for each filing (accession
number, filing date, report date).

Note: this requires real internet access — it will NOT run inside a
sandboxed environment with a restricted network whitelist. Run it on your
own machine or any environment with open outbound HTTP access.

## Step 3: Scale to more companies

`ingestion/edgar_client.py` includes a `SEED_COMPANIES` dict with CIKs for
10 companies across tech and finance sectors. Loop over it:

```python
from ingestion.edgar_client import SEED_COMPANIES, fetch_company_10ks

for ticker, cik in SEED_COMPANIES.items():
    fetch_company_10ks(ticker, cik, num_filings=2)
```

For the full company list (all public companies + their CIKs), see:
https://www.sec.gov/files/company_tickers.json

## What's next (rest of Month 1)

1. **Parsing** (`parsing/`): 10-K HTML is messy and inconsistent across
   companies. Write a parser that extracts `Item 1A. Risk Factors` and
   `Item 7. MD&A` sections specifically, rather than treating the whole
   document as one blob. BeautifulSoup + regex on section headers is a
   reasonable starting approach; expect to iterate as you hit edge cases
   across different companies' filing formats.

2. **Chunking** (`chunking/`): once you have clean section text, chunk by
   subsection/paragraph rather than fixed character counts — financial
   filings have meaningful structural boundaries (numbered risk factors,
   paragraph breaks in MD&A) that fixed-size chunking destroys.

3. **Storage** (`storage/`): stand up Qdrant (Docker: `docker run -p 6333:6333
   qdrant/qdrant`) or pgvector, embed your chunks (start with a solid
   open-weight embedding model), and get one end-to-end query working
   through a minimal FastAPI endpoint.

Validate the full pipeline on ONE company before scaling to all 10 — you
want to catch parsing edge cases early rather than debugging them across
10 companies' worth of inconsistent HTML at once.
