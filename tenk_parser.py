"""
10-K Section Parser.

Extracts structured sections from SEC 10-K HTML filings.
The two sections we care about most for RAG:
  - Item 1A: Risk Factors
  - Item 7:  Management's Discussion & Analysis (MD&A)

10-K HTML is notoriously inconsistent across companies and years.
This parser handles the 4 most common formatting patterns found in real filings:
  1. <a name="item1a"> anchor tags  (most common modern filings)
  2. <p><b>ITEM 1A...</b></p>        (bold paragraph headers)
  3. <td><b>ITEM 1A</b></td>         (table-cell layout, older filings)
  4. <p id="item1a">...</p>           (id-attributed paragraphs)

Strategy: find the START of each target section using a regex match on
visible text, then collect all text content until we hit the NEXT known
section header — this is robust against different tag structures.
"""

import re
import json
from pathlib import Path
from bs4 import BeautifulSoup, Tag, NavigableString


# ---------------------------------------------------------------------------
# Section boundary definitions
# Each entry: (item_number, canonical_name, regex to match the header text)
# Order matters: we use this list to detect "next section" boundaries.
# ---------------------------------------------------------------------------
SECTION_BOUNDARIES = [
    ("1",   "Business",                         r"item\s*1[\.\s]*business"),
    ("1A",  "Risk Factors",                     r"item\s*1a[\.\s]*risk\s*factors?"),
    ("1B",  "Unresolved Staff Comments",        r"item\s*1b[\.\s]*unresolved"),
    ("2",   "Properties",                       r"item\s*2[\.\s]*properties"),
    ("3",   "Legal Proceedings",                r"item\s*3[\.\s]*legal\s*proceedings?"),
    ("4",   "Mine Safety",                      r"item\s*4[\.\s]*mine\s*safety"),
    ("5",   "Market for Registrant",            r"item\s*5[\.\s]*market"),
    ("6",   "Selected Financial Data",          r"item\s*6[\.\s]*selected"),
    ("7",   "MD&A",                             r"item\s*7[\.\s]*management"),
    ("7A",  "Quantitative Disclosures",         r"item\s*7a[\.\s]*quantitative"),
    ("8",   "Financial Statements",             r"item\s*8[\.\s]*financial\s*statements?"),
    ("9",   "Changes in Accountants",           r"item\s*9[\.\s]*changes"),
    ("9A",  "Controls and Procedures",          r"item\s*9a[\.\s]*controls"),
    ("9B",  "Other Information",                r"item\s*9b[\.\s]*other"),
    ("10",  "Directors",                        r"item\s*10[\.\s]*directors"),
]

TARGET_SECTIONS = {"1A", "7"}  # sections we extract full text for


def _visible_text(tag) -> str:
    """Get all visible text inside a tag, stripped of whitespace."""
    if isinstance(tag, NavigableString):
        return str(tag).strip()
    return " ".join(tag.get_text(" ", strip=True).split())


def _match_section_header(text: str) -> str | None:
    """
    Check if `text` looks like a 10-K section header.
    Returns the item number string ("1A", "7", etc.) or None.
    """
    normalized = text.lower().strip()
    # Must start with "item" to avoid false positives
    if not normalized.startswith("item"):
        return None
    for item_num, _, pattern in SECTION_BOUNDARIES:
        if re.match(pattern, normalized):
            return item_num
    return None


def _find_section_elements(soup: BeautifulSoup) -> list[tuple[str, Tag]]:
    """
    Walk the document and find (item_number, tag) for every section header.
    Returns them in document order.
    """
    found = []
    seen_items = set()

    for tag in soup.find_all(True):
        # Skip tags that are clearly not headers (too long)
        text = _visible_text(tag)
        if not text or len(text) > 200:
            continue

        item = _match_section_header(text)
        if item and item not in seen_items:
            # Avoid matching the same section twice (table of contents + body)
            # We keep only the LAST occurrence assuming TOC comes before body.
            # We'll de-duplicate after collecting all.
            found.append((item, tag, text))
            seen_items.discard(item)  # allow overwrite with later match
            seen_items.add(item)

    # De-dup: if same item appears multiple times, keep the last one
    # (table of contents links appear before the actual section body)
    deduped = {}
    for item, tag, text in found:
        deduped[item] = (item, tag, text)

    # Sort by document position
    all_tags = list(soup.find_all(True))
    tag_index = {id(t): i for i, t in enumerate(all_tags)}
    return sorted(deduped.values(), key=lambda x: tag_index.get(id(x[1]), 0))


