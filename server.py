#!/usr/bin/env python3
"""
Multi-State & Multi-Filing Status Tax & IRMAA Estimator Server.
Data loaded dynamically from tax_data.json
"""

import http.server
import socketserver
import json
import math
import os
import sys

PORT = int(os.environ.get("PORT", 8001))

def load_tax_data(file_path="tax_data.json"):
    """Loads tax configuration from an external JSON file and replaces nulls with infinity."""
    if not os.path.exists(file_path):
        print(f"Error: Could not find '{file_path}'. Please place it in the same directory.")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Convert JS 'null' limits to Python float('inf')
    def process_brackets(bracket_list):
        processed = []
        for limit, rate in bracket_list:
            processed.append((float('inf') if limit is None else float(limit), rate))
        return processed

    # Parse Ordinary & Preferential Brackets
    for year in data.get("FED_ORDINARY", {}):
        for status in data["FED_ORDINARY"][year]:
            data["FED_ORDINARY"][year][status] = process_brackets(data["FED_ORDINARY"][year][status])

    for year in data.get("FED_PREFERENTIAL", {}):
        for status in data["FED_PREFERENTIAL"][year]:
            data["FED_PREFERENTIAL"][year][status] = process_brackets(data["FED_PREFERENTIAL"][year][status])

    # Parse IRMAA Upper Limits
    for year in data.get("IRMAA_DATA", {}):
        for status in data["IRMAA_DATA"][year]:
            for tier in data["IRMAA_DATA"][year][status]:
                if tier["limit"] is None:
                    tier["limit"] = float('inf')

        # Map MFS/HOH to Single/MFJ tables if omitted
        data["IRMAA_DATA"][year]["MFS"] = data["IRMAA_DATA"][year].get("MFS", data["IRMAA_DATA"][year]["SINGLE"])
        data["IRMAA_DATA"][year]["HOH"] = data["IRMAA_DATA"][year].get("HOH", data["IRMAA_DATA"][year]["SINGLE"])

    # Parse State Brackets
    for state, info in data.get("STATE_TAX_DATA", {}).items():
        if "brackets" in info:
            info["brackets"] = process_brackets(info["brackets"])
        if "nyc" in info:
            info["nyc"] = process_brackets(info["nyc"])

    return data

# Load tax tables dynamically
TAX_CONFIG = load_tax_data()
FED_STANDARD_DEDUCTION = TAX_CONFIG["FED_STANDARD_DEDUCTION"]
NIIT_THRESHOLDS = TAX_CONFIG["NIIT_THRESHOLDS"]
FED_ORDINARY = TAX_CONFIG["FED_ORDINARY"]
FED_PREFERENTIAL = TAX_CONFIG["FED_PREFERENTIAL"]
IRMAA_DATA = TAX_CONFIG["IRMAA_DATA"]
STATE_TAX_DATA = TAX_CONFIG["STATE_TAX_DATA"]


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
        print(f" Loaded tax parameters from tax_data.json")
        print(f" Press Ctrl+C to terminate.")
        print(f"=====================================================")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            sys.exit(0)


if __name__ == "__main__":
    run()