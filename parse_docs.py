#!/usr/bin/env python3
"""
parse_docs.py -- first-cut PDF -> tax-estimator-inputs JSON extractor.

Scans a folder of tax documents (Fidelity 1099 consolidated statements,
Schedule K-1s, 1099-Rs, co-op real-estate-tax/mortgage-interest letters,
estimated-tax payment confirmations, W-2s, ...) and produces:

  - a tax-estimator-inputs JSON file you can Import in the app
  - a human-readable Markdown report of what was found in which file,
    with anything uncertain or unrecognized flagged for manual review

This is a HEURISTIC, best-effort tool, not a filing-accurate one. It is
meant to save you re-typing the easy 90% of the numbers so you can review
and correct the rest in the app. Always check the report before trusting
the JSON.

Usage:
    python3 parse_docs.py <folder> [--year YYYY] [--base FILE.json]
                          [--out FILE.json] [--report FILE.md]

  <folder>     Folder of PDFs to scan (e.g. docs/2025). Required.
  --year       Tax year, used in the output file's household.year and in
               default output filenames. Guessed from the folder name if
               it looks like a year, otherwise defaults to the current year.
  --base       An existing tax-estimator-inputs JSON to start from (e.g.
               last year's file). Household/credits/prior-year fields you
               don't have new documents for are kept from it. If omitted,
               starts from a blank MFJ skeleton -- check household/filing
               status/priors by hand in that case.
  --out        Output JSON path. Defaults to data/tax-inputs-<year>-parsed.json
               next to this script if run from the project, else ./tax-inputs-<year>-parsed.json.
  --report     Output report path. Defaults alongside --out with a .md extension.

Requires on PATH: pdftotext, pdftoppm, tesseract (poppler-utils + tesseract-ocr).
Requires the Python packages: pytesseract, Pillow (for the OCR fallback only).
"""

import argparse
import glob
import shutil
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def pdftotext(path, layout=False):
    args = ["pdftotext"]
    if layout:
        args.append("-layout")
    args += [path, "-"]
    r = run(args)
    return r.stdout or ""


def ocr_pdf(path):
    """Render each page to PNG and OCR it. Used only when normal text
    extraction comes back (near-)empty, i.e. a scanned/image-only PDF."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return "", "OCR skipped: pytesseract/Pillow not installed " \
                    "(pip install --break-system-packages pytesseract Pillow)"
    with tempfile.TemporaryDirectory() as td:
        prefix = os.path.join(td, "pg")
        r = run(["pdftoppm", "-png", "-r", "200", path, prefix])
        if r.returncode != 0:
            return "", f"OCR skipped: pdftoppm failed ({r.stderr.strip()[:200]})"
        pages = sorted(glob.glob(prefix + "*.png"))
        if not pages:
            return "", "OCR skipped: no pages rendered"
        text = []
        for p in pages:
            try:
                text.append(pytesseract.image_to_string(Image.open(p)))
            except Exception as e:
                return "", f"OCR failed: {e}"
        return "\n".join(text), None


def extract(path):
    """Returns dict: raw, layout, ocr_used (bool), ocr_note (str or None)."""
    raw = pdftotext(path, layout=False)
    layout = pdftotext(path, layout=True)
    ocr_used, ocr_note = False, None
    if len(raw.strip()) < 60 and len(layout.strip()) < 60:
        ocr_text, ocr_note = ocr_pdf(path)
        if ocr_text.strip():
            raw = ocr_text
            layout = ocr_text
            ocr_used = True
    return {"raw": raw, "layout": layout, "ocr_used": ocr_used, "ocr_note": ocr_note}


# ---------------------------------------------------------------------------
# Number parsing helpers
# ---------------------------------------------------------------------------

NUM_RE = r"\(?-?\$?\s?[\d,]+\.?\d*\)?"


def parse_num(s):
    """'$1,036.00' / '1,036.' / '(1,036.00)' / '-1,036' -> float, or None."""
    if s is None:
        return None
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace("$", "").replace(",", "").strip()
    s = s.rstrip(".")
    if s in ("", "-"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def first_number_after(text, label_pattern, max_lines=6, stop_patterns=None):
    """Find `label_pattern` in `text`, then scan forward up to `max_lines`
    non-empty lines for the first bare numeric token. Stops early (returns
    None, meaning blank/zero) if a line matching any of `stop_patterns` is
    hit first. Case-insensitive."""
    lines = text.splitlines()
    label_re = re.compile(label_pattern, re.IGNORECASE)
    stop_res = [re.compile(p, re.IGNORECASE) for p in (stop_patterns or [])]
    for i, line in enumerate(lines):
        if not label_re.search(line):
            continue
        # number on the same line, after the label?
        same_line_tail = label_re.split(line, maxsplit=1)
        tail = same_line_tail[-1] if len(same_line_tail) > 1 else ""
        m = re.search(NUM_RE, tail)
        if m and re.search(r"\d", m.group()):
            v = parse_num(m.group())
            if v is not None:
                return v
        seen = 0
        for j in range(i + 1, min(i + 1 + max_lines * 3, len(lines))):
            cand = lines[j].strip()
            if not cand:
                continue
            if any(sr.search(cand) for sr in stop_res):
                return None
            m = re.fullmatch(NUM_RE, cand) or re.match(r"^\$?\s?" + NUM_RE + r"\s*$", cand)
            if m:
                v = parse_num(cand)
                if v is not None:
                    return v
            seen += 1
            if seen >= max_lines:
                break
        return None
    return None


def find_amount(text, *patterns):
    """Try each regex in order; each must have one capturing group with the
    amount. Returns the first successful match's float, or None."""
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            v = parse_num(m.group(1))
            if v is not None:
                return v
    return None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def has(text, phrase):
    """Case-insensitive phrase match tolerant of the phrase's words being
    split across lines/extra whitespace by pdftotext (common in multi-column
    PDFs, e.g. 'Department of\\nTaxation and Finance')."""
    pattern = r"\s+".join(re.escape(w) for w in phrase.split())
    return re.search(pattern, text, re.IGNORECASE) is not None


