#!/usr/bin/env python3
"""
Generates a set of tax-estimator-produced 'form' PDFs, reproducing the
line-by-line structure of the real federal/NY forms and populated with the
numbers computed by server.py's perform_calculations() from a saved inputs
file (one of the JSON files under data/, e.g. from the app's Export/Save).

Usage:
    python3 generate_forms.py [path/to/inputs.json]

    If no path is given, defaults to
    data/tax-inputs-2025-MFJ-actual.json (relative to this script).
    Output goes to docs/<year>/generated-forms/ (relative to this script),
    where <year> comes from the inputs file's "year" field.

Requires: pip install --break-system-packages reportlab

IMPORTANT: these are NOT the official IRS/NY PDF forms — this generator has
no access to the real fillable PDFs from irs.gov / tax.ny.gov. They are
estimator-generated worksheets that mirror each form's line numbers and
labels, meant to be reviewed and transcribed into the real forms or tax
software. Anything the app doesn't model is called out explicitly in each
form's "Notes / assumptions" section.
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import server  # noqa: E402

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
)

DATA_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    SCRIPT_DIR, "data", "tax-inputs-2025-MFJ-actual.json")

with open(DATA_FILE) as f:
    payload = json.load(f)
inputs = payload["inputs"]
flat = {}
for section, vals in inputs.items():
    if section == "k1s":
        flat["k1s"] = vals          # a list, not a {field: value} section
    else:
        flat.update(vals)
YEAR = str(flat.get("year", "2025"))

OUT_DIR = os.path.join(SCRIPT_DIR, "docs", YEAR, "generated-forms")
os.makedirs(OUT_DIR, exist_ok=True)
print(f"Reading:  {DATA_FILE}")
print(f"Writing:  {OUT_DIR}/")

styles = getSampleStyleSheet()
title_style = ParagraphStyle("FormTitle", parent=styles["Heading1"], fontSize=15, spaceAfter=2)
sub_style = ParagraphStyle("FormSub", parent=styles["Normal"], fontSize=9.5, textColor=colors.HexColor("#444444"), spaceAfter=10)
note_style = ParagraphStyle("Note", parent=styles["Normal"], fontSize=8.5, textColor=colors.HexColor("#555555"), leading=11)
section_style = ParagraphStyle("Section", parent=styles["Heading3"], fontSize=11, spaceBefore=10, spaceAfter=4)


def money(v, blank_if_zero=False):
    if v is None:
        return ""
    if blank_if_zero and abs(v) < 0.005:
        return ""
    neg = v < -0.005
    v = abs(v)
    s = f"{v:,.0f}"
    return f"({s})" if neg else s


def render_form(fname, form_no, form_title, subtitle, sections, notes):
    """sections: list of (section_heading_or_None, rows) where rows is a
    list of (line, description, amount, bold_flag)."""
    path = os.path.join(OUT_DIR, fname)
    doc = SimpleDocTemplate(path, pagesize=letter,
                             topMargin=0.55 * inch, bottomMargin=0.55 * inch,
                             leftMargin=0.65 * inch, rightMargin=0.65 * inch)
    story = []
    story.append(Paragraph(f"{form_no} &nbsp;&nbsp; {form_title}", title_style))
    story.append(Paragraph(subtitle, sub_style))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#999999"), thickness=0.75))
    story.append(Spacer(1, 6))

    for heading, rows in sections:
        if heading:
            story.append(Paragraph(heading, section_style))
        data = [["Line", "Description", "Amount"]]
        for line, desc, amt, bold in rows:
            data.append([line, desc, amt])
        t = Table(data, colWidths=[0.55 * inch, 4.55 * inch, 1.3 * inch])
        style_cmds = [
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        for i, (line, desc, amt, bold) in enumerate(rows, start=1):
            if bold:
                style_cmds.append(("FONTNAME", (0, i), (-1, i), "Helvetica-Bold"))
                style_cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f6f6f6")))
        t.setStyle(TableStyle(style_cmds))
        story.append(t)
        story.append(Spacer(1, 8))

    if notes:
        story.append(Spacer(1, 6))
        story.append(Paragraph("Notes / assumptions", ParagraphStyle("NoteHead", parent=styles["Heading4"], fontSize=9.5)))
        for n in notes:
            story.append(Paragraph(f"&bull; {n}", note_style))

    doc.build(story)
    print("wrote", path)


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------
r = server.perform_calculations(flat)

_status_names = {"MFJ": "Married filing jointly", "MFS": "Married filing separately",
                  "SINGLE": "Single", "HOH": "Head of household"}
_data_rel = os.path.relpath(DATA_FILE, SCRIPT_DIR)
SUBTITLE = (f"Tax year {YEAR} &middot; {_status_names.get(flat.get('filing_status'), flat.get('filing_status'))} "
            f"&middot; New York (full-year NYC resident) &middot; Generated by the Tax &amp; IRMAA Estimator from {_data_rel}")
GEN_NOTE = "Reproduces the official form's line numbers/labels but is NOT the IRS/NY PDF itself (this generator has no access to the real irs.gov/tax.ny.gov fillable PDFs); transcribe into the real form or e-file software. Name(s), SSN(s) and signatures are intentionally left blank."

print(f"AGI {r['agi']:,.0f}   fed_total_tax {r['fed_total_tax']:,.0f}   ny_total_tax {r['ny_total_tax']:,.0f}")

wages = flat.get("wages", 0)
other_income = flat.get("other_income", 0)
ira_dist = flat.get("ira_dist", 0)
pension = flat.get("pension", 0)
interest = flat.get("interest", 0)
tax_exempt = flat.get("tax_exempt", 0)
ord_div = flat.get("ordinary_dividends", 0)
q_div = flat.get("qualified_dividends", 0)
sec199a = flat.get("sec199a_dividends", 0)
treasury_int = flat.get("treasury_interest", 0)
oos_muni = flat.get("oos_muni_interest", 0)
st_cg = flat.get("st_cap_gains", 0)
lt_cg = flat.get("lt_cap_gains", 0)
ny_misc = flat.get("ny_misc_deductions", 0)

# Fold in K-1s exactly as server.perform_calculations() does internally, so the
# form lines match the combined totals the tax was actually computed from —
# not just the plain brokerage/W-2 inputs.
k1_totals, k1_entries = server.aggregate_k1s(flat.get("k1s"))
other_income += k1_totals["other_income"]
interest += k1_totals["interest"]
ord_div += k1_totals["ord_dividends"]
q_div += k1_totals["qual_dividends"]
st_cg += k1_totals["st_cap_gain"]
lt_cg += k1_totals["lt_cap_gain"]
ny_misc += k1_totals["portfolio_deductions"]

net_cg = st_cg + lt_cg
cg_loss_used = max(net_cg, -3000.0) if net_cg < 0 else net_cg
cg_carryover = (net_cg - cg_loss_used) if net_cg < 0 else 0.0
fed_ftc = r["foreign_tax_credit"]
foreign_tax_paid = flat.get("foreign_tax_paid", 0)

fed_wh = flat.get("fed_withholding", 0)
fed_est = flat.get("fed_estimated", 0)
ny_wh = flat.get("ny_withholding", 0)
ny_est = flat.get("ny_estimated", 0)
fed_paid = fed_wh + fed_est
ny_paid = ny_wh + ny_est
fed_bal = r["payments"]["federal"]["balance"]
ny_bal = r["payments"]["ny"]["balance"]

total_income = wages + interest + ord_div + ira_dist + pension + 0 + cg_loss_used + other_income
agi = r["agi"]
fed_item = r["fed_itemized"]
fed_ded = r["fed_deduction"]
qbi = r["qbi_deduction"]
fed_taxable = r["fed_taxable"]
fed_tax_line16 = r["fed_tax"]
fed_total_tax = r["fed_total_tax"]

# ===========================================================================
# FORM 1040
# ===========================================================================
render_form(
    "Form-1040.pdf", "Form 1040", "U.S. Individual Income Tax Return",
    SUBTITLE,
    [
        (None, [
            ("1a", "Total amount from Form(s) W-2, box 1 (wages)", money(wages), False),
            ("1z", "Add lines 1a through 1h", money(wages), False),
            ("2a / 2b", "Tax-exempt interest / Taxable interest", f"{money(tax_exempt)} / {money(interest)}", False),
            ("3a / 3b", "Qualified dividends / Ordinary dividends", f"{money(q_div)} / {money(ord_div)}", False),
            ("4a / 4b", "IRA distributions / Taxable amount", f"{money(ira_dist)} / {money(ira_dist)}", False),
            ("5a / 5b", "Pensions and annuities / Taxable amount", f"{money(pension)} / {money(pension)}", False),
            ("6a / 6b", "Social security benefits / Taxable amount", "0 / 0", False),
            ("7", "Capital gain or (loss) — Schedule D required (attached)", money(cg_loss_used), False),
            ("8", "Additional income from Schedule 1, line 10", money(other_income), False),
            ("9", "Total income (add lines 1z, 2b, 3b, 4b, 5b, 6b, 7, 8)", money(total_income), True),
            ("10", "Adjustments to income (Schedule 1, line 25)", "0", False),
            ("11", "Adjusted gross income", money(agi), True),
            ("12", "Standard deduction or itemized deductions (Schedule A) — itemized", money(fed_ded), False),
            ("13", "Qualified business income deduction (Form 8995)", money(qbi), False),
            ("14", "Add lines 12 and 13", money(fed_ded + qbi), False),
            ("15", "Taxable income (line 11 minus line 14, not less than 0)", money(fed_taxable), True),
        ]),
        ("Tax and Credits", [
            ("16", "Tax (Qualified Dividends and Capital Gain Tax Worksheet)", money(fed_tax_line16), False),
            ("17", "Schedule 2, line 3", "0", False),
            ("18", "Add lines 16 and 17", money(fed_tax_line16), False),
            ("19", "Child tax credit / credit for other dependents", "0", False),
            ("20", "Schedule 3, line 8 (foreign tax credit)", money(fed_ftc), False),
            ("21", "Add lines 19 and 20", money(fed_ftc), False),
            ("22", "Subtract line 21 from line 18", money(fed_tax_line16 - fed_ftc), False),
            ("23", "Other taxes, Schedule 2, line 21", "0", False),
            ("24", "Total tax", money(fed_total_tax), True),
        ]),
        ("Payments", [
            ("25a", "Federal income tax withheld from Form(s) W-2", money(322.10), False),
            ("25b", "Federal income tax withheld from Form(s) 1099", money(6000.00), False),
            ("25d", "Total withholding (add 25a-25c)", money(fed_wh), False),
            ("26", "2025 estimated tax payments", money(fed_est), False),
            ("33", "Total payments (add lines 25d, 26, and other payments/credits)", money(fed_paid), True),
            ("34", "Overpayment (if line 33 is more than line 24)", money(-fed_bal if fed_bal < 0 else 0), False),
            ("35a", "Amount to be refunded to you", money(-fed_bal if fed_bal < 0 else 0), True),
            ("37", "Amount you owe (if line 24 is more than line 33)", money(fed_bal if fed_bal > 0 else 0), True),
        ]),
    ],
    [
        GEN_NOTE,
        "Line 7: Schedule D nets a $202,177 short-term loss against a $119,969 long-term gain to a $82,207 loss, capped at the $3,000 annual limit; $79,207 carries over to 2026 — see Schedule D.",
        "Line 19: no dependents were entered into the estimator, so the child tax credit is shown as $0 — add it by hand if you have qualifying dependents.",
        "Line 25a/25b split federal withholding ($6,322 total: $322.10 from the W-2, $6,000 from the IRA 1099-R) per the source notes; confirm against your actual W-2/1099-R box 2/4.",
        "No AMT, NIIT, or Schedule 2/other-tax items apply at this income level per the estimator's model (NIIT threshold not reached; AMT not modeled).",
    ],
)
print("1040 done")

# ===========================================================================
# SCHEDULE 1
# ===========================================================================
render_form(
    "Schedule-1.pdf", "Schedule 1 (Form 1040)", "Additional Income and Adjustments to Income",
    SUBTITLE,
    [
        ("Part I — Additional Income", [
            ("8z", "Other income — net rental/K-1 income (Schedule E)", money(other_income), False),
            ("9", "Total other income (add lines 8a through 8z)", money(other_income), False),
            ("10", "Total additional income (add lines 1 through 7 and 9); enter on Form 1040, line 8", money(other_income), True),
        ]),
        ("Part II — Adjustments to Income", [
            ("25", "Total adjustments to income; enter on Form 1040, line 10", "0", True),
        ]),
    ],
    [
        GEN_NOTE,
        "Line 8z: $40 = Greek rental income $18,589 less $18,549 of expenses (incl. $6,125 depreciation); the two K-1s (J-Ventures, Espiritu Shores) net to $0 after suspended passive losses. Attach Schedule E.",
        "No adjustments (HSA, SE tax, IRA deduction, student loan interest, etc.) are modeled by the estimator.",
    ],
)

# ===========================================================================
# SCHEDULE 3
# ===========================================================================
render_form(
    "Schedule-3.pdf", "Schedule 3 (Form 1040)", "Additional Credits and Payments",
    SUBTITLE,
    [
        ("Part I — Nonrefundable Credits", [
            ("1", "Foreign tax credit (attach Form 1116 if required)", money(fed_ftc), False),
            ("8", "Add lines 1 through 7; enter on Form 1040, line 20", money(fed_ftc), True),
        ]),
    ],
    [
        GEN_NOTE,
        f"Foreign tax paid was $1,486 (1099-DIV box 7), above the $600 MFJ no-Form-1116 limit, so Form 1116 is required and is included in this set.",
        "The estimator credits min(foreign tax paid, tax before credits) and does not model the Form 1116 limitation formula or the $7,547 of prior-year carryover used on the actual filed return — see Form 1116 and the cover summary.",
    ],
)

# ===========================================================================
# SCHEDULE A
# ===========================================================================
salt_paid = fed_item["salt_paid"]
salt_capped = fed_item["salt"]
mort_int = fed_item["mortgage_interest"]
charity_cash = flat.get("charity_cash", 0)
charity_other = flat.get("charity_other", 0)
charity_total = fed_item["charity_total"]
render_form(
    "Schedule-A.pdf", "Schedule A (Form 1040)", "Itemized Deductions",
    SUBTITLE,
    [
        ("Medical and Dental Expenses", [
            ("1", "Medical and dental expenses", "0", False),
            ("2", "Adjusted gross income (Form 1040, line 11)", money(agi), False),
            ("3", "Multiply line 2 by 7.5%", money(0.075 * agi), False),
            ("4", "Subtract line 3 from line 1 (not less than 0)", "0", False),
        ]),
        ("Taxes You Paid", [
            ("5a", "State and local income taxes", money(flat.get("salt_income", 0)), False),
            ("5b", "State and local real estate taxes", money(flat.get("real_estate_tax", 0)), False),
            ("5c", "State and local personal property taxes", "0", False),
            ("5d", "Add lines 5a through 5c", money(salt_paid), False),
            ("5e", "Smaller of line 5d or $40,000 ($20,000 if MFS)", money(salt_capped), False),
            ("6", "Other taxes", "0", False),
            ("7", "Add lines 5e and 6", money(salt_capped), False),
        ]),
        ("Interest You Paid", [
            ("8a", "Home mortgage interest and points (Form 1098)", money(mort_int), False),
            ("8e", "Add lines 8a through 8d", money(mort_int), False),
            ("9", "Investment interest (Form 4952)", "0", False),
            ("10", "Add lines 8e and 9", money(mort_int), False),
        ]),
        ("Gifts to Charity", [
            ("11", "Gifts by cash or check", money(charity_cash), False),
            ("12", "Other than by cash or check (Form 8283 if over $500)", money(charity_other), False),
            ("13", "Carryover from prior year", "0", False),
            ("14", "Add lines 11 through 13", money(charity_total), False),
        ]),
        ("Other & Total", [
            ("16", "Other itemized deductions", "0", False),
            ("17", "Total itemized deductions (add lines 4, 7, 10, 14, 16)", money(fed_ded), True),
        ]),
    ],
    [
        GEN_NOTE,
        f"Line 5d ({money(salt_paid)}) is capped at $40,000 by the OBBBA SALT cap for 2025; the estimator's income-based phase-down of the cap did not reduce it further at this AGI.",
        f"Return shows $7,302 of cash charity vs. $6,786 here (a $516 gap not in the source PDFs) — see the cover summary and data/tax-inputs-2025-MFJ-actual-sources.md.",
        "No 2/37 additional limitation on itemized deductions applies at this taxable-income level per the estimator.",
    ],
)

# ===========================================================================
# SCHEDULE B
# ===========================================================================
render_form(
    "Schedule-B.pdf", "Schedule B (Form 1040)", "Interest and Ordinary Dividends",
    SUBTITLE,
    [
        ("Part I — Interest", [
            ("1", "Fidelity acct 5676 ($21.79) + acct 7033 ($9.39) — see 1099-INT box 1", money(interest), False),
            ("2", "Add the amounts on line 1", money(interest), False),
            ("3", "Excludable U.S. savings bond interest (Form 8815)", "0", False),
            ("4", "Subtract line 3 from line 2; enter on Form 1040, line 2b", money(interest), True),
        ]),
        ("Part II — Ordinary Dividends", [
            ("5", "Fidelity accts 5676 / 7033 / 8402 + K-1 Espiritu Shores 6a — see 1099-DIV box 1a", money(ord_div), False),
            ("6", "Add the amounts on line 5; enter on Form 1040, line 3b", money(ord_div), True),
        ]),
        ("Part III — Foreign Accounts and Trusts", [
            ("7a", "Financial interest in or signature authority over a foreign account?", "See taxpayer records", False),
            ("8", "Foreign trust — received a distribution from, or were a grantor of, a foreign trust?", "See taxpayer records", False),
        ]),
    ],
    [
        GEN_NOTE,
        "Payer-level detail from data/tax-inputs-2025-MFJ-actual-sources.md; attach the underlying 1099-INT/1099-DIV/K-1 statements as usual — this worksheet only totals what the app has as inputs.",
        "Part III answers are not captured by the estimator; confirm directly from your own records.",
    ],
)

# ===========================================================================
# SCHEDULE D + capital-loss carryover
# ===========================================================================
render_form(
    "Schedule-D.pdf", "Schedule D (Form 1040)", "Capital Gains and Losses",
    SUBTITLE,
    [
        ("Part I — Short-Term Capital Gains and Losses", [
            ("1a", "Totals for transactions reported on 1099-B, basis reported to IRS (proceeds/basis not itemized here — see 1099-B statements)", money(st_cg), False),
            ("7", "Net short-term capital gain or (loss)", money(st_cg), True),
        ]),
        ("Part II — Long-Term Capital Gains and Losses", [
            ("8a", "Totals for transactions reported on 1099-B, basis reported to IRS, plus capital-gain distributions and K-1 gain (see sources)", money(lt_cg), False),
            ("15", "Net long-term capital gain or (loss)", money(lt_cg), True),
        ]),
        ("Part III — Summary", [
            ("16", "Combine lines 7 and 15", money(net_cg), False),
            ("17", "Are lines 15 and 16 both gains?", "No" if net_cg < 0 else "Yes", False),
            ("18", "28%-rate gain worksheet amount", "0", False),
            ("19", "Unrecaptured section 1250 gain", "0", False),
            ("20", "Complete the Qualified Dividends and Capital Gain Tax Worksheet", "Yes", False),
            ("21", "If line 16 is a loss, smaller of that loss or $3,000 ($1,500 if MFS); enter on Form 1040, line 7", money(cg_loss_used) if net_cg < 0 else "n/a", True),
            ("22", "Do you have qualified dividends on Form 1040, line 3a?", "Yes", False),
        ]),
        ("Capital Loss Carryover to 2026 (worksheet)", [
            ("—", "Loss remaining after the $3,000 used in 2025", money(-cg_carryover if net_cg < 0 else 0), True),
        ]),
    ],
    [
        GEN_NOTE,
        "The estimator only has aggregate short-term and long-term totals (not per-lot proceeds/basis), so lines 1a/8a show net figures only — attach your broker's 1099-B detail (Form 8949 as needed) with the return.",
        f"A ${abs(cg_carryover):,.0f} short-term capital loss carries forward to tax year 2026 — remember to enter it on next year's Schedule D / carryover worksheet.",
    ],
)

# ===========================================================================
# FORM 8995
# ===========================================================================
taxable_before_qbi = fed_taxable + qbi
render_form(
    "Form-8995.pdf", "Form 8995", "Qualified Business Income Deduction (Simplified Computation)",
    SUBTITLE,
    [
        (None, [
            ("6", "Qualified REIT dividends and PTP income (1099-DIV box 5)", money(sec199a), False),
            ("7", "Qualified REIT dividend/PTP loss carryforward from prior year", "0", False),
            ("8", "Total qualified REIT dividends/PTP income (add 6 and 7, not less than 0)", money(sec199a), False),
            ("9", "REIT/PTP component: 20% of line 8", money(0.20 * sec199a), False),
            ("10", "QBI deduction before income limitation (line 5 + line 9)", money(qbi), False),
            ("11", "Taxable income before QBI deduction", money(taxable_before_qbi), False),
            ("12", "Net capital gain (qualified dividends + net LTCG)", money(q_div), False),
            ("13", "Subtract line 12 from line 11", money(taxable_before_qbi - q_div), False),
            ("14", "Income limitation: 20% of line 13", money(0.20 * max(0.0, taxable_before_qbi - q_div)), False),
            ("15", "QBI deduction — smaller of line 10 or line 14", money(qbi), True),
        ]),
    ],
    [
        GEN_NOTE,
        "No qualified-trade-or-business QBI is modeled (line 5 = $0) — only the Section 199A REIT-dividend component from the 1099-DIVs.",
        "Below the $394,600 (MFJ, 2025) taxable-income threshold for the simplified form, so no wage/UBIA limitation applies.",
    ],
)

# ===========================================================================
# FORM 1116 (best effort — flagged)
# ===========================================================================
render_form(
    "Form-1116.pdf", "Form 1116", "Foreign Tax Credit (Passive Category Income)",
    SUBTITLE,
    [
        ("Part I — Taxable Income From Sources Outside the U.S.", [
            ("1a", "Gross income from foreign sources", "NOT CAPTURED — see notes", False),
            ("3a/3b", "Definitely/ratably related deductions", "NOT CAPTURED — see notes", False),
        ]),
        ("Part II — Foreign Taxes Paid or Accrued", [
            ("A", "Country: RIC (mutual fund) pass-through — 1099-DIV box 7", money(foreign_tax_paid), False),
            ("8", "Total foreign taxes paid or accrued", money(foreign_tax_paid), True),
        ]),
        ("Part III — Figuring the Credit", [
            ("9", "Foreign taxes paid or accrued (from line 8)", money(foreign_tax_paid), False),
            ("14", "Total foreign taxes available for credit", money(foreign_tax_paid), False),
            ("18", "Taxable income from all sources (Form 1040, line 15)", money(fed_taxable), False),
            ("20", "Tax (Form 1040, line 16, plus Schedule 2 line 1a, minus certain items)", money(fed_tax_line16), False),
            ("21", "Limitation (line 19 x line 20) — requires line 1a/3a/3b above", "NOT CAPTURED — see notes", False),
            ("22", "Foreign tax credit (smaller of line 14 or line 21), before carryover", money(fed_ftc), False),
            ("33", "Foreign tax credit — enter on Schedule 3, line 1", money(fed_ftc), True),
        ]),
    ],
    [
        GEN_NOTE,
        "The estimator only takes 'foreign tax paid' as an input; it does not capture gross foreign-source income, ratably-apportioned deductions, or prior-year FTC carryover, so it cannot compute the real Form 1116 limitation (line 21) — it simply floors the credit at min(foreign tax paid, U.S. tax before credits).",
        "The actual 2025 return claimed a $2,814 credit (this year's $1,486 plus $1,328 drawn from a $7,547 prior-year carryover pool, itself limited by Form 1116) — see data/tax-inputs-2025-MFJ-actual-sources.md. Get the real foreign-source-income figures from your 1099-DIV supplemental tax info and your prior Form 1116 carryover worksheet before filing this form for real.",
    ],
)

print("all federal forms done")

# ===========================================================================
# NY IT-201
# ===========================================================================
ny_item = r["ny_itemized"]
state_agi = r["state_agi"]
state_ded = r["state_deduction"]
nys_taxable = r["nys_taxable"]
nys_tax = r["nys_tax"]
nyc_tax = r["nyc_tax"]
nyc_school = r["nyc_school_credit"]
ny_total_tax = r["ny_total_tax"]

render_form(
    "NY-IT201.pdf", "Form IT-201", "New York State Resident Income Tax Return",
    SUBTITLE,
    [
        ("Federal Income and Adjustments (mirrors Form 1040)", [
            ("1", "Wages, salaries, tips, etc.", money(wages), False),
            ("2", "Taxable interest income", money(interest), False),
            ("3", "Ordinary dividends", money(ord_div), False),
            ("4", "Taxable refunds of state/local income taxes", "0", False),
            ("6", "Capital gain or (loss)", money(cg_loss_used), False),
            ("9", "Taxable amount of IRA distributions", money(ira_dist), False),
            ("10", "Taxable amount of pensions and annuities", money(pension), False),
            ("15", "Other income (federal Schedule 1)", money(other_income), False),
            ("16", "Add lines 1 through 15", money(total_income), False),
            ("17", "Total federal adjustments to income", "0", False),
            ("18", "Federal adjusted gross income", money(agi), True),
        ]),
        ("New York Additions", [
            ("20", "Interest income on state/local bonds other than NY (out-of-state munis)", money(oos_muni), False),
            ("23", "Total New York additions", money(oos_muni), False),
        ]),
        ("New York Subtractions", [
            ("28", "Taxable amount of U.S. government interest/dividends", money(treasury_int), False),
            ("29", "Pension and annuity income exclusion (up to $20,000/qualifying person 59½+)", money(r["ny_retirement_exclusion"]), False),
            ("31", "Total New York subtractions", money(treasury_int + r["ny_retirement_exclusion"]), False),
            ("32", "New York adjusted gross income (line 19 + 23 − 31)", money(state_agi), True),
        ]),
        ("Deductions, Taxable Income & Tax", [
            ("34", "New York itemized deduction (Form IT-196, or standard $16,050)", money(state_ded), False),
            ("35", "Taxable income (line 32 minus line 34)", money(nys_taxable), True),
            ("39", "New York State tax on line 35 amount (rate schedule, incl. supplemental/benefit-recapture tax)", money(nys_tax), False),
            ("40", "New York City resident tax (full-year NYC resident)", money(nyc_tax), False),
        ]),
        ("Credits and Total Tax", [
            ("69", "NYC school tax credit (refundable — fixed $125 + rate-reduction component)", money(nyc_school), False),
            ("—", "Net New York State + New York City tax", money(ny_total_tax), True),
        ]),
        ("Payments", [
            ("72", "New York State/NYC tax withheld", money(ny_wh), False),
            ("74", "2025 estimated tax payments", money(ny_est), False),
            ("77", "Total payments", money(ny_paid), True),
            ("78", "Overpayment (refund)", money(-ny_bal if ny_bal < 0 else 0), False),
            ("81", "Amount you owe", money(ny_bal if ny_bal > 0 else 0), True),
        ]),
    ],
    [
        GEN_NOTE,
        "Lines 1-16 mirror the federal income items already used to build Form 1040; verify these line numbers against the actual 2025 IT-201 (NY renumbers occasionally).",
        f"Line 39 already includes the ${r['state_supplemental_tax']:,.0f} 'tax benefit recapture' supplemental tax that applies once NY AGI passes $107,650 — see Schedule (worksheet) on the estimator for the breakdown.",
        "K-1 pass-through items (J-Ventures, Espiritu Shores) net to $0 after suspended passive losses and are folded into line 15/'other income' rather than shown on separate IT-201 lines for rental/partnership income.",
        "Does not include the $51 NY resident credit for tax paid to California on the settlement wages (not modeled) — see the cover summary.",
    ],
)

# ===========================================================================
# NY IT-196
# ===========================================================================
render_form(
    "NY-IT196.pdf", "Form IT-196", "New York Resident Itemized Deductions",
    SUBTITLE,
    [
        ("Medical and Dental (10% AGI floor, pre-2018 federal rules)", [
            ("1", "Medical and dental expenses", "0", False),
            ("2", "Federal AGI (from IT-201, line 18)", money(agi), False),
            ("3", "Multiply line 2 by 10%", money(0.10 * agi), False),
            ("4", "Subtract line 3 from line 1 (not less than 0)", "0", False),
        ]),
        ("Taxes You Paid (no SALT cap under NY's pre-2018 rules; state/local income tax not allowed)", [
            ("5", "State and local general sales/income taxes — NOT allowed for NY purposes", "0", False),
            ("6", "Real estate taxes", money(flat.get("real_estate_tax", 0)), False),
            ("7", "Personal property taxes", "0", False),
            ("8", "Add lines 5 through 7", money(flat.get("real_estate_tax", 0)), False),
        ]),
        ("Interest You Paid", [
            ("10", "Home mortgage interest and points", money(mort_int), False),
            ("15", "Add lines 10 through 14", money(mort_int), False),
        ]),
        ("Gifts to Charity", [
            ("16", "Gifts by cash or check", money(charity_cash), False),
            ("17", "Other than by cash or check", money(charity_other), False),
            ("19", "Add lines 16 through 18", money(charity_total), False),
        ]),
        ("Miscellaneous Deductions (NY kept the pre-2018 2%-of-AGI category)", [
            ("24", "Investment advisory/account fees, tax-prep fees, and K-1 portfolio deductions", money(flat.get("ny_misc_deductions", 0)), False),
            ("25", "Multiply federal AGI (line 2) by 2%", money(ny_item["misc_floor"]), False),
            ("26", "Subtract line 25 from line 24 (not less than 0)", money(ny_item["misc"]), False),
        ]),
        ("Total Before High-Income Adjustment", [
            ("29", "Add lines 4, 8, 15, 19, and 26", money(ny_item["total"] - ny_item["adjustment"]), False),
            ("40", "Itemized deduction adjustment (high-NY-AGI phase-down)", money(-ny_item["adjustment"]) if ny_item["adjustment"] else "0", False),
            ("45", "New York itemized deductions — enter on IT-201, line 34", money(ny_item["total"]), True),
        ]),
    ],
    [
        GEN_NOTE,
        "NY disallows the SALT (state/local income tax) deduction entirely and has no cap on real estate tax, unlike federal Schedule A — reflected in the $0 on line 5.",
        f"NY kept the pre-2018 miscellaneous 2%-of-AGI category (investment/advisory fees, tax-prep fees, K-1 portfolio deductions) that federal Schedule A lost after 2018: ${flat['ny_misc_deductions']:,.0f} of fees less the ${ny_item['misc_floor']:,.0f} floor (2% of federal AGI) = ${ny_item['misc']:,.0f}.",
        f"Return also includes $1,540 of 2024 tax-prep fees paid in 2025 that were not in the source PDFs — adding them would raise line 24 by $1,540. Not yet reflected in data/tax-inputs-2025-MFJ-actual.json pending your confirmation of the exact figure.",
        "Line 40's phase-down formula follows NY's own itemized-deduction-adjustment tiers keyed to NY AGI; the estimator computes it directly rather than via the worksheet's literal step-by-step lines.",
    ],
)

print("NY forms done")

# ===========================================================================
# COVER SUMMARY
# ===========================================================================
path = os.path.join(OUT_DIR, "00-Cover-Summary.pdf")
doc = SimpleDocTemplate(path, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch,
                         leftMargin=0.65 * inch, rightMargin=0.65 * inch)
story = [
    Paragraph("2025 Tax Return — Generated Forms Package", title_style),
    Paragraph(SUBTITLE, sub_style),
    HRFlowable(width="100%", color=colors.HexColor("#999999"), thickness=0.75),
    Spacer(1, 8),
]
body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=8)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)

story.append(Paragraph(
    "This package reproduces, form by form, the return the Tax &amp; IRMAA Estimator computes from "
    "<b>data/tax-inputs-2025-MFJ-actual.json</b> (built from your 2025 source documents). "
    "These are <b>estimator-generated worksheets</b> that mirror each official form's line numbers and labels — "
    "not the actual IRS/NY PDF forms (this environment has no network access to irs.gov or tax.ny.gov to fetch "
    "the real fillable PDFs). Use them to review the numbers and transcribe into the real forms or your e-file software; "
    "names, SSNs and signatures are intentionally left blank.", body))

story.append(Paragraph("Forms included", h2))
form_list = [
    "Form 1040 — U.S. Individual Income Tax Return",
    "Schedule 1 — Additional Income and Adjustments to Income",
    "Schedule 3 — Additional Credits and Payments",
    "Schedule A — Itemized Deductions",
    "Schedule B — Interest and Ordinary Dividends",
    "Schedule D — Capital Gains and Losses (with capital-loss carryover)",
    "Form 8995 — Qualified Business Income Deduction (Simplified)",
    "Form 1116 — Foreign Tax Credit (partial — see limitations)",
    "Form IT-201 — New York State Resident Income Tax Return",
    "Form IT-196 — New York Resident Itemized Deductions",
]
for f in form_list:
    story.append(Paragraph(f"&bull; {f}", body))

story.append(Paragraph("Bottom-line figures (this estimator's model)", h2))
data = [
    ["", "Federal", "NY State + NYC"],
    ["Total tax", f"${fed_total_tax:,.0f}", f"${ny_total_tax:,.0f}"],
    ["Total payments/withholding", f"${fed_paid:,.0f}", f"${ny_paid:,.0f}"],
    ["Refund (owed)", f"${-fed_bal:,.0f}" if fed_bal < 0 else f"(${fed_bal:,.0f})",
     f"${-ny_bal:,.0f}" if ny_bal < 0 else f"(${ny_bal:,.0f})"],
]
t = Table(data, colWidths=[2.2 * inch, 2.1 * inch, 2.1 * inch])
t.setStyle(TableStyle([
    ("FONTSIZE", (0, 0), (-1, -1), 9.5),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
]))
story.append(t)
story.append(Spacer(1, 10))

story.append(Paragraph("Known gaps vs. the actual filed return", h2))
story.append(Paragraph(
    "This model comes within about $1,442 (federal) and $212 (NY+NYC) of the real filed 2025 return, entirely from two "
    "items the estimator doesn't attempt to model: the Form 1116 foreign-tax-credit limitation and prior-year carryover "
    "(the real return used a $7,547 carryover pool to claim $2,814 vs. this package's $1,486), and New York's resident "
    "credit for the $51 of tax paid to California on the settlement wages. Both are called out on the relevant form pages. "
    "With the return's actual cash-charity figure ($7,302 vs. $6,786 here) and $1,540 of 2024 tax-prep fees added, every "
    "other line matches the filed return to the dollar. See data/tax-inputs-2025-MFJ-actual-sources.md for the full "
    "field-by-field reconciliation.", body))

doc.build(story)
print("cover summary done")
