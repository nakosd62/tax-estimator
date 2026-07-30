#!/usr/bin/env python3
"""
Multi-State & Multi-Filing Status Tax & IRMAA Estimator Server.
"""

import http.server
import socketserver
import json
import math
import os
import sys

PORT = int(os.environ.get("PORT", 8001))

# Federal Standard Deductions by Year & Filing Status
FED_STANDARD_DEDUCTION = {
    "2025": {"MFJ": 31500.0, "SINGLE": 15750.0, "MFS": 15750.0, "HOH": 23600.0},
    "2026": {"MFJ": 32200.0, "SINGLE": 16100.0, "MFS": 16100.0, "HOH": 24150.0},
    "2027": {"MFJ": 33000.0, "SINGLE": 16500.0, "MFS": 16500.0, "HOH": 24750.0},
}

# Federal NIIT Thresholds by Filing Status
NIIT_THRESHOLDS = {
    "MFJ": 250000.0,
    "SINGLE": 200000.0,
    "MFS": 125000.0,
    "HOH": 200000.0
}

# Federal Ordinary Brackets
FED_ORDINARY = {
    "2025": {
        "MFJ": [
            (23850.0, 0.10), (96950.0, 0.12), (206700.0, 0.22),
            (394600.0, 0.24), (501050.0, 0.32), (751600.0, 0.35), (float('inf'), 0.37)
        ],
        "SINGLE": [
            (11925.0, 0.10), (48475.0, 0.12), (103350.0, 0.22),
            (197300.0, 0.24), (250525.0, 0.32), (626350.0, 0.35), (float('inf'), 0.37)
        ],
        "MFS": [
            (11925.0, 0.10), (48475.0, 0.12), (103350.0, 0.22),
            (197300.0, 0.24), (250525.0, 0.32), (375800.0, 0.35), (float('inf'), 0.37)
        ],
        "HOH": [
            (17000.0, 0.10), (64850.0, 0.12), (103350.0, 0.22),
            (197300.0, 0.24), (250500.0, 0.32), (626350.0, 0.35), (float('inf'), 0.37)
        ]
    },
    "2026": {
        "MFJ": [
            (24800.0, 0.10), (100800.0, 0.12), (211400.0, 0.22),
            (403550.0, 0.24), (512450.0, 0.32), (768700.0, 0.35), (float('inf'), 0.37)
        ],
        "SINGLE": [
            (12400.0, 0.10), (50400.0, 0.12), (105700.0, 0.22),
            (201775.0, 0.24), (256225.0, 0.32), (609350.0, 0.35), (float('inf'), 0.37)
        ],
        "MFS": [
            (12400.0, 0.10), (50400.0, 0.12), (105700.0, 0.22),
            (201775.0, 0.24), (256225.0, 0.32), (384350.0, 0.35), (float('inf'), 0.37)
        ],
        "HOH": [
            (17700.0, 0.10), (67450.0, 0.12), (105700.0, 0.22),
            (201750.0, 0.24), (256200.0, 0.32), (609350.0, 0.35), (float('inf'), 0.37)
        ]
    },
    "2027": {
        "MFJ": [
            (25400.0, 0.10), (103300.0, 0.12), (216700.0, 0.22),
            (413650.0, 0.24), (525250.0, 0.32), (787900.0, 0.35), (float('inf'), 0.37)
        ],
        "SINGLE": [
            (12700.0, 0.10), (51650.0, 0.12), (108350.0, 0.22),
            (206800.0, 0.24), (262600.0, 0.32), (624550.0, 0.35), (float('inf'), 0.37)
        ],
        "MFS": [
            (12700.0, 0.10), (51650.0, 0.12), (108350.0, 0.22),
            (206800.0, 0.24), (262600.0, 0.32), (393950.0, 0.35), (float('inf'), 0.37)
        ],
        "HOH": [
            (18150.0, 0.10), (69150.0, 0.12), (108350.0, 0.22),
            (206800.0, 0.24), (262600.0, 0.32), (624550.0, 0.35), (float('inf'), 0.37)
        ]
    }
}