def classify(t):
    low = t["raw"] + "\n" + t["layout"]
    if has(low, "fidelity") and (has(low, "1099-div") or has(low, "1099-int") or has(low, "1099-b")
                                  or (has(low, "consolidated") and has(low, "1099"))):
        return "fidelity_1099"
    if has(low, "schedule k-1") or (has(low, "k-1") and has(low, "partner")) or has(low, "form 1065"):
        return "k1"
    if has(low, "1099-r") or has(low, "gross distribution"):
        return "1099r"
    if (has(low, "tenant-shareholder") or has(low, "cooperative housing corporation")) \
            and (has(low, "real estate tax") or has(low, "mortgage interest")):
        return "coop_letter"
    if has(low, "reason for payment") and has(low, "estimated tax") and has(low, "1040es"):
        return "estimated_fed"
    if has(low, "department of taxation and finance") and has(low, "estimated tax payment"):
        return "estimated_ny"
    if has(low, "wage and tax statement") or (has(low, "w-2") and has(low, "employer identification")):
        return "w2"
    return "unknown"


# ---------------------------------------------------------------------------
# Per-type extractors. Each returns (contributions, notes) where
# contributions is a list of (field_path, amount, detail_label) and notes
# is a list of strings for the report (findings, warnings, flags).
# ---------------------------------------------------------------------------

