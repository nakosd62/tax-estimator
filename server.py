#!/usr/bin/env python3
"""
Multi-State & Multi-Filing Status Tax & IRMAA Estimator Server.
Tax tables are loaded from tax_data.json.
"""

import http.server
import socketserver
import json
import math
import os
import re
import sys
from datetime import datetime
from urllib.parse import unquote

PORT = int(os.environ.get("PORT", 8001))
INF = float("inf")
FILING_STATUSES = ["MFJ", "SINGLE", "MFS", "HOH"]

# Only these files are served; everything else in the folder (source, data, Dockerfile) stays private.
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/script.js": ("script.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
MAX_BODY_BYTES = 64 * 1024

# Saved input files live in ./data next to this script. Saving/listing is for local use only:
# it's switched off on Cloud Run (K_SERVICE is set there) or when SAVE_INPUTS=0.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
SAVE_INPUTS_ENABLED = os.environ.get("SAVE_INPUTS", "1") != "0" and not os.environ.get("K_SERVICE")
LAST_USED_FILE = os.path.join(DATA_DIR, ".last-used.json")   # remembers the last file loaded/saved
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.json$")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _brackets(bracket_list):
    """[[limit|null, rate], ...] -> [(limit|inf, rate), ...]"""
    return [(INF if limit is None else float(limit), float(rate)) for limit, rate in bracket_list]


def _tiers(tier_list):
    """[[max_income|null, amount], ...] -> [(max_income|inf, amount), ...]"""
    return [(INF if cap is None else float(cap), float(amt)) for cap, amt in tier_list]


def _by_status(value, convert=lambda v: v):
    """A value may be given once for all statuses or as a {status: value} dict."""
    if isinstance(value, dict):
        return {s: convert(value.get(s, value.get("SINGLE"))) for s in FILING_STATUSES}
    return {s: convert(value) for s in FILING_STATUSES}


def load_tax_data(file_path="tax_data.json"):
    if not os.path.exists(file_path):
        print(f"Error: Could not find '{file_path}'. Please place it in the same directory.")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for table in ("FED_ORDINARY", "FED_PREFERENTIAL"):
        for year in data[table]:
            for status in data[table][year]:
                data[table][year][status] = _brackets(data[table][year][status])

    data["IRMAA_DATA"].pop("_comment", None)
    data["BASE_PREMIUMS"].pop("_comment", None)
    for year in data["IRMAA_DATA"]:
        yd = data["IRMAA_DATA"][year]
        for status in yd:
            for tier in yd[status]:
                if tier["limit"] is None:
                    tier["limit"] = INF
        yd.setdefault("HOH", yd["SINGLE"])
        yd.setdefault("MFS", yd["SINGLE"])

    # Normalise every state into {status: ...} form.
    for info in data["STATE_TAX_DATA"].values():
        if "brackets_by_year" in info:
            info["brackets_by_year"] = {
                y: _by_status(b, _brackets) for y, b in info["brackets_by_year"].items()
            }
        else:
            info["brackets"] = _by_status(info.get("brackets", []), _brackets)
        info["deduction"] = _by_status(info.get("deduction", 0.0), float)
        info["exemption"] = _by_status(info.get("exemption", 0.0), float)
        info["credit"] = _by_status(info.get("credit", 0.0), float)
        if "exemption_tiers" in info:
            info["exemption_tiers"] = _by_status(info["exemption_tiers"], _tiers)
        if "nyc" in info:
            info["nyc"] = _by_status(info["nyc"], _brackets)

    return data