# Federal Preferential Brackets
FED_PREFERENTIAL = {
    "2025": {
        "MFJ": [(96700.0, 0.00), (600050.0, 0.15), (float('inf'), 0.20)],
        "SINGLE": [(48350.0, 0.00), (533400.0, 0.15), (float('inf'), 0.20)],
        "MFS": [(48350.0, 0.00), (300025.0, 0.15), (float('inf'), 0.20)],
        "HOH": [(64750.0, 0.00), (566700.0, 0.15), (float('inf'), 0.20)]
    },
    "2026": {
        "MFJ": [(98900.0, 0.00), (613700.0, 0.15), (float('inf'), 0.20)],
        "SINGLE": [(49450.0, 0.00), (545550.0, 0.15), (float('inf'), 0.20)],
        "MFS": [(49450.0, 0.00), (306850.0, 0.15), (float('inf'), 0.20)],
        "HOH": [(66250.0, 0.00), (579600.0, 0.15), (float('inf'), 0.20)]
    },
    "2027": {
        "MFJ": [(101350.0, 0.00), (629050.0, 0.15), (float('inf'), 0.20)],
        "SINGLE": [(50650.0, 0.00), (559200.0, 0.15), (float('inf'), 0.20)],
        "MFS": [(50650.0, 0.00), (314500.0, 0.15), (float('inf'), 0.20)],
        "HOH": [(67900.0, 0.00), (594100.0, 0.15), (float('inf'), 0.20)]
    }
}