def extract_fidelity(t, fname):
    raw = t["raw"]
    c, notes = [], []

    def grab(field, *patterns, section="income_investments"):
        v = find_amount(raw, *patterns)
        if v is not None:
            c.append((f"{section}.{field}", v, f"{fname}"))
            notes.append(f"  - {field}: {v:,.2f}")
        return v

    notes.append(f"Fidelity consolidated 1099 ({fname}):")
    grab("interest", r"\n\s*1\s+Interest Income\s*\.*\s*(" + NUM_RE + r")")
    grab("tax_exempt", r"12\s+Exempt-?Interest Dividends\s*\.*\s*(" + NUM_RE + r")",
         r"12\s+Exempt Interest Dividends\s*\.*\s*(" + NUM_RE + r")")
    grab("ordinary_dividends", r"1a\s+Total Ordinary Dividends\s*\.*\s*(" + NUM_RE + r")")
    grab("qualified_dividends", r"1b\s+Qualified Dividends\s*\.*\s*(" + NUM_RE + r")")
    grab("sec199a_dividends", r"5\s+Section 199A Dividends\s*\.*\s*(" + NUM_RE + r")")
    grab("foreign_tax_paid", r"7\s+Foreign Tax Paid\s*\.*\s*(" + NUM_RE + r")", section="credits")

    # 1099-DIV box 2a (capital gain distributions, e.g. from mutual funds)
    # is long-term gain and doesn't show up in the 1099-B proceeds summary
    # below at all -- easy to miss, but it's real long-term gain.
    cap_gain_distr = find_amount(raw, r"2a\s+Total Capital Gain Distributions\s*\.*\s*(" + NUM_RE + r")")
    if cap_gain_distr:
        c.append(("income_investments.lt_cap_gains", cap_gain_distr, fname))
        notes.append(f"  - capital gain distributions (1099-DIV box 2a, long-term): {cap_gain_distr:,.2f}")

    # 1099-B summary: 5 category rows, each with 6 numeric columns (Proceeds,
    # Cost Basis, Market Discount, Wash Sales, Realized Gain/Loss, Federal
    # Tax Withheld); the 5th is the one we want. -layout mode keeps each
    # category's 6 numbers on the same physical line as its header, which
    # raw-order text does not reliably do -- use layout text here.
    categories = [
        ("short", "Short-term transactions for which basis is reported to the IRS"),
        ("short", "Short-term transactions for which basis is not reported to the IRS"),
        ("long", "Long-term transactions for which basis is reported to the IRS"),
        ("long", "Long-term transactions for which basis is not reported to the IRS"),
        ("long_unknown", "Transactions for which basis is not reported to the IRS and Term is Unknown"),
    ]
    st_total, lt_total, unknown_hit = 0.0, 0.0, False
    for line in t["layout"].splitlines():
        stripped = line.strip()
        for key, header in categories:
            if stripped.startswith(header):
                nums = re.findall(NUM_RE, stripped[len(header):])
                nums = [parse_num(n) for n in nums if re.search(r"\d", n)]
                nums = [n for n in nums if n is not None]
                if len(nums) >= 5:
                    gain_loss = nums[4]
                    if key == "short":
                        st_total += gain_loss
                        notes.append(f"  - short-term proceeds row ({header[:30]}...): gain/loss {gain_loss:,.2f}")
                    else:
                        lt_total += gain_loss
                        notes.append(f"  - long-term proceeds row ({header[:30]}...): gain/loss {gain_loss:,.2f}")
                        if key == "long_unknown" and gain_loss:
                            unknown_hit = True
                break
    if st_total:
        c.append(("income_investments.st_cap_gains", st_total, fname))
    if lt_total:
        c.append(("income_investments.lt_cap_gains", lt_total, fname))
    if unknown_hit:
        notes.append("  NEEDS REVIEW: a 'term is unknown' 1099-B row was folded into long-term gains; confirm the holding period.")
    return c, notes