TAX_CONFIG = load_tax_data(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tax_data.json"))
META = TAX_CONFIG.get("_meta", {})
FED_STANDARD_DEDUCTION = TAX_CONFIG["FED_STANDARD_DEDUCTION"]
FED_ADDITIONAL_65 = TAX_CONFIG["FED_ADDITIONAL_65"]
SENIOR_DEDUCTION = TAX_CONFIG["SENIOR_DEDUCTION"]
SS_TAXABILITY = TAX_CONFIG["SS_TAXABILITY"]
NIIT_THRESHOLDS = TAX_CONFIG["NIIT_THRESHOLDS"]
FED_ORDINARY = TAX_CONFIG["FED_ORDINARY"]
FED_PREFERENTIAL = TAX_CONFIG["FED_PREFERENTIAL"]
FED_ITEMIZED = TAX_CONFIG["FED_ITEMIZED"]
FOREIGN_TAX_CREDIT = TAX_CONFIG["FOREIGN_TAX_CREDIT"]
QBI_REIT = TAX_CONFIG["QBI_REIT"]
SAFE_HARBOR = TAX_CONFIG["SAFE_HARBOR"]
IRMAA_DATA = TAX_CONFIG["IRMAA_DATA"]
IRMAA_PROJECTION = TAX_CONFIG["IRMAA_PROJECTION"]
BASE_PREMIUMS = TAX_CONFIG["BASE_PREMIUMS"]
STATE_TAX_DATA = TAX_CONFIG["STATE_TAX_DATA"]


def _round_to(value, step):
    return round(round(value / step) * step, 2)


def _project_irmaa_year(premium_year: int) -> dict:
    """Project an IRMAA table (all filing statuses) for a premium year CMS hasn't published,
    starting from the latest official year."""
    base_year = max(int(y) for y in IRMAA_DATA)
    base = IRMAA_DATA[str(base_year)]
    years = premium_year - base_year
    infl = IRMAA_PROJECTION["threshold_inflation"]
    growth = (1 + IRMAA_PROJECTION["premium_growth"]) ** years

    # Tiers 1-4 (single) are indexed annually and rounded to $1,000; joint is double.
    single_limits = [_round_to(t["limit"] * (1 + infl) ** years, 1000.0) for t in base["SINGLE"][:4]]
    joint_limits = [2 * l for l in single_limits]

    # Top thresholds ($500k single / $750k joint) stay frozen until indexing starts.
    start = IRMAA_PROJECTION["top_threshold_indexing_start"]
    top_years = max(0, premium_year - (start - 1)) - max(0, base_year - (start - 1))
    top_factor = (1 + infl) ** top_years
    single_top = _round_to(base["SINGLE"][4]["limit"] * top_factor, 1000.0)
    joint_top = _round_to(base["MFJ"][4]["limit"] * top_factor, 1000.0)

    surcharges = [(_round_to(t["part_b"] * growth, 0.1), _round_to(t["part_d"] * growth, 0.1)) for t in base["SINGLE"]]

    def table(limits):
        return [
            {"limit": limit, "tier": i + 1, "part_b": b, "part_d": d}
            for i, (limit, (b, d)) in enumerate(zip(limits + [INF], surcharges))
        ]

    single = table(single_limits + [single_top])
    return {
        "SINGLE": single,
        "HOH": single,
        "MFJ": table(joint_limits + [joint_top]),
        # MFS (lived with spouse): tier 1, then straight to tiers 5 and 6.
        "MFS": [
            {"limit": single_limits[0], "tier": 1, "part_b": 0.0, "part_d": 0.0},
            {"limit": single_top - single_limits[0], "tier": 5, "part_b": surcharges[4][0], "part_d": surcharges[4][1]},
            {"limit": INF, "tier": 6, "part_b": surcharges[5][0], "part_d": surcharges[5][1]},
        ],
    }


def irmaa_table(tax_year: str, filing_status: str) -> tuple[int, bool, list]:
    """IRMAA for premium year = tax year + lookback. Returns (premium_year, projected, tiers)."""
    premium_year = int(tax_year) + IRMAA_PROJECTION["lookback_years"]
    official = IRMAA_DATA.get(str(premium_year))
    if official:
        return premium_year, False, official[filing_status]
    return premium_year, True, _project_irmaa_year(premium_year)[filing_status]


def base_premiums(premium_year: int) -> tuple[float, float, bool]:
    """Standard Part B premium and the Part D 'national base beneficiary premium'
    (a CMS reference figure, not any specific plan's actual cost) for a premium year.
    Returns (part_b, part_d_base, projected)."""
    official = BASE_PREMIUMS.get(str(premium_year))
    if official:
        return official["part_b"], official["part_d_base"], False

    base_year = max(int(y) for y in BASE_PREMIUMS)
    base = BASE_PREMIUMS[str(base_year)]
    growth = (1 + IRMAA_PROJECTION["premium_growth"]) ** (premium_year - base_year)
    return _round_to(base["part_b"] * growth, 0.1), _round_to(base["part_d_base"] * growth, 0.1), True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def json_safe(value):
    if isinstance(value, float) and math.isinf(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def _fmt_limit(limit):
    return "Over" if limit == INF else f"${limit:,.0f}"


def calculate_progressive_tax(income: float, brackets: list) -> tuple[float, list[dict]]:
    if income <= 0 or not brackets:
        return 0.0, []

    tax = 0.0
    previous_limit = 0.0
    breakdown = []

    for limit, rate in brackets:
        top = min(income, limit)
        bracket_income = top - previous_limit
        bracket_tax = bracket_income * rate
        tax += bracket_tax
        breakdown.append({
            "range": f"${previous_limit:,.0f} - {_fmt_limit(limit)}",
            "taxable": bracket_income,
            "rate": rate,
            "tax": bracket_tax,
        })
        if income <= limit:
            break
        previous_limit = limit

    return tax, breakdown


def calculate_preferential_tax(ord_income_taxable: float, pref_income_taxable: float, pref_brackets: list) -> tuple[float, list[dict]]:
    if pref_income_taxable <= 0:
        return 0.0, []

    tax = 0.0
    previous_limit = 0.0
    breakdown = []

    for limit, rate in pref_brackets:
        bracket_start = max(previous_limit, ord_income_taxable)
        bracket_end = min(limit, ord_income_taxable + pref_income_taxable)

        if bracket_end > bracket_start:
            taxable_in_bracket = bracket_end - bracket_start
            bracket_tax = taxable_in_bracket * rate
            tax += bracket_tax
            breakdown.append({
                "range": f"${previous_limit:,.0f} - {_fmt_limit(limit)}",
                "taxable": taxable_in_bracket,
                "rate": rate,
                "tax": bracket_tax,
            })
        previous_limit = limit

    return tax, breakdown


def get_irmaa_tier(magi: float, irmaa_tiers: list) -> dict:
    floor = 0.0
    for tier in irmaa_tiers:
        if magi <= tier["limit"]:
            return {**tier, "floor": floor}
        floor = tier["limit"]
    return {**irmaa_tiers[-1], "floor": floor}


def taxable_social_security(ss_benefits: float, other_income: float, tax_exempt_interest: float, filing_status: str) -> float:
    """IRS Pub. 915 worksheet: 0%, up to 50%, or up to 85% of benefits are taxable
    depending on provisional income (other AGI + tax-exempt interest + half of benefits)."""
    if ss_benefits <= 0:
        return 0.0
    base1, base2 = SS_TAXABILITY[filing_status]
    provisional = other_income + tax_exempt_interest + 0.5 * ss_benefits
    if provisional <= base1:
        return 0.0
    if provisional <= base2:
        return min(0.5 * ss_benefits, 0.5 * (provisional - base1))
    return min(
        0.85 * ss_benefits,
        0.85 * (provisional - base2) + min(0.5 * ss_benefits, 0.5 * (base2 - base1)),
    )


def senior_deduction(year: str, filing_status: str, filers_65: int, magi: float) -> float:
    """OBBBA $6,000-per-senior deduction (2025-2028), phased out at 6% of MAGI over threshold."""
    threshold = SENIOR_DEDUCTION["thresholds"].get(filing_status)
    if year not in SENIOR_DEDUCTION["years"] or threshold is None or filers_65 <= 0:
        return 0.0
    per_person = max(0.0, SENIOR_DEDUCTION["amount"] - SENIOR_DEDUCTION["phaseout_rate"] * max(0.0, magi - threshold))
    return per_person * filers_65


def ny_supplemental_tax(taxable: float, ny_agi: float, brackets: list, rules: dict) -> float:
    """NY "tax benefit recapture" (Tax Law §601(d-1); IT-201 tax computation worksheets).

    Above an AGI threshold, NY claws back the benefit of the lower brackets so that,
    once fully phased in, all taxable income is taxed at the rate of the taxpayer's top
    bracket. For bracket i with lower limit L_i and rate r_i the full benefit is
    F_i = r_i * L_i - T(L_i). Each step phases in over the next $50,000 of NY AGI:
      * first recapture bracket (the one containing the AGI threshold, and anything below):
            T(TI) + (r * TI - T(TI)) * min(1, (AGI - threshold) / 50,000)
      * higher bracket i:  T(TI) + F_{i-1} + (F_i - F_{i-1}) * min(1, (AGI - L_i) / 50,000)
      * AGI above $25M: TI * top rate
    Returns only the supplemental amount (on top of the rate-schedule tax).
    """
    threshold = rules["agi_threshold"]
    if ny_agi <= threshold or taxable <= 0:
        return 0.0

    base_tax, _ = calculate_progressive_tax(taxable, brackets)
    if ny_agi > rules["flat_above_agi"]:
        return max(0.0, taxable * brackets[-1][1] - base_tax)

    phase = rules["phase_in"]
    lowers = [0.0] + [limit for limit, _ in brackets[:-1]]   # lower limit of each bracket
    rates = [rate for _, rate in brackets]

    def full_benefit(i):
        return rates[i] * lowers[i] - calculate_progressive_tax(lowers[i], brackets)[0]

    def fraction(excess):
        return round(min(1.0, max(0.0, excess) / phase), 4)

    first = next(i for i, (limit, _) in enumerate(brackets) if lowers[i] <= threshold < limit)
    current = next(i for i, (limit, _) in enumerate(brackets) if taxable <= limit)

    if current <= first:
        return (rates[first] * taxable - base_tax) * fraction(ny_agi - threshold)

    prior = full_benefit(current - 1)
    increment = full_benefit(current) - prior
    return prior + increment * fraction(ny_agi - lowers[current])


def _num(data, key, default=0.0, allow_negative=False):
    raw = data.get(key, default)
    if raw is None or raw == "":
        return default
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"'{key}' must be a finite number.")
    if value < 0 and not allow_negative:
        raise ValueError(f"'{key}' cannot be negative.")
    return value


def _count(data, key, max_count):
    try:
        value = int(data.get(key, 0) or 0)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a whole number.")
    return max(0, min(value, max_count))


def aggregate_k1s(k1_list):
    """Sums a list of K-1 entries into the income/deduction buckets they flow into.

    Each entry may have: label, ordinary_income (box 1, can be negative),
    net_rental (box 2, can be negative), interest (box 5), ord_dividends (box 6a),
    qual_dividends (box 6b), royalties (box 7), st_cap_gain (box 8, can be negative),
    lt_cap_gain (box 9a, can be negative), portfolio_deductions (box 13, e.g. code AE),
    suspended (bool — box 1/2 trade-or-business/rental income or loss is excluded from
    this year's totals when passive-activity/at-risk/basis rules suspend it; portfolio
    items — interest, dividends, capital gains, box 13 deductions — are never suspended).
    """
    totals = {
        "other_income": 0.0, "interest": 0.0, "ord_dividends": 0.0, "qual_dividends": 0.0,
        "st_cap_gain": 0.0, "lt_cap_gain": 0.0, "portfolio_deductions": 0.0,
        "suspended_loss": 0.0,
    }
    entries = []
    for k1 in (k1_list or []):
        if not isinstance(k1, dict):
            continue
        ordinary_income = _num(k1, "ordinary_income", allow_negative=True)
        net_rental = _num(k1, "net_rental", allow_negative=True)
        interest = _num(k1, "interest", allow_negative=True)
        ord_div = _num(k1, "ord_dividends")
        qual_div = _num(k1, "qual_dividends")
        royalties = _num(k1, "royalties")
        st_cg = _num(k1, "st_cap_gain", allow_negative=True)
        lt_cg = _num(k1, "lt_cap_gain", allow_negative=True)
        portfolio_ded = _num(k1, "portfolio_deductions")
        suspended = bool(k1.get("suspended", False))

        active_business = 0.0 if suspended else (ordinary_income + net_rental)
        if suspended:
            totals["suspended_loss"] += ordinary_income + net_rental

        totals["other_income"] += active_business + royalties
        totals["interest"] += interest
        totals["ord_dividends"] += ord_div
        totals["qual_dividends"] += qual_div
        totals["st_cap_gain"] += st_cg
        totals["lt_cap_gain"] += lt_cg
        totals["portfolio_deductions"] += portfolio_ded

        entries.append({
            "label": str(k1.get("label") or "K-1"),
            "ordinary_income": ordinary_income, "net_rental": net_rental,
            "interest": interest, "ord_dividends": ord_div, "qual_dividends": qual_div,
            "royalties": royalties, "st_cap_gain": st_cg, "lt_cap_gain": lt_cg,
            "portfolio_deductions": portfolio_ded, "suspended": suspended,
            "net_this_year": (0.0 if suspended else (ordinary_income + net_rental)) + royalties
                             + interest + ord_div + st_cg + lt_cg - portfolio_ded,
        })
    return totals, entries


# ---------------------------------------------------------------------------
# Itemized deductions
# ---------------------------------------------------------------------------

def federal_itemized(year: str, filing_status: str, agi: float, senior_ded: float, d: dict) -> dict:
    """Federal Schedule A under OBBBA: medical over 7.5% of AGI, capped SALT (with high-income
    phase-down), mortgage interest, charity (0.5%-of-AGI floor from 2026), other, and the 2026+
    "2/37" limitation for taxpayers in the 37% bracket."""
    rules = FED_ITEMIZED
    y = int(year)
    mfs = filing_status == "MFS"

    medical = max(0.0, d["medical"] - rules["medical_agi_floor"] * agi)

    salt_paid = d["salt_income"] + d["real_estate_tax"]
    cap = rules["salt_cap"].get(year, rules["salt_cap_after"])
    threshold = rules["salt_phase_threshold"].get(year)
    floor = rules["salt_floor"]
    if mfs:
        cap, floor = cap / 2, floor / 2
        threshold = threshold / 2 if threshold else None
    if threshold:
        cap = max(floor, cap - rules["salt_phase_rate"] * max(0.0, agi - threshold))
    salt = min(salt_paid, cap)

    charity_total = d["charity_cash"] + d["charity_other"]
    charity_floor = 0.0
    if y >= rules["charity_agi_floor"]["from_year"]:
        charity_floor = rules["charity_agi_floor"]["rate"] * agi
    charity = max(0.0, charity_total - charity_floor)

    gross = medical + salt + d["mortgage_interest"] + charity + d["other_itemized"]

    limitation = 0.0
    lim = rules["top_bracket_limitation"]
    if y >= lim["from_year"] and gross > 0:
        top_start = FED_ORDINARY[year][filing_status][-2][0]   # start of the 37% bracket
        excess = max(0.0, (agi - senior_ded) - top_start)       # taxable income + itemized, over the 37% start
        limitation = lim["fraction"] * min(gross, excess)

    return {
        "medical": medical,
        "salt_paid": salt_paid,
        "salt_cap": cap,
        "salt": salt,
        "mortgage_interest": d["mortgage_interest"],
        "charity_total": charity_total,
        "charity_floor": min(charity_floor, charity_total),
        "charity": charity,
        "other": d["other_itemized"],
        "limitation": limitation,
        "total": gross - limitation,
    }


def ny_itemized(year: str, filing_status: str, fed_agi: float, ny_agi: float, d: dict, rules: dict) -> dict:
    """NY Form IT-196 (full-year resident). NY itemizes independently of the federal return,
    using pre-2018 federal rules: 10% medical floor, no SALT cap, state/local income taxes
    removed, the Pease limitation (federal AGI), then the NY itemized deduction adjustment
    and the >$1M / >$10M charity-only rules (NY AGI)."""
    medical = max(0.0, d["medical"] - rules["medical_agi_floor"] * fed_agi)
    income_taxes = d["salt_income"]
    charity = d["charity_cash"] + d["charity_other"]
    misc_rules = rules.get("misc_deductions", {"agi_floor": 0.02})
    misc = max(0.0, d.get("ny_misc", 0.0) - misc_rules["agi_floor"] * fed_agi)   # lines 25-28
    l1 = medical + income_taxes + d["real_estate_tax"] + d["mortgage_interest"] + charity + misc + d["other_itemized"]
    l2 = medical   # categories exempt from the Pease limitation (medical; investment interest not modeled)

    by_year = rules["pease_threshold"]
    threshold = by_year.get(year, by_year[max(by_year)])[filing_status]
    pease = 0.0
    ratio = 0.0
    if fed_agi > threshold and l1 > l2:
        pease = min(rules["pease_max_share"] * (l1 - l2), rules["pease_rate"] * (fed_agi - threshold))
        ratio = round(pease / (l1 - l2), 4)
    line40 = l1 - pease
    line41 = income_taxes * (1 - ratio)        # remove state/local income taxes (net of Pease share)
    line45 = line40 - line41

    # NY itemized deduction adjustment (lines 46-47), based on NY AGI.
    base = rules["adjustment_base"][filing_status]
    step2 = rules["adjustment_step2_agi"]
    adjustment = 0.0
    if ny_agi > rules["charity_only_agi"]:
        high = ny_agi > rules["charity_only_high_agi"] and int(year) <= rules["charity_only_high_until_year"]
        pct = rules["charity_only_high_pct"] if high else rules["charity_only_pct"]
        total = pct * charity
        adjustment = line45 - total
    else:
        if ny_agi > rules["adjustment_full_agi"]:
            adjustment = 0.50 * line45
        elif ny_agi > step2:
            g = round(min(ny_agi - step2, 50000.0) / 50000.0, 4)
            adjustment = 0.25 * line45 + g * 0.25 * line45
        elif ny_agi > rules["adjustment_min_agi"] and ny_agi > base:
            f = round(min(ny_agi - base, 50000.0) / 50000.0, 4)
            adjustment = f * 0.25 * line45
        total = line45 - adjustment

    return {
        "medical": medical,
        "taxes": d["real_estate_tax"],
        "income_taxes_removed": income_taxes,
        "mortgage_interest": d["mortgage_interest"],
        "charity": charity,
        "misc": misc,
        "misc_floor": min(d.get("ny_misc", 0.0), misc_rules["agi_floor"] * fed_agi),
        "other": d["other_itemized"],
        "pease": pease,
        "adjustment": adjustment,
        "total": max(0.0, total),
    }


def nyc_school_tax_credit(filing_status, fed_agi, taxable_ira, nyc_taxable, rules):
    """Refundable NYC school tax credit (IT-201 lines 69 and 69a). Returns (fixed, rate_reduction)."""
    fixed = rules["fixed"][filing_status] if fed_agi - taxable_ira <= rules["income_limit"] else 0.0
    rate = 0.0
    if 0 < nyc_taxable <= rules["rate_ti_limit"]:
        threshold, base = rules["threshold"][filing_status]
        if nyc_taxable <= threshold:
            rate = rules["low_rate"] * nyc_taxable
        else:
            rate = base + rules["high_rate"] * (nyc_taxable - threshold)
    return fixed, rate


def safe_harbor(current_tax, paid, prior_tax, prior_agi, filing_status, min_balance):
    """Underpayment-penalty check: paid >= lesser of 90% of this year's tax or 100%/110% of last
    year's (110% if prior-year AGI > $150k / $75k MFS); or balance due under min_balance."""
    rules = SAFE_HARBOR
    required = rules["current_pct"] * current_tax
    basis = "90% of this year's tax"
    if prior_tax is not None:
        high = prior_agi is not None and prior_agi > rules["high_agi"][filing_status]
        pct = rules["prior_pct_high_agi"] if high else rules["prior_pct"]
        prior_required = pct * prior_tax
        if prior_required < required:
            required = prior_required
            basis = f"{pct:.0%} of last year's tax"
    balance = current_tax - paid
    met = paid >= required or balance < min_balance
    return {
        "required": required,
        "basis": basis,
        "met": met,
        "shortfall": 0.0 if met else required - paid,
    }


# ---------------------------------------------------------------------------
# State tax
# ---------------------------------------------------------------------------

def compute_state_tax(params, year, filing_status, state_agi, fed_deduction_total, itemized=0.0):
    """Returns (state_taxable, state_deduction, state_tax, breakdown, credit, supplemental, itemizing)."""
    if "brackets_by_year" in params:
        by_year = params["brackets_by_year"]
        brackets = by_year.get(year, by_year[max(by_year)])[filing_status]
    else:
        brackets = params["brackets"][filing_status]

    if params.get("uses_federal_deduction"):
        deduction = fed_deduction_total
    else:
        deduction = params["deduction"][filing_status]

    # Sliding-scale standard deduction (e.g. WI): best of [start, rate, max_amount] segments.
    phase = params.get("std_deduction_phaseout", {}).get(filing_status)
    if phase:
        deduction += max(
            max(0.0, min(max_amt, max_amt - rate * (state_agi - start)))
            for start, rate, max_amt in phase
        )

    deduction += params["exemption"][filing_status]

    # Income-tiered exemptions (e.g. OH tiers, IL cliff).
    tiers = params.get("exemption_tiers", {}).get(filing_status)
    if tiers:
        deduction += next(amt for cap, amt in tiers if state_agi <= cap)

    itemizing = itemized > deduction
    if itemizing:
        deduction = itemized

    state_taxable = max(0.0, state_agi - deduction)
    tax, breakdown = calculate_progressive_tax(state_taxable, brackets)

    supplemental = 0.0
    if "supplemental_tax" in params:
        supplemental = ny_supplemental_tax(state_taxable, state_agi, brackets, params["supplemental_tax"])
        if supplemental > 0:
            breakdown.append({"range": "Supplemental tax (benefit recapture)", "taxable": None, "rate": None, "tax": supplemental})
            tax += supplemental

    credit = params["credit"][filing_status]
    phase = params.get("credit_phaseout")
    if credit and phase:
        credit = max(0.0, credit - phase["rate"][filing_status] * max(0.0, state_agi - phase["start"][filing_status]))
    credit = min(credit, tax)
    if credit > 0:
        breakdown.append({"range": "Personal credit", "taxable": None, "rate": None, "tax": -credit})
        tax -= credit

    return state_taxable, deduction, tax, breakdown, credit, supplemental, itemizing


# ---------------------------------------------------------------------------
# Main calculation
# ---------------------------------------------------------------------------

def perform_calculations(data: dict) -> dict:
    year = str(data.get("year", "2026"))
    if year not in FED_ORDINARY:
        year = "2026"

    state_code = str(data.get("state", "NY")).upper()
    if state_code not in STATE_TAX_DATA:
        state_code = "NY"

    filing_status = str(data.get("filing_status", "MFJ")).upper()
    if filing_status not in FILING_STATUSES:
        filing_status = "MFJ"

    married = filing_status in ("MFJ", "MFS")
    max_filers = 2 if filing_status == "MFJ" else 1

    fed_ord_brackets = FED_ORDINARY[year][filing_status]
    fed_pref_brackets = FED_PREFERENTIAL[year][filing_status]
    irmaa_premium_year, irmaa_projected, irmaa_tiers = irmaa_table(year, filing_status)
    part_b_base, part_d_base, base_premiums_projected = base_premiums(irmaa_premium_year)
    niit_threshold = NIIT_THRESHOLDS[filing_status]
    state_params = STATE_TAX_DATA[state_code]

    # --- Inputs ---
    wages = _num(data, "wages")
    other_income = _num(data, "other_income")
    pension = _num(data, "pension")
    ira_dist = _num(data, "ira_dist")
    roth_conv = _num(data, "roth_conv")
    social_security = _num(data, "social_security")
    interest = _num(data, "interest")
    ord_dividends = _num(data, "ordinary_dividends")
    q_dividends = _num(data, "qualified_dividends")
    tax_exempt = _num(data, "tax_exempt")
    st_cap_gains = _num(data, "st_cap_gains", allow_negative=True)
    lt_cap_gains = _num(data, "lt_cap_gains", allow_negative=True)
    treasury_interest = _num(data, "treasury_interest")   # U.S. government income in interest or dividends
    sec199a_dividends = _num(data, "sec199a_dividends")
    oos_muni_interest = _num(data, "oos_muni_interest")
    foreign_tax_paid = _num(data, "foreign_tax_paid")
    ftc_carryforward = _num(data, "ftc_carryforward")
    foreign_source_income = _num(data, "foreign_source_income")
    cl_carryforward_st = _num(data, "cl_carryforward_st")
    cl_carryforward_lt = _num(data, "cl_carryforward_lt")

    # K-1s (partnerships/S-corps) fold into the plain income/deduction fields above: box 1/2
    # trade-or-business/rental income or loss (unless suspended by passive/at-risk/basis rules)
    # and box 7 royalties join "other income"; box 5/6a/6b/8/9a portfolio items join interest,
    # dividends and capital gains; box 13 portfolio deductions (e.g. code AE) join NY misc. deductions.
    k1_totals, k1_entries = aggregate_k1s(data.get("k1s"))
    other_income += k1_totals["other_income"]
    interest += k1_totals["interest"]
    ord_dividends += k1_totals["ord_dividends"]
    q_dividends += k1_totals["qual_dividends"]
    st_cap_gains += k1_totals["st_cap_gain"]
    lt_cap_gains += k1_totals["lt_cap_gain"]

    # Prior-year unused capital losses carry over and keep their character
    # (short-term carryover reduces this year's short-term gain/loss, long-term
    # reduces long-term), per the Schedule D Capital Loss Carryover Worksheet.
    st_cap_gains -= cl_carryforward_st
    lt_cap_gains -= cl_carryforward_lt

    # Payments
    fed_withholding = _num(data, "fed_withholding")
    fed_estimated = _num(data, "fed_estimated")
    ny_withholding = _num(data, "ny_withholding")
    ny_estimated = _num(data, "ny_estimated")
    prior_agi = None if data.get("prior_agi") in (None, "") else _num(data, "prior_agi")
    prior_fed_tax = None if data.get("prior_fed_tax") in (None, "") else _num(data, "prior_fed_tax")
    prior_ny_tax = None if data.get("prior_ny_tax") in (None, "") else _num(data, "prior_ny_tax")

    # Itemized deduction components. State/local income taxes default to the NY payments entered.
    salt_income_raw = data.get("salt_income")
    salt_income_auto = salt_income_raw in (None, "")
    itemized_inputs = {
        "medical": _num(data, "medical"),
        "salt_income": ny_withholding + ny_estimated if salt_income_auto else _num(data, "salt_income"),
        "real_estate_tax": _num(data, "real_estate_tax"),
        "mortgage_interest": _num(data, "mortgage_interest"),
        "charity_cash": _num(data, "charity_cash"),
        "charity_other": _num(data, "charity_other"),
        "other_itemized": _num(data, "other_itemized"),
        "ny_misc": _num(data, "ny_misc_deductions") + k1_totals["portfolio_deductions"],
    }
    filers_65 = _count(data, "filers_65", max_filers)
    filers_59 = _count(data, "filers_59", max_filers)
    gov_pension = bool(data.get("gov_pension", False))

    if q_dividends > ord_dividends:
        raise ValueError("Qualified dividends cannot exceed total ordinary dividends.")
    if treasury_interest > interest + ord_dividends:
        raise ValueError("U.S. government income is part of taxable interest or dividends, so it cannot exceed their total.")
    if sec199a_dividends > ord_dividends:
        raise ValueError("Section 199A (REIT) dividends are part of ordinary dividends, so they cannot exceed them.")
    if oos_muni_interest > tax_exempt:
        raise ValueError("Out-of-state muni interest is part of tax-exempt interest, so it cannot exceed it.")

    # --- Capital gain / loss netting & $3,000 loss cap ---
    max_loss_cap = 1500.0 if filing_status == "MFS" else 3000.0
    net_cap_gain = st_cap_gains + lt_cap_gains

    if net_cap_gain >= 0:
        if st_cap_gains >= 0 and lt_cap_gains >= 0:
            taxable_stcg, taxable_ltcg = st_cap_gains, lt_cap_gains
        elif st_cap_gains < 0:          # ST loss offsets LT gain
            taxable_stcg, taxable_ltcg = 0.0, net_cap_gain
        else:                            # LT loss offsets ST gain
            taxable_stcg, taxable_ltcg = net_cap_gain, 0.0
    else:
        taxable_stcg = max(net_cap_gain, -max_loss_cap)
        taxable_ltcg = 0.0

    # Approximate capital loss left to carry forward to next year (informational only --
    # the real Capital Loss Carryover Worksheet can split this differently between
    # short-term and long-term; this just reports the total still unused).
    cl_carryforward_remaining = max(0.0, -net_cap_gain - max_loss_cap) if net_cap_gain < 0 else 0.0

    non_qualified_dividends = ord_dividends - q_dividends
    preferential_income = q_dividends + taxable_ltcg

    # --- Federal AGI (with Social Security taxability worksheet) ---
    ordinary_ex_ss = (
        wages + other_income + pension + ira_dist + roth_conv +
        interest + non_qualified_dividends + taxable_stcg
    )
    agi_ex_ss = ordinary_ex_ss + preferential_income
    taxable_ss_fed = taxable_social_security(social_security, agi_ex_ss, tax_exempt, filing_status)
    fed_agi = agi_ex_ss + taxable_ss_fed

    # --- Federal deductions ---
    base_standard = FED_STANDARD_DEDUCTION[year][filing_status]
    additional_65 = FED_ADDITIONAL_65[year]["MARRIED" if married else "UNMARRIED"] * filers_65
    standard_total = base_standard + additional_65
    senior_ded = senior_deduction(year, filing_status, filers_65, fed_agi)
    fed_item = federal_itemized(year, filing_status, fed_agi, senior_ded, itemized_inputs)
    if fed_item["total"] > standard_total:
        fed_deduction, fed_deduction_type = fed_item["total"], "itemized"
    else:
        fed_deduction, fed_deduction_type = standard_total, "standard"

    # 2026+: non-itemizers may deduct cash gifts to charity up to $1,000 ($2,000 MFJ).
    nonitemizer_charity = 0.0
    nic = FED_ITEMIZED["nonitemizer_cash_charity"]
    if fed_deduction_type == "standard" and int(year) >= nic["from_year"]:
        nonitemizer_charity = min(itemized_inputs["charity_cash"], nic[filing_status])

    fed_taxable = max(0.0, fed_agi - fed_deduction - senior_ded - nonitemizer_charity)

    # Section 199A deduction on qualified REIT dividends (Form 8995): 20%, limited to 20% of
    # (taxable income before this deduction - net capital gain).
    qbi_deduction = min(
        QBI_REIT["rate"] * sec199a_dividends,
        QBI_REIT["rate"] * max(0.0, fed_taxable - preferential_income),
    )
    fed_taxable = max(0.0, fed_taxable - qbi_deduction)

    # --- Federal tax ---
    pref_portion = min(preferential_income, fed_taxable)
    ordinary_portion = max(0.0, fed_taxable - pref_portion)
    fed_ord_tax, fed_ord_breakdown = calculate_progressive_tax(ordinary_portion, fed_ord_brackets)
    fed_pref_tax, fed_pref_breakdown = calculate_preferential_tax(ordinary_portion, pref_portion, fed_pref_brackets)
    fed_tax = fed_ord_tax + fed_pref_tax

    # Foreign tax credit. Never offsets NIIT. A carryforward from a prior year's unused
    # credit (Form 1116 Schedule B) joins this year's foreign tax paid before any limit
    # is applied; whatever still can't be used this year carries forward again (not
    # tracked automatically across runs -- see ftc_carryforward_unused below).
    foreign_tax_pool = foreign_tax_paid + ftc_carryforward
    no_form_limit = FOREIGN_TAX_CREDIT["no_form_limit"][filing_status]

    # Under the no-Form-1116 election (all foreign tax from passive income reported on a
    # 1099, total under $300/$600, and no carryover to or from this year), the credit is
    # simply limited to regular tax. Otherwise Form 1116 applies the real limit: regular
    # tax times (foreign-source taxable income / total taxable income) -- so the credit
    # can be well below "all of it up to your tax bill" when foreign income is a small
    # share of the total, which is the common case for a little foreign tax withheld by
    # an international fund against mostly US-source income.
    form1116_required = foreign_tax_pool > no_form_limit or ftc_carryforward > 0
    if form1116_required:
        ftc_ratio = min(1.0, foreign_source_income / fed_taxable) if fed_taxable > 0 else 0.0
        form1116_limit = fed_tax * ftc_ratio
    else:
        ftc_ratio = None
        form1116_limit = fed_tax

    foreign_tax_credit = min(foreign_tax_pool, form1116_limit)
    ftc_carryforward_unused = max(0.0, foreign_tax_pool - foreign_tax_credit)

    # --- NIIT ---
    nii_cap_gain = max(net_cap_gain, -max_loss_cap)
    nii = interest + ord_dividends + nii_cap_gain
    niit = 0.038 * min(max(0.0, nii), max(0.0, fed_agi - niit_threshold))

    # --- State AGI (Social Security is exempt in all modeled states) ---
    state_agi = agi_ex_ss
    if state_code == "NY":
        # NY modifications: subtract U.S. obligation interest, add other states' muni interest.
        state_agi = state_agi - treasury_interest + oos_muni_interest

    # NY: government pensions fully exempt; $20k per filer 59½+ on other pension/IRA/conversion income.
    ny_gov_pension_exempt = 0.0
    ny_retirement_exclusion = 0.0
    if state_code == "NY":
        if gov_pension:
            ny_gov_pension_exempt = pension
        eligible = (0.0 if gov_pension else pension) + ira_dist + roth_conv
        cap = state_params.get("retirement_exclusion", {}).get("per_person", 0.0) * filers_59
        ny_retirement_exclusion = min(eligible, cap)
        state_agi = max(0.0, state_agi - ny_gov_pension_exempt - ny_retirement_exclusion)

    ny_item = None
    state_itemized = 0.0
    if "itemized" in state_params:
        ny_item = ny_itemized(year, filing_status, fed_agi, state_agi, itemized_inputs, state_params["itemized"])
        state_itemized = ny_item["total"]

    (state_taxable, state_deduction, state_tax, state_breakdown,
     state_credit, state_supplemental, state_itemizing) = compute_state_tax(
        state_params, year, filing_status, state_agi, fed_deduction + senior_ded, state_itemized
    )

    # --- NYC (NYC residency assumed for NY) ---
    nyc_tax, nyc_breakdown = 0.0, []
    if state_code == "NY":
        nyc_tax, nyc_breakdown = calculate_progressive_tax(state_taxable, state_params["nyc"][filing_status])

    # --- IRMAA (this year's MAGI sets premiums two years later) ---
    irmaa_magi = fed_agi + tax_exempt
    irmaa = get_irmaa_tier(irmaa_magi, irmaa_tiers)

    nyc_school_fixed, nyc_school_rate = 0.0, 0.0
    if state_code == "NY" and "nyc_school_tax_credit" in state_params:
        nyc_school_fixed, nyc_school_rate = nyc_school_tax_credit(
            filing_status, fed_agi, ira_dist, state_taxable, state_params["nyc_school_tax_credit"])
    nyc_school_credit = nyc_school_fixed + nyc_school_rate

    fed_total_tax = fed_tax - foreign_tax_credit + niit
    ny_total_tax = state_tax + nyc_tax - nyc_school_credit   # school credit is refundable
    total_tax = fed_total_tax + ny_total_tax
    net_income = fed_agi - total_tax

    # --- Payments, balance due & safe harbor ---
    fed_paid = fed_withholding + fed_estimated
    ny_paid = ny_withholding + ny_estimated
    prior_agi_for_test = prior_agi
    payments = {
        "federal": {
            "total_tax": fed_total_tax,
            "withholding": fed_withholding,
            "estimated": fed_estimated,
            "paid": fed_paid,
            "balance": fed_total_tax - fed_paid,
            "safe_harbor": safe_harbor(fed_total_tax, fed_paid, prior_fed_tax, prior_agi_for_test,
                                       filing_status, SAFE_HARBOR["federal_min_balance"]),
        },
        "ny": {
            "total_tax": ny_total_tax,
            "withholding": ny_withholding,
            "estimated": ny_estimated,
            "paid": ny_paid,
            "balance": ny_total_tax - ny_paid,
            "safe_harbor": safe_harbor(ny_total_tax, ny_paid, prior_ny_tax, prior_agi_for_test,
                                       filing_status, SAFE_HARBOR["ny_min_balance"]),
        },
    }

    return {
        "year": year,
        "year_projected": year in META.get("projected_years", []),
        "state": state_code,
        "filing_status": filing_status,
        "agi": fed_agi,
        "state_agi": state_agi,
        "taxable_social_security": taxable_ss_fed,
        "non_qualified_dividends": non_qualified_dividends,
        "irmaa_magi": irmaa_magi,
        "fed_standard_deduction": standard_total,
        "fed_additional_65": additional_65,
        "fed_deduction": fed_deduction,
        "fed_deduction_type": fed_deduction_type,
        "fed_itemized": fed_item,
        "nonitemizer_charity": nonitemizer_charity,
        "qbi_deduction": qbi_deduction,
        "sec199a_dividends": sec199a_dividends,
        "nyc_school_credit": nyc_school_credit,
        "nyc_school_credit_fixed": nyc_school_fixed,
        "nyc_school_credit_rate": nyc_school_rate,
        "k1_entries": k1_entries,
        "k1_totals": k1_totals,
        "salt_income_used": itemized_inputs["salt_income"],
        "salt_income_auto": salt_income_auto,
        "ny_itemized": ny_item,
        "state_itemizing": state_itemizing,
        "state_standard_deduction": state_params["deduction"][filing_status],
        "treasury_interest": treasury_interest,
        "oos_muni_interest": oos_muni_interest,
        "foreign_tax_credit": foreign_tax_credit,
        "foreign_tax_no_form_limit": no_form_limit,
        "foreign_source_income": foreign_source_income,
        "form1116_required": form1116_required,
        "form1116_limit": form1116_limit if form1116_required else None,
        "ftc_ratio": ftc_ratio,
        "ftc_carryforward": ftc_carryforward,
        "ftc_carryforward_unused": ftc_carryforward_unused,
        "cl_carryforward_st": cl_carryforward_st,
        "cl_carryforward_lt": cl_carryforward_lt,
        "cl_carryforward_remaining": cl_carryforward_remaining,
        "fed_total_tax": fed_total_tax,
        "ny_total_tax": ny_total_tax,
        "payments": payments,
        "senior_deduction": senior_ded,
        "state_deduction": state_deduction,
        "state_credit": state_credit,
        "state_supplemental_tax": state_supplemental,
        "ny_retirement_exclusion": ny_retirement_exclusion,
        "ny_gov_pension_exempt": ny_gov_pension_exempt,
        "fed_taxable": fed_taxable,
        "nys_taxable": state_taxable,
        "fed_tax": fed_tax,
        "fed_ord_tax": fed_ord_tax,
        "fed_pref_tax": fed_pref_tax,
        "fed_ord_breakdown": fed_ord_breakdown,
        "fed_pref_breakdown": fed_pref_breakdown,
        "niit": niit,
        "nys_tax": state_tax,
        "nys_breakdown": state_breakdown,
        "nyc_tax": nyc_tax,
        "nyc_breakdown": nyc_breakdown,
        "irmaa_premium_year": irmaa_premium_year,
        "irmaa_projected": irmaa_projected,
        "irmaa_tier": irmaa["tier"],
        "irmaa_limit": irmaa["limit"],
        "irmaa_floor": irmaa["floor"],
        "irmaa_over_floor": max(0.0, irmaa_magi - irmaa["floor"]) if irmaa["tier"] > 1 else 0.0,
        "irmaa_limits": [t["limit"] for t in irmaa_tiers],
        "irmaa_part_b": irmaa["part_b"],
        "irmaa_part_d": irmaa["part_d"],
        "part_b_base": part_b_base,
        "part_d_base": part_d_base,
        "base_premiums_projected": base_premiums_projected,
        "total_tax": total_tax,
        "net_income": net_income,
        "effective_rate": total_tax / fed_agi if fed_agi > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class TaxEstimatorHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _send_json(self, status, payload):
        body = json.dumps(json_safe(payload)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, head_only=False):
        entry = STATIC_FILES.get(self.path.split("?", 1)[0])
        if not entry:
            self.send_error(404)
            return
        filename, content_type = entry
        with open(filename, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    # --- Saved inputs (./data) -------------------------------------------------
    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Invalid request body.")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    @staticmethod
    def _safe_name(name):
        name = (name or "").strip()
        if not name.lower().endswith(".json"):
            name += ".json"
        if not SAFE_FILENAME.match(name) or ".." in name:
            raise ValueError("File names may use letters, numbers, spaces, '.', '_' and '-' only.")
        return name

    @staticmethod
    def _remember(name):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(LAST_USED_FILE, "w", encoding="utf-8") as f:
                json.dump({"name": name}, f)
        except OSError:
            pass

    def _last_input(self):
        """The file last loaded or saved, with its contents, so the page can restore it on reload."""
        if not SAVE_INPUTS_ENABLED:
            self._send_json(200, {"name": None})
            return
        try:
            with open(LAST_USED_FILE, "r", encoding="utf-8") as f:
                name = self._safe_name(json.load(f).get("name"))
            with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as f:
                self._send_json(200, {"name": name, "data": json.load(f)})
        except (OSError, ValueError, AttributeError):
            self._send_json(200, {"name": None})

    def _set_last_input(self):
        if not SAVE_INPUTS_ENABLED:
            self._send_json(403, {"error": "Saved inputs are only available when running locally."})
            return
        try:
            name = self._safe_name(self._read_json_body().get("name"))
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": str(e)})
            return
        if not os.path.isfile(os.path.join(DATA_DIR, name)):
            self._send_json(404, {"error": f"{name} not found in data/."})
            return
        self._remember(name)
        self._send_json(200, {"name": name})

    def _list_inputs(self):
        if not SAVE_INPUTS_ENABLED:
            self._send_json(200, {"enabled": False, "files": []})
            return
        files = []
        if os.path.isdir(DATA_DIR):
            for entry in os.scandir(DATA_DIR):
                if entry.is_file() and entry.name.lower().endswith(".json") and not entry.name.startswith("."):
                    st = entry.stat()
                    files.append({
                        "name": entry.name,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                        "size": st.st_size,
                    })
        files.sort(key=lambda f: f["modified"], reverse=True)
        self._send_json(200, {"enabled": True, "folder": DATA_DIR, "files": files})

    def _load_input(self, raw_name):
        if not SAVE_INPUTS_ENABLED:
            self._send_json(403, {"error": "Saved inputs are only available when running locally."})
            return
        try:
            name = self._safe_name(unquote(raw_name))
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        path = os.path.join(DATA_DIR, name)
        if not os.path.isfile(path):
            self._send_json(404, {"error": f"{name} not found in data/."})
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._remember(name)
            self._send_json(200, {"name": name, "data": data})
        except json.JSONDecodeError:
            self._send_json(400, {"error": f"{name} is not valid JSON."})

    def _save_input(self):
        if not SAVE_INPUTS_ENABLED:
            self._send_json(403, {"error": "Saving to the server is only available when running locally."})
            return
        try:
            body = self._read_json_body()
            name = self._safe_name(body.get("name"))
            payload = body.get("data")
            if not isinstance(payload, dict):
                raise ValueError("Nothing to save.")
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": str(e)})
            return
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, name)
        if os.path.exists(path) and not body.get("overwrite"):
            self._send_json(409, {"error": f"{name} already exists.", "exists": True, "name": name})
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        self._remember(name)
        self._send_json(200, {"saved": True, "name": name, "path": path})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/inputs":
            self._list_inputs()
        elif path == "/api/inputs/last":
            self._last_input()
        elif path.startswith("/api/inputs/"):
            self._load_input(path[len("/api/inputs/"):])
        else:
            self._serve_static()

    def do_HEAD(self):
        self._serve_static(head_only=True)

    def do_POST(self):
        if self.path == "/api/inputs":
            self._save_input()
            return
        if self.path == "/api/inputs/last":
            self._set_last_input()
            return
        if self.path != "/api/calculate":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"error": "Invalid request body."})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object.")
            self._send_json(200, perform_calculations(data))
        except (ValueError, TypeError) as e:
            self._send_json(400, {"error": str(e)})
        except Exception:
            self._send_json(500, {"error": "Internal error during calculation."})


def run():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.ThreadingTCPServer.allow_reuse_address = True

    with socketserver.ThreadingTCPServer(("", PORT), TaxEstimatorHandler) as httpd:
        print("=====================================================")
        print(" Tax & IRMAA Estimator Server active at:")
        print(f" http://localhost:{PORT}")
        print(" Loaded tax parameters from tax_data.json")
        print(f" Saved inputs: {DATA_DIR if SAVE_INPUTS_ENABLED else 'disabled'}")
        print(" Press Ctrl+C to terminate.")
        print("=====================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            sys.exit(0)


if __name__ == "__main__":
    run()