# Medicare IRMAA Surcharges by Status
IRMAA_DATA = {
    "2025": {
        "MFJ": [
            {"limit": 212000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 266000.0, "tier": 2, "part_b": 74.00, "part_d": 13.70},
            {"limit": 334000.0, "tier": 3, "part_b": 185.00, "part_d": 35.30},
            {"limit": 400000.0, "tier": 4, "part_b": 295.90, "part_d": 57.00},
            {"limit": 750000.0, "tier": 5, "part_b": 406.90, "part_d": 78.60},
            {"limit": float('inf'), "tier": 6, "part_b": 443.90, "part_d": 85.80}
        ],
        "SINGLE": [
            {"limit": 106000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 133000.0, "tier": 2, "part_b": 74.00, "part_d": 13.70},
            {"limit": 167000.0, "tier": 3, "part_b": 185.00, "part_d": 35.30},
            {"limit": 200000.0, "tier": 4, "part_b": 295.90, "part_d": 57.00},
            {"limit": 500000.0, "tier": 5, "part_b": 406.90, "part_d": 78.60},
            {"limit": float('inf'), "tier": 6, "part_b": 443.90, "part_d": 85.80}
        ]
    },
    "2026": {
        "MFJ": [
            {"limit": 218000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 274000.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 342000.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 410000.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 750000.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ],
        "SINGLE": [
            {"limit": 109000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 137000.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 171000.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 205000.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 500000.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ]
    },
    "2027": {
        "MFJ": [
            {"limit": 223450.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 280850.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 350550.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 420250.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 768750.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ],
        "SINGLE": [
            {"limit": 111725.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 140425.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 175275.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 210125.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 500000.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ]
    }
}
# Map MFS/HOH to Single/MFJ IRMAA tables for simplicity
IRMAA_DATA["2025"]["MFS"] = IRMAA_DATA["2025"]["SINGLE"]
IRMAA_DATA["2025"]["HOH"] = IRMAA_DATA["2025"]["SINGLE"]
IRMAA_DATA["2026"]["MFS"] = IRMAA_DATA["2026"]["SINGLE"]
IRMAA_DATA["2026"]["HOH"] = IRMAA_DATA["2026"]["SINGLE"]
IRMAA_DATA["2027"]["MFS"] = IRMAA_DATA["2027"]["SINGLE"]
IRMAA_DATA["2027"]["HOH"] = IRMAA_DATA["2027"]["SINGLE"]

# State Tax Data
STATE_TAX_DATA = {
    # No Income Tax States
    "AK": {"deduction": 0.0, "brackets": []},
    "FL": {"deduction": 0.0, "brackets": []},
    "NV": {"deduction": 0.0, "brackets": []},
    "SD": {"deduction": 0.0, "brackets": []},
    "TN": {"deduction": 0.0, "brackets": []},
    "TX": {"deduction": 0.0, "brackets": []},
    "WA": {"deduction": 0.0, "brackets": []},
    "WY": {"deduction": 0.0, "brackets": []},

    # Flat Tax States
    "AZ": {"deduction": 29200.0, "brackets": [(float('inf'), 0.0250)]},
    "CO": {"deduction": 29200.0, "brackets": [(float('inf'), 0.0440)]},
    "GA": {"deduction": 24000.0, "brackets": [(float('inf'), 0.0549)]},
    "ID": {"deduction": 29200.0, "brackets": [(float('inf'), 0.05695)]},
    "IL": {"deduction": 4850.0, "brackets": [(float('inf'), 0.0495)]},
    "IN": {"deduction": 2000.0, "brackets": [(float('inf'), 0.0305)]},
    "IA": {"deduction": 0.0, "brackets": [(float('inf'), 0.0380)]},
    "KY": {"deduction": 3160.0, "brackets": [(float('inf'), 0.0400)]},
    "MI": {"deduction": 11200.0, "brackets": [(float('inf'), 0.0425)]},
    "NC": {"deduction": 25500.0, "brackets": [(float('inf'), 0.0450)]},
    "ND": {"deduction": 29200.0, "brackets": [(113800.0, 0.00), (float('inf'), 0.0225)]},
    "PA": {"deduction": 0.0, "brackets": [(float('inf'), 0.0307)]},
    "UT": {"deduction": 0.0, "brackets": [(float('inf'), 0.0455)]},

    # Progressive Tax States
    "CA": {
        "deduction": 10726.0,
        "brackets": [
            (21000.0, 0.0100), (49800.0, 0.0200), (78600.0, 0.0400),
            (109000.0, 0.0600), (137800.0, 0.0800), (703800.0, 0.0930),
            (844600.0, 0.1030), (1000000.0, 0.1130), (float('inf'), 0.1230)
        ]
    },
    "MA": {
        "deduction": 8800.0,
        "brackets": [(1000000.0, 0.0500), (float('inf'), 0.0900)]
    },
    "NJ": {
        "deduction": 2000.0,
        "brackets": [
            (20000.0, 0.0140), (50000.0, 0.0175), (70000.0, 0.0245),
            (80000.0, 0.0350), (150000.0, 0.05525), (500000.0, 0.0637),
            (1000000.0, 0.0897), (float('inf'), 0.1075)
        ]
    },
    "NY": {
        "deduction": 16050.0,
        "brackets": [
            (17150.0, 0.0400), (23600.0, 0.0450), (27900.0, 0.0525),
            (161550.0, 0.0550), (323200.0, 0.0600), (2155350.0, 0.0685),
            (5000000.0, 0.0965), (25000000.0, 0.1030), (float('inf'), 0.1090)
        ],
        "nyc": [
            (21600.0, 0.03078), (45000.0, 0.03762), (90000.0, 0.03819), (float('inf'), 0.03876)
        ]
    },
    "OH": {
        "deduction": 0.0,
        "brackets": [(26050.0, 0.00), (100000.0, 0.0275), (float('inf'), 0.0350)]
    },
    "VA": {
        "deduction": 9000.0,
        "brackets": [(3000.0, 0.0200), (5000.0, 0.0300), (17000.0, 0.0500), (float('inf'), 0.0575)]
    },
    "WI": {
        "deduction": 23800.0,
        "brackets": [(18800.0, 0.0350), (37600.0, 0.0440), (413100.0, 0.0530), (float('inf'), 0.0765)]
    }
}


def json_safe(value):
    if isinstance(value, float) and math.isinf(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def calculate_progressive_tax(income: float, brackets: list) -> tuple[float, list[dict]]:
    if income <= 0 or not brackets:
        return 0.0, []

    tax = 0.0
    previous_limit = 0.0
    breakdown = []

    for limit, rate in brackets:
        if income > limit:
            bracket_income = limit - previous_limit
            bracket_tax = bracket_income * rate
            tax += bracket_tax
            breakdown.append({
                "range": f"${previous_limit:,.0f} - ${limit:,.0f}",
                "taxable": bracket_income,
                "rate": rate,
                "tax": bracket_tax
            })
            previous_limit = limit
        else:
            bracket_income = income - previous_limit
            bracket_tax = bracket_income * rate
            tax += bracket_tax
            breakdown.append({
                "range": f"${previous_limit:,.0f} - {f'${limit:,.0f}' if limit != float('inf') else 'Over'}",
                "taxable": bracket_income,
                "rate": rate,
                "tax": bracket_tax
            })
            break

    return tax, breakdown


def calculate_preferential_tax(ord_income_taxable: float, cap_gains_taxable: float, pref_brackets: list) -> tuple[float, list[dict]]:
    if cap_gains_taxable <= 0:
        return 0.0, []

    tax = 0.0
    previous_limit = 0.0
    breakdown = []

    for limit, rate in pref_brackets:
        bracket_start = max(previous_limit, ord_income_taxable)
        bracket_end = min(limit, ord_income_taxable + cap_gains_taxable)

        if bracket_end > bracket_start:
            taxable_in_bracket = bracket_end - bracket_start
            bracket_tax = taxable_in_bracket * rate
            tax += bracket_tax
            breakdown.append({
                "range": f"${previous_limit:,.0f} - {f'${limit:,.0f}' if limit != float('inf') else 'Over'}",
                "taxable": taxable_in_bracket,
                "rate": rate,
                "tax": bracket_tax
            })
        previous_limit = limit

    return tax, breakdown


def get_irmaa_tier(magi: float, irmaa_tiers: list) -> dict:
    for tier in irmaa_tiers:
        if magi <= tier["limit"]:
            return tier
    return irmaa_tiers[-1]


def perform_calculations(data: dict) -> dict:
    year = str(data.get("year", "2026"))
    if year not in FED_ORDINARY:
        year = "2026"

    state_code = str(data.get("state", "NY")).upper()
    if state_code not in STATE_TAX_DATA:
        state_code = "NY"

    filing_status = str(data.get("filing_status", "MFJ")).upper()
    if filing_status not in ["MFJ", "SINGLE", "MFS", "HOH"]:
        filing_status = "MFJ"

    fed_ord_brackets = FED_ORDINARY[year].get(filing_status, FED_ORDINARY[year]["MFJ"])
    fed_pref_brackets = FED_PREFERENTIAL[year].get(filing_status, FED_PREFERENTIAL[year]["MFJ"])
    irmaa_tiers = IRMAA_DATA[year].get(filing_status, IRMAA_DATA[year]["MFJ"])
    default_fed_deduction = FED_STANDARD_DEDUCTION[year].get(filing_status, 32200.0)
    niit_threshold = NIIT_THRESHOLDS.get(filing_status, 250000.0)

    state_params = STATE_TAX_DATA[state_code]

    # Parse inputs
    wages = float(data.get("wages", 0))
    pension = float(data.get("pension", 0))
    ira_dist = float(data.get("ira_dist", 0))
    roth_conv = float(data.get("roth_conv", 0))
    interest = float(data.get("interest", 0))
    ord_dividends = float(data.get("ordinary_dividends", 0))
    q_dividends = float(data.get("qualified_dividends", 0))
    tax_exempt = float(data.get("tax_exempt", 0))
    
    fed_deductions = float(data.get("itemized_deductions", default_fed_deduction))
    state_deductions = state_params["deduction"]

    # Derived
    non_qualified_dividends = max(0.0, ord_dividends - q_dividends)
    
    # 1. AGI
    ordinary_income = wages + pension + ira_dist + roth_conv + interest + non_qualified_dividends
    agi = ordinary_income + q_dividends

    # 2. Taxable Income
    fed_taxable = max(0.0, agi - fed_deductions)
    state_taxable = max(0.0, agi - state_deductions)

    # 3. Federal Tax
    pref_portion = min(q_dividends, fed_taxable)
    ordinary_portion = max(0.0, fed_taxable - pref_portion)

    fed_ord_tax, fed_ord_breakdown = calculate_progressive_tax(ordinary_portion, fed_ord_brackets)
    fed_pref_tax, fed_pref_breakdown = calculate_preferential_tax(ordinary_portion, pref_portion, fed_pref_brackets)
    fed_tax = fed_ord_tax + fed_pref_tax

    # 4. NIIT
    nii = interest + ord_dividends
    niit = 0.038 * min(nii, max(0.0, agi - niit_threshold))

    # 5. State Tax
    state_tax, state_breakdown = calculate_progressive_tax(state_taxable, state_params["brackets"])

    # 6. Local Tax (NY / NYC)
    nyc_tax, nyc_breakdown = 0.0, []
    if state_code == "NY":
        nyc_tax, nyc_breakdown = calculate_progressive_tax(state_taxable, state_params.get("nyc", []))

    # 7. IRMAA
    irmaa_magi = agi + tax_exempt
    irmaa = get_irmaa_tier(irmaa_magi, irmaa_tiers)

    total_tax = fed_tax + niit + state_tax + nyc_tax
    net_income = agi - total_tax

    return {
        "year": year,
        "state": state_code,
        "filing_status": filing_status,
        "agi": agi,
        "non_qualified_dividends": non_qualified_dividends,
        "irmaa_magi": irmaa_magi,
        "fed_deduction": fed_deductions,
        "state_deduction": state_deductions,
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
        "irmaa_tier": irmaa["tier"],
        "irmaa_limit": irmaa["limit"],
        "irmaa_limits": [t["limit"] for t in irmaa_tiers],
        "irmaa_part_b": irmaa["part_b"],
        "irmaa_part_d": irmaa["part_d"],
        "total_tax": total_tax,
        "net_income": net_income,
        "effective_rate": total_tax / agi if agi > 0 else 0.0
    }


class TaxEstimatorHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.path == "/api/calculate":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                result = perform_calculations(data)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(json_safe(result)).encode('utf-8'))
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()


def run():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    socketserver.TCPServer.allow_reuse_address = True
    
    with socketserver.TCPServer(("", PORT), TaxEstimatorHandler) as httpd:
        print(f"=====================================================")
        print(f" Tax & IRMAA Estimator Server active at:")
        print(f" http://localhost:{PORT}")
        print(f" Press Ctrl+C to terminate.")
        print(f"=====================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            sys.exit(0)


if __name__ == "__main__":
    run()