def extract_k1(t, fname):
    raw = t["raw"]
    c, notes = [], []
    boxes = [
        ("ordinary_income", r"^\s*1\s+Ordinary (business )?income"),
        ("net_rental", r"^\s*2\s+Net rental real estate"),
        ("interest", r"^\s*5\s+Interest income"),
        ("ord_dividends", r"^\s*6a\s+Ordinary dividends"),
        ("qual_dividends", r"^\s*6b\s+Qualified dividends"),
        ("royalties", r"^\s*7\s+Royalties"),
        ("st_cap_gain", r"^\s*8\s+Net short-term capital gain"),
        ("lt_cap_gain", r"^\s*9a\s+Net long-term capital gain"),
    ]
    stop_patterns = [b[1] for b in boxes] + [r"^\s*(9b|9c|10|11|12|13|14|K1|K2|K3|L)\b"]
    entry = {"label": None, "suspended": False}
    for field, pat in boxes:
        v = first_number_after(raw, pat, max_lines=4, stop_patterns=stop_patterns)
        entry[field] = v or 0
        if v:
            notes.append(f"  - box {field}: {v:,.2f}")

    # Box 13 (portfolio deductions) -- try the specific "total to Schedule
    # K-1 line 13" style summary statement first, it's far more reliable
    # than scanning the box grid itself (which often just shows a bare
    # code letter with the amount on an attached statement page instead).
    # Use layout text and require the amount on the SAME physical line as
    # the label (raw-order text often has an unrelated barcode/ID number
    # sitting between the label and its real value on the next line).
    v13 = find_amount(
        t["layout"],
        r"(?m)PORTFOLIO DEDUCTIONS SUBJECT TO (?:THE )?2%\s*FLOOR[ \t]+(" + NUM_RE + r")\s*$",
        r"(?m)TOTAL TO SCHEDULE K-1,?\s*LINE\s*13[A-Z]?[ \t]+(" + NUM_RE + r")\s*$",
    )
    if v13 is None:
        v13 = first_number_after(raw, r"^\s*13\s+Other deductions", max_lines=4,
                                  stop_patterns=stop_patterns)
        if v13 is not None:
            notes.append("  NEEDS REVIEW: box 13 amount found via generic scan, not a labeled statement -- double-check.")
    entry["portfolio_deductions"] = v13 or 0
    if v13:
        notes.append(f"  - box 13 (portfolio deductions): {v13:,.2f}")

    # Entity name: look for an all-caps-ish line with LLC/LP/Inc/Holdings
    # in the first part of the document.
    label = None
    for line in raw.splitlines()[:120]:
        s = line.strip()
        if re.search(r"\b(LLC|L\.L\.C\.|LP|L\.P\.|Inc\.?|Holdings|Partners)\b", s) and len(s) < 80 and len(s) > 3:
            label = s
            break
    if not label:
        label = re.sub(r"^2\d{3}-K1-", "", os.path.splitext(fname)[0]).replace("-", " ")
        notes.append("  NEEDS REVIEW: couldn't find the partnership's name in the text; used the filename instead -- fix the label in the app.")
    entry["label"] = label

    notes_header = [f"Schedule K-1 ({fname}) -- entity guessed as '{label}':"] + notes
    notes_header.append("  NEEDS REVIEW: 'Loss suspended' always defaults to unchecked here -- confirm against your prior-year basis/at-risk tracking.")
    return [("k1s", entry, fname)], notes_header


def standalone_numbers(text, limit_lines=100):
    """Lines that are (after stripping) nothing but a dollar amount, in
    document order, within the first `limit_lines` lines."""
    out = []
    for line in text.splitlines()[:limit_lines]:
        s = line.strip()
        if s and re.fullmatch(NUM_RE, s) and re.search(r"\d", s):
            v = parse_num(s)
            if v is not None:
                out.append(v)
    return out


def extract_1099r(t, fname):
    raw = t["raw"]
    notes = [f"1099-R ({fname}):"]
    # Fidelity-style 1099-Rs often reflow so the box 1 label and its value
    # aren't adjacent in raw-order text, but the gross distribution figure
    # tends to appear twice near the top (once near box 2a, again near
    # "Total distribution"). The most-repeated standalone dollar figure
    # near the top of the document is a decent heuristic for it.
    from collections import Counter
    nums = [v for v in standalone_numbers(raw, 100) if v > 0]
    counts = Counter(nums)
    dup = [v for v, c in counts.items() if c >= 2]
    if dup:
        gross = max(dup)
    elif nums:
        gross = max(nums)
        notes.append("  NEEDS REVIEW: gross distribution guessed as the largest dollar figure near the top of the document (no repeated value found) -- double-check.")
    else:
        gross = None
    fed_wh = first_number_after(raw, r"4\s+Federal income tax", max_lines=4,
                                 stop_patterns=[r"5\s+Employee", r"6\s+Net unrealized"])
    is_ira = bool(re.search(r"IRA/\s*\n?\s*SEP/\s*\n?\s*SIMPLE", raw, re.IGNORECASE))
    c = []
    if gross is not None:
        field = "ira_dist" if is_ira else "pension"
        c.append((f"income_wages_and_retirement.{field}", gross, fname))
        notes.append(f"  - gross distribution: {gross:,.2f} -> {field} ({'IRA/SEP/SIMPLE box checked' if is_ira else 'no IRA box found, assumed pension/annuity'})")
        if not is_ira:
            notes.append("  NEEDS REVIEW: confirm this wasn't actually an IRA distribution -- the IRA/SEP/SIMPLE checkbox wasn't detected reliably.")
    else:
        notes.append("  NEEDS REVIEW: couldn't find a gross distribution amount -- enter by hand.")
    if fed_wh:
        c.append(("payments.fed_withholding", fed_wh, fname))
        notes.append(f"  - federal tax withheld: {fed_wh:,.2f}")
    return c, notes