def _all_tags_in_order(soup: BeautifulSoup) -> list[Tag]:
    """Return every tag in the document in tree/document order."""
    return list(soup.find_all(True))


def _extract_text_between(start_tag: Tag, end_tag: Tag | None, soup: BeautifulSoup) -> str:
    """
    Collect all text content between start_tag and end_tag in document order.
    Works by walking ALL tags in order, which correctly handles:
      - <p> paragraph layouts
      - table-cell layouts (<td> content follows <table> header)
      - anchor-preceded headers (<a name=...> + following <p>)
    """
    all_tags = _all_tags_in_order(soup)
    start_id = id(start_tag)
    end_id = id(end_tag) if end_tag else None

    # Find start position
    start_idx = next((i for i, t in enumerate(all_tags) if id(t) == start_id), None)
    if start_idx is None:
        return ""

    # Find end position
    end_idx = None
    if end_id:
        end_idx = next((i for i, t in enumerate(all_tags) if id(t) == end_id), None)

    # Collect leaf-level text-bearing tags between start and end
    LEAF_TAGS = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "span"}
    paragraphs = []
    seen_texts = set()

    for tag in all_tags[start_idx + 1: end_idx]:
        if tag.name not in LEAF_TAGS:
            continue
        # Skip if this tag has block-level children (avoid double-counting)
        has_block_child = any(
            c.name in {"p", "div", "ul", "ol", "table", "tr"}
            for c in tag.children
            if isinstance(c, Tag)
        )
        if has_block_child:
            continue

        text = _visible_text(tag)
        if not text or len(text) < 30:
            continue
        # Skip if we've already captured this text (dedup table cells)
        if text in seen_texts:
            continue
        seen_texts.add(text)
        paragraphs.append(text)

    return "\n\n".join(paragraphs)


