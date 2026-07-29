#!/usr/bin/env python3
"""
Local web server for NYC Resident Tax & IRMAA Estimator (Multi-Year: 2025, 2026, 2027 MFJ).
Serves static assets and provides a POST /api/calculate endpoint.
"""

import http.server
import socketserver
import json
import math
import os
import sys

PORT = int(os.environ.get("PORT", 8001))

# Year-by-Year Tax Parametrization
TAX_DATA = {
    "2025": {
        "fed_deduction": 31500.0,
        "state_deduction": 16050.0,
        "fed_ordinary": [
            (23850.0, 0.10),
            (96950.0, 0.12),
            (206700.0, 0.22),
            (394600.0, 0.24),
            (501050.0, 0.32),
            (751600.0, 0.35),
            (float('inf'), 0.37)
        ],
        "fed_preferential": [
            (96700.0, 0.00),
            (600050.0, 0.15),
            (float('inf'), 0.20)
        ],
        "nys": [
            (17150.0, 0.0400),
            (23600.0, 0.0450),
            (27900.0, 0.0525),
            (43000.0, 0.0550),
            (161550.0, 0.0600),
            (215400.0, 0.0625),
            (2155350.0, 0.0685),
            (5388350.0, 0.0965),
            (float('inf'), 0.1090)
        ],
        "nyc": [
            (21600.0, 0.03078),
            (45000.0, 0.03762),
            (90000.0, 0.03819),
            (float('inf'), 0.03876)
        ],
        "irmaa": [
            {"limit": 212000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 266000.0, "tier": 2, "part_b": 74.00, "part_d": 13.70},
            {"limit": 334000.0, "tier": 3, "part_b": 185.00, "part_d": 35.30},
            {"limit": 400000.0, "tier": 4, "part_b": 295.90, "part_d": 57.00},
            {"limit": 750000.0, "tier": 5, "part_b": 406.90, "part_d": 78.60},
            {"limit": float('inf'), "tier": 6, "part_b": 443.90, "part_d": 85.80}
        ]
    },
    "2026": {
        "fed_deduction": 32200.0,
        "state_deduction": 16050.0,
        "fed_ordinary": [
            (24800.0, 0.10),
            (100800.0, 0.12),
            (211400.0, 0.22),
            (403550.0, 0.24),
            (512450.0, 0.32),
            (768700.0, 0.35),
            (float('inf'), 0.37)
        ],
        "fed_preferential": [
            (98900.0, 0.00),
            (613700.0, 0.15),
            (float('inf'), 0.20)
        ],
        "nys": [
            (17150.0, 0.0390),
            (23400.0, 0.0440),
            (27900.0, 0.0515),
            (161550.0, 0.0540),
            (430800.0, 0.0590),
            (2155350.0, 0.0685),
            (5000000.0, 0.0965),
            (25000000.0, 0.1030),
            (float('inf'), 0.1090)
        ],
        "nyc": [
            (21600.0, 0.03078),
            (45000.0, 0.03762),
            (90000.0, 0.03819),
            (float('inf'), 0.03876)
        ],
        "irmaa": [
            {"limit": 218000.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 274000.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 342000.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 410000.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 749999.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ]
    },
    "2027": {
        # Projected 2.5% inflation adjustments over 2026 (rounded)
        "fed_deduction": 33000.0,
        "state_deduction": 16050.0,
        "fed_ordinary": [
            (25400.0, 0.10),
            (103300.0, 0.12),
            (216700.0, 0.22),
            (413650.0, 0.24),
            (525250.0, 0.32),
            (787900.0, 0.35),
            (float('inf'), 0.37)
        ],
        "fed_preferential": [
            (101350.0, 0.00),
            (629050.0, 0.15),
            (float('inf'), 0.20)
        ],
        "nys": [
            (17600.0, 0.0390),
            (24000.0, 0.0440),
            (28600.0, 0.0515),
            (165600.0, 0.0540),
            (441550.0, 0.0590),
            (2209250.0, 0.0685),
            (5125000.0, 0.0965),
            (25625000.0, 0.1030),
            (float('inf'), 0.1090)
        ],
        "nyc": [
            (21600.0, 0.03078),
            (45000.0, 0.03762),
            (90000.0, 0.03819),
            (float('inf'), 0.03876)
        ],
        "irmaa": [
            {"limit": 223450.0, "tier": 1, "part_b": 0.00, "part_d": 0.00},
            {"limit": 280850.0, "tier": 2, "part_b": 81.20, "part_d": 14.50},
            {"limit": 350550.0, "tier": 3, "part_b": 202.90, "part_d": 37.50},
            {"limit": 420250.0, "tier": 4, "part_b": 324.60, "part_d": 60.40},
            {"limit": 768750.0, "tier": 5, "part_b": 446.30, "part_d": 83.30},
            {"limit": float('inf'), "tier": 6, "part_b": 487.00, "part_d": 91.00}
        ]
    }
}


def json_safe(value):
    """Convert values that aren't strict JSON (e.g. Infinity) for browser parsing."""
    if isinstance(value, float) and math.isinf(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value


def calculate_progressive_tax(income: float, brackets: list) -> tuple[float, list[dict]]:
    if income <= 0:
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
    if year not in TAX_DATA:
        year = "2026"

    params = TAX_DATA[year]

    pension = float(data.get("pension", 0))
    ira_dist = float(data.get("ira_dist", 0))
    roth_conv = float(data.get("roth_conv", 0))
    interest = float(data.get("interest", 0))
    ord_dividends = float(data.get("ordinary_dividends", 0))
    q_dividends = float(data.get("qualified_dividends", 0))
    tax_exempt = float(data.get("tax_exempt", 0))
    
    # Defaults depending on selected year
    default_fed_deduction = params["fed_deduction"]
    fed_deductions = float(data.get("itemized_deductions", default_fed_deduction))
    
    state_deductions = data.get("state_itemized_deductions")
    if state_deductions is None or state_deductions == "":
        state_deductions = max(fed_deductions, params["state_deduction"])
    else:
        state_deductions = float(state_deductions)

    # Derived
    non_qualified_dividends = max(0.0, ord_dividends - q_dividends)
    
    # 1. AGI
    ordinary_income = pension + ira_dist + roth_conv + interest + non_qualified_dividends
    agi = ordinary_income + q_dividends

    # 2. Federal Taxable
    fed_taxable = max(0.0, agi - fed_deductions)

    # 3. State Taxable
    nys_taxable = max(0.0, agi - state_deductions)

    # 4. Federal Tax
    pref_portion = min(q_dividends, fed_taxable)
    ordinary_portion = max(0.0, fed_taxable - pref_portion)

    fed_ord_tax, fed_ord_breakdown = calculate_progressive_tax(ordinary_portion, params["fed_ordinary"])
    fed_pref_tax, fed_pref_breakdown = calculate_preferential_tax(ordinary_portion, pref_portion, params["fed_preferential"])
    fed_tax = fed_ord_tax + fed_pref_tax

    # 5. Net Investment Income Tax (NIIT)
    nii = interest + ord_dividends
    niit = 0.038 * min(nii, max(0.0, agi - 250000.0))

    # 6. State & Local
    nys_tax, nys_breakdown = calculate_progressive_tax(nys_taxable, params["nys"])
    nyc_tax, nyc_breakdown = calculate_progressive_tax(nys_taxable, params["nyc"])

    # 7. IRMAA
    irmaa_magi = agi + tax_exempt
    irmaa = get_irmaa_tier(irmaa_magi, params["irmaa"])

    total_tax = fed_tax + niit + nys_tax + nyc_tax
    net_income = agi - total_tax

    # Output formatted IRMAA tiers mapping for UI meter limits
    irmaa_limits = [t["limit"] for t in params["irmaa"]]

    return {
        "year": year,
        "agi": agi,
        "non_qualified_dividends": non_qualified_dividends,
        "irmaa_magi": irmaa_magi,
        "fed_deduction": fed_deductions,
        "state_deduction": state_deductions,
        "fed_taxable": fed_taxable,
        "nys_taxable": nys_taxable,
        "fed_tax": fed_tax,
        "fed_ord_tax": fed_ord_tax,
        "fed_pref_tax": fed_pref_tax,
        "fed_ord_breakdown": fed_ord_breakdown,
        "fed_pref_breakdown": fed_pref_breakdown,
        "niit": niit,
        "nys_tax": nys_tax,
        "nys_breakdown": nys_breakdown,
        "nyc_tax": nyc_tax,
        "nyc_breakdown": nyc_breakdown,
        "irmaa_tier": irmaa["tier"],
        "irmaa_limit": irmaa["limit"],
        "irmaa_limits": irmaa_limits,
        "irmaa_part_b": irmaa["part_b"],
        "irmaa_part_d": irmaa["part_d"],
        "total_tax": total_tax,
        "net_income": net_income,
        "effective_rate": total_tax / agi if agi > 0 else 0.0
    }


class TaxEstimatorHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/api/years":
            payload = {
                year: {
                    "fed_deduction": params["fed_deduction"],
                    "state_deduction": params["state_deduction"],
                    "projected": year == "2027",
                }
                for year, params in sorted(TAX_DATA.items())
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(json_safe({"years": payload, "default": "2026"})).encode("utf-8"))
        else:
            super().do_GET()

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