def extract_coop_letter(t, fname):
    raw = t["raw"]
    notes = [f"Co-op real-estate-tax/mortgage-interest letter ({fname}):"]
    re_tax = find_amount(raw, r"Real estate taxes[^\n\$]*\$?\s*(" + NUM_RE + r")")
    mort_int = find_amount(raw, r"Mortgage interest\s*\$?\s*(" + NUM_RE + r")")
    per_share = has(raw, "per share")
    c = []
    if re_tax is None and mort_int is None:
        notes.append("  NEEDS REVIEW: recognized as a co-op tax letter but couldn't find the dollar figures -- enter by hand.")
        return c, notes
    if per_share:
        # These letters typically give a PER-SHARE deduction; the real
        # dollar amount needs multiplying by the number of shares you own,
        # which isn't in the letter. Report the raw figures but don't
        # write them into the total (a wrong total here would be a much
        # worse miss than an empty field).
        notes.append("  NEEDS REVIEW: this letter states PER-SHARE amounts -- multiply by your share count "
                      "before entering (not done automatically, and not added to the totals below):")
        if re_tax is not None:
            notes.append(f"  - real estate taxes per share: {re_tax:,.4f}")
        if mort_int is not None:
            notes.append(f"  - mortgage interest per share: {mort_int:,.4f}")
        return c, notes
    if re_tax is not None:
        c.append(("deductions_itemized.real_estate_tax", re_tax, fname))
        notes.append(f"  - real estate taxes: {re_tax:,.2f}")
    if mort_int is not None:
        c.append(("deductions_itemized.mortgage_interest", mort_int, fname))
        notes.append(f"  - mortgage interest: {mort_int:,.2f}")
    return c, notes


def extract_estimated_fed(t, fname):
    raw = t["raw"]
    notes = [f"Federal estimated-tax payment confirmation ({fname}):"]
    amt = find_amount(raw, r"Payment Amount\s*\$?\s*(" + NUM_RE + r")")
    c = []
    if amt is not None:
        c.append(("payments.fed_estimated", amt, fname))
        notes.append(f"  - payment amount: {amt:,.2f}")
    else:
        notes.append("  NEEDS REVIEW: couldn't find the payment amount.")
    return c, notes


def extract_estimated_ny(t, fname):
    raw = t["raw"]
    notes = [f"NY estimated-tax payment confirmation ({fname}):"]
    amt = find_amount(raw, r"Payment amount \(\$\):\s*(" + NUM_RE + r")")
    c = []
    if amt is not None:
        c.append(("payments.ny_estimated", amt, fname))
        notes.append(f"  - payment amount (NYS + NYC): {amt:,.2f}")
    else:
        notes.append("  NEEDS REVIEW: couldn't find the payment amount.")
    return c, notes


def extract_w2(t, fname):
    raw = t["raw"]
    notes = [f"W-2 ({fname}):"]
    if not re.search(r"wage and tax statement|employer identification number", raw, re.IGNORECASE):
        notes.append("  NEEDS REVIEW: this looked like a W-2 by name/keyword but doesn't match the standard "
                      "W-2 layout (no 'Wage and Tax Statement' / 'Employer identification number' found). "
                      "Skipped automatic extraction -- open it and enter Box 1/2 by hand.")
        return [], notes
    wages = first_number_after(raw, r"1\s+Wages, tips, other compensation", max_lines=3)
    fed_wh = first_number_after(raw, r"2\s+Federal income tax withheld", max_lines=3)
    c = []
    if wages is not None:
        c.append(("income_wages_and_retirement.wages", wages, fname))
        notes.append(f"  - box 1 wages: {wages:,.2f}")
    if fed_wh is not None:
        c.append(("payments.fed_withholding", fed_wh, fname))
        notes.append(f"  - box 2 federal withholding: {fed_wh:,.2f}")
    if wages is None:
        notes.append("  NEEDS REVIEW: matched the W-2 layout but couldn't read box 1 -- enter by hand.")
    return c, notes