def parse_10k(html: str, ticker: str = "", filing_date: str = "") -> dict:
    """
    Main entry point. Given raw 10-K HTML, returns a dict with:
      {
        "ticker": str,
        "filing_date": str,
        "sections": {
          "1A": {"name": "Risk Factors", "text": "..."},
          "7":  {"name": "MD&A",         "text": "..."},
        },
        "parse_warnings": [...]
      }
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove noise: scripts, styles, navigation boilerplate
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    section_elements = _find_section_elements(soup)

    # Build a lookup: item_num -> (tag, next_section_tag)
    sections_found = {}
    for i, (item_num, tag, _) in enumerate(section_elements):
        next_tag = section_elements[i + 1][1] if i + 1 < len(section_elements) else None
        sections_found[item_num] = (tag, next_tag)

    result = {
        "ticker": ticker,
        "filing_date": filing_date,
        "sections": {},
        "parse_warnings": [],
    }

    section_names = {item: name for item, name, _ in SECTION_BOUNDARIES}

    for item_num in TARGET_SECTIONS:
        if item_num not in sections_found:
            result["parse_warnings"].append(f"Section {item_num} not found in filing")
            continue

        start_tag, end_tag = sections_found[item_num]
        text = _extract_text_between(start_tag, end_tag, soup)

        if len(text) < 200:
            result["parse_warnings"].append(
                f"Section {item_num} extracted very little text ({len(text)} chars) — "
                "may be a parsing miss; check the raw HTML manually."
            )

        result["sections"][item_num] = {
            "name": section_names.get(item_num, f"Item {item_num}"),
            "text": text,
            "char_count": len(text),
            "word_count": len(text.split()),
        }

    return result


def parse_10k_file(filepath: str | Path) -> dict:
    """Parse a 10-K HTML file saved to disk (output from edgar_client.py)."""
    path = Path(filepath)
    html = path.read_text(encoding="utf-8", errors="replace")

    # Try to get ticker and date from filename pattern: TICKER_10K_YYYY-MM-DD.html
    stem = path.stem  # e.g. "AAPL_10K_2024-09-28"
    parts = stem.split("_")
    ticker = parts[0] if len(parts) >= 1 else ""
    date = parts[2] if len(parts) >= 3 else ""

    return parse_10k(html, ticker=ticker, filing_date=date)


def parse_all_raw(raw_dir: str = "data/raw", processed_dir: str = "data/processed") -> dict:
    """
    Parse every downloaded 10-K HTML file under `raw_dir/`.
    Saves a JSON result per filing under `processed_dir/`.
    Returns summary stats.
    """
    raw_path = Path(raw_dir)
    proc_path = Path(processed_dir)
    proc_path.mkdir(parents=True, exist_ok=True)

    stats = {"processed": 0, "warnings": 0, "errors": 0}

    for html_file in sorted(raw_path.rglob("*.html")):
        try:
            result = parse_10k_file(html_file)
            out_file = proc_path / f"{html_file.stem}_parsed.json"
            out_file.write_text(json.dumps(result, indent=2))

            w = len(result["parse_warnings"])
            stats["processed"] += 1
            stats["warnings"] += w

            sections_summary = {
                k: f"{v['word_count']:,} words"
                for k, v in result["sections"].items()
            }
            status = "OK" if not w else f"{w} warning(s)"
            print(f"{html_file.name} -> [{status}] {sections_summary}")

        except Exception as e:
            stats["errors"] += 1
            print(f"ERROR parsing {html_file.name}: {e}")

    print(f"\nDone. {stats['processed']} parsed, "
          f"{stats['warnings']} warnings, {stats['errors']} errors.")
    return stats


# ---------------------------------------------------------------------------
# Quick smoke test against a synthetic 10-K excerpt
# ---------------------------------------------------------------------------
SAMPLE_10K_HTML = """
<html><body>
<p><b>ITEM 1A. RISK FACTORS</b></p>
<p>We face significant competition in all of our markets. Our competitors include
large established companies with significant resources, as well as emerging startups.
Failure to compete effectively could materially adversely affect our business.</p>
<p>Our business depends on continued growth of demand for our products and services.
The market for our products is evolving rapidly, and we may not be able to predict
customer preferences accurately or in time to capitalize on changes.</p>
<p>We are subject to extensive government regulation and oversight. Failure to comply
with applicable laws and regulations could result in significant fines and penalties.</p>
<p><b>ITEM 1B. UNRESOLVED STAFF COMMENTS</b></p>
<p>None.</p>
<p><b>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION</b></p>
<p>Net revenue for the fiscal year ended September 2024 was $391 billion,
compared to $383 billion for fiscal year 2023, an increase of 2 percent year over year.
This increase was primarily driven by growth in Services revenue.</p>
<p>Gross margin was 46.2 percent compared to 44.1 percent in the prior year.
The increase in gross margin was primarily due to a higher proportion of Services revenue,
which carries significantly higher margins than our Products segment.</p>
<p><b>ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK</b></p>
<p>We are exposed to market risk from fluctuations in foreign currency exchange rates.</p>
</body></html>
"""

if __name__ == "__main__":
    print("=" * 60)
    print("SMOKE TEST: parsing synthetic 10-K excerpt")
    print("=" * 60)

    result = parse_10k(SAMPLE_10K_HTML, ticker="AAPL", filing_date="2024-09-28")

    for item_num, section in result["sections"].items():
        print(f"\n--- Item {item_num}: {section['name']} ---")
        print(f"Words: {section['word_count']}")
        print(f"Preview: {section['text'][:300]}...")

    if result["parse_warnings"]:
        print(f"\nWarnings: {result['parse_warnings']}")
    else:
        print("\nNo parse warnings.")