EXTRACTORS = {
    "fidelity_1099": extract_fidelity,
    "k1": extract_k1,
    "1099r": extract_1099r,
    "coop_letter": extract_coop_letter,
    "estimated_fed": extract_estimated_fed,
    "estimated_ny": extract_estimated_ny,
    "w2": extract_w2,
}


# ---------------------------------------------------------------------------
# Skeleton / merge
# ---------------------------------------------------------------------------

def blank_skeleton(year):
    return {
        "format": "tax-estimator-inputs",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "description": f"First-cut {year} data generated by parse_docs.py -- review every number before relying on it.",
        "inputs": {
            "household": {"state": "NY", "year": str(year), "filing_status": "MFJ", "filers_65": 0, "filers_59": 0},
            "income_wages_and_retirement": {"wages": 0, "other_income": 0, "ira_dist": 0, "roth_conv": 0, "pension": 0, "social_security": 0},
            "income_investments": {"interest": 0, "tax_exempt": 0, "ordinary_dividends": 0, "qualified_dividends": 0,
                                    "sec199a_dividends": 0, "treasury_interest": 0, "oos_muni_interest": 0,
                                    "st_cap_gains": 0, "lt_cap_gains": 0},
            "deductions_itemized": {"medical": 0, "salt_income": 0, "real_estate_tax": 0, "mortgage_interest": 0,
                                     "charity_cash": 0, "charity_other": 0, "ny_misc_deductions": 0},
            "credits": {"foreign_tax_paid": 0},
            "k1s": [],
            "payments": {"fed_withholding": 0, "fed_estimated": 0, "ny_withholding": 0, "ny_estimated": 0,
                         "prior_agi": None, "prior_fed_tax": None, "prior_ny_tax": None},
        },
    }


def get_path(d, path):
    node = d
    for part in path.split(".")[:-1]:
        node = node.setdefault(part, {})
    return node, path.split(".")[-1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="Folder of PDFs to scan")
    ap.add_argument("--year")
    ap.add_argument("--base")
    ap.add_argument("--out")
    ap.add_argument("--report")
    args = ap.parse_args()

    folder = args.folder
    if not os.path.isdir(folder):
        sys.exit(f"Not a folder: {folder}")

    if not shutil.which("pdftotext"):
        sys.exit(
            "pdftotext not found on your PATH -- nothing can be read from any PDF without it.\n"
            "Install it (macOS): brew install poppler\n"
            "Then also run: brew install tesseract   (for the OCR fallback on scanned PDFs)\n"
            "              pip3 install pytesseract Pillow"
        )
    missing_ocr = []
    if not shutil.which("pdftoppm"):
        missing_ocr.append("pdftoppm (part of poppler)")
    if not shutil.which("tesseract"):
        missing_ocr.append("tesseract")
    try:
        import pytesseract as _pt  # noqa: F401
        from PIL import Image as _Im  # noqa: F401
    except ImportError:
        missing_ocr.append("the pytesseract/Pillow Python packages (pip3 install pytesseract Pillow)")
    if missing_ocr:
        print("Note: OCR fallback is unavailable (missing: " + ", ".join(missing_ocr) + "). "
              "Scanned/image-only PDFs (e.g. some 1099-Rs, some K-1s) will come back unrecognized "
              "instead of being read -- install the above with Homebrew/pip if you want them handled too.\n")

    year = args.year
    if not year:
        m = re.search(r"(20\d{2})", os.path.basename(os.path.normpath(folder)))
        year = m.group(1) if m else str(datetime.now().year)

    if args.base:
        with open(args.base) as f:
            base_payload = json.load(f)
        result = base_payload
        result.setdefault("inputs", {}).setdefault("k1s", [])
    else:
        result = blank_skeleton(year)

    out_path = args.out or os.path.join(SCRIPT_DIR, "data", f"tax-inputs-{year}-parsed.json")
    report_path = args.report or os.path.splitext(out_path)[0] + ".md"

    pdfs = sorted(glob.glob(os.path.join(folder, "*.pdf")) + glob.glob(os.path.join(folder, "*.PDF")))
    if not pdfs:
        sys.exit(f"No PDFs found in {folder}")

    report_lines = [f"# Parse report -- {folder} ({datetime.now().strftime('%Y-%m-%d %H:%M')})", ""]
    contributions = []  # (field_path, amount, source) for numeric fields
    k1_entries = []      # (entry_dict, source)
    unclassified = []

    for path in pdfs:
        fname = os.path.basename(path)
        t = extract(path)
        kind = classify(t)
        if t["ocr_used"]:
            report_lines.append(f"### {fname}  (OCR used)")
        else:
            report_lines.append(f"### {fname}")
        if kind == "unknown":
            unclassified.append(fname)
            reason = t["ocr_note"] or "no OCR text extracted" if not t["raw"].strip() and not t["ocr_used"] else ""
            report_lines.append(f"  NOT RECOGNIZED -- no extractor matched this document's content. "
                                 f"Enter its figures by hand.{(' (' + reason + ')') if reason else ''}")
            report_lines.append("")
            continue
        if t["ocr_used"] and t["ocr_note"]:
            report_lines.append(f"  ({t['ocr_note']})")
        c, notes = EXTRACTORS[kind](t, fname)
        for item in c:
            if item[0] == "k1s":
                k1_entries.append((item[1], item[2]))
            else:
                contributions.append(item)
        report_lines.extend(notes)
        report_lines.append("")

    # Merge simple numeric fields: sum contributions per field, overwriting
    # the base/skeleton value with the sum (only for fields we actually
    # found something for).
    by_field = {}
    for field_path, amount, source in contributions:
        by_field.setdefault(field_path, []).append((amount, source))

    for field_path, items in by_field.items():
        node, key = get_path(result["inputs"], field_path)
        total = sum(a for a, _ in items)
        node[key] = round(total, 2)

    for entry, source in k1_entries:
        result["inputs"]["k1s"].append(entry)

    # Report: per-field summary of what changed
    if by_field:
        report_lines.append("## Fields set (summed across all matching documents)")
        for field_path in sorted(by_field):
            items = by_field[field_path]
            total = sum(a for a, _ in items)
            srcs = "; ".join(f"{a:,.2f} from {s}" for a, s in items)
            report_lines.append(f"- `{field_path}` = **{total:,.2f}** ({srcs})")
        report_lines.append("")

    if k1_entries:
        report_lines.append("## K-1 rows added")
        for entry, source in k1_entries:
            report_lines.append(f"- {entry.get('label')} (from {source}) -- portfolio_deductions {entry.get('portfolio_deductions', 0):,.2f}, "
                                f"lt_cap_gain {entry.get('lt_cap_gain', 0):,.2f}, ord_dividends {entry.get('ord_dividends', 0):,.2f}")
        report_lines.append("")

    if unclassified:
        report_lines.append("## Not recognized (enter by hand)")
        for f in unclassified:
            report_lines.append(f"- {f}")
        report_lines.append("")

    fields_never_touched = [
        "household.state", "household.filing_status", "household.filers_65", "household.filers_59",
        "income_wages_and_retirement.roth_conv", "income_wages_and_retirement.social_security",
        "income_investments.treasury_interest", "income_investments.oos_muni_interest",
        "deductions_itemized.medical", "deductions_itemized.salt_income",
        "deductions_itemized.charity_cash", "deductions_itemized.charity_other",
        "payments.ny_withholding", "payments.prior_agi", "payments.prior_fed_tax", "payments.prior_ny_tax",
        "K-1 'Loss suspended' checkboxes",
    ]
    report_lines.append("## Always needs manual entry/review")
    report_lines.append("This script never has a source document for these -- carry them over from last "
                         "year's file or fill them in the app:")
    for f in fields_never_touched:
        report_lines.append(f"- {f}")
    report_lines.append("")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))

    print(f"Wrote {out_path}")
    print(f"Wrote {report_path}")
    print(f"{len(pdfs)} PDFs scanned, {len(unclassified)} not recognized, {len(by_field)} fields set, {len(k1_entries)} K-1 row(s) added.")
    print("Review the report before importing the JSON into the app.")


if __name__ == "__main__":
    main()
