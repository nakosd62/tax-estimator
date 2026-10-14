# Tax & IRMAA Estimator

A small web app that estimates federal, New York State and NYC income tax, balance due after payments, and the Medicare IRMAA tier, for tax years 2025–2027 and all four filing statuses.

The page is currently NY/NYC-only (the state is fixed to NY). The backend and `tax_data.json` still support 28 states; re-add the state dropdown in `index.html` to expose them.

- **Backend:** `server.py` — dependency-free Python HTTP server. Serves the page and exposes `POST /api/calculate`, plus `GET /api/inputs`, `GET /api/inputs/<name>` and `POST /api/inputs` for saved inputs in `data/`.
- **Tax tables:** `tax_data.json` — every bracket, deduction and threshold lives here; no numbers are hard-coded in Python or JS.
- **Frontend:** `index.html`, `script.js`, `style.css` — recalculates on every input change. Inputs span the full width, grouped as Household, Income (wages & retirement, investments), Itemized deductions, Credits and Payments, and flow into as many columns as fit. Results appear below the inputs. Explanations appear as hover popups on the ⓘ icon next to a label or heading (also reachable with Tab); to add one, put a hidden `<div class="input-help" data-help-for="<selector>">` anywhere in the page — dynamic ones are filled in by `script.js`.

## Saving and loading inputs

**Export** (top right) saves every input as a JSON file in the `data/` folder next to `server.py`. It suggests a name like `tax-inputs-2026-MFJ-2026-09-27.json`, which you can change; saving over an existing file asks you to confirm. **Import…** lists the files in `data/` (newest first) and loads one with a click, or picks any other file from your computer.

The form has no built-in sample values: every amount starts at zero. The app remembers the last file you loaded or saved in `data/` (in `data/.last-used.json`) and reloads it when you open or refresh the page; its name shows in the header. Loading a file from `data/` first clears the form, so fields missing from the file are zero. Unsaved edits are not kept across a reload, so Export to keep them. A file imported from elsewhere on your computer isn't remembered until you Export it into `data/`.

Saving to `data/` is for local use only. It's switched off automatically on Cloud Run (the `K_SERVICE` variable is set there) or when `SAVE_INPUTS=0`; in that case Export downloads the file through the browser and Import only offers "From another file…". `data/` is listed in `.gcloudignore`, so your saved inputs are never uploaded when you deploy.

The file groups inputs by form section, using the same field names the API takes:

```json
{
  "format": "tax-estimator-inputs",
  "version": 1,
  "exported_at": "2026-09-27T14:42:52.157Z",
  "inputs": {
    "household": { "state": "NY", "year": "2026", "filing_status": "MFJ", "filers_65": 2, "filers_59": 2 },
    "income_wages_and_retirement": { "wages": 0, "ira_dist": 120000, "pension": 104000, "...": "..." },
    "deductions_itemized": { "salt_income": null, "...": "..." },
    "payments": { "prior_agi": null, "...": "..." }
  }
}
```

`null` means the field was left blank (for example, `salt_income` then defaults to your NY payments). Import also accepts a flat `{ "field": value }` object and partial files: fields not in the file are left as they are, and unknown fields or invalid values are skipped and listed in the status message.

## Running locally

```bash
python3 server.py            # http://localhost:8001  (PORT env var overrides)
```

## Generating form worksheets

`generate_forms.py` turns a saved inputs file (anything under `data/`) into a set of PDFs that mirror the real federal/NY forms' line numbers and labels — Form 1040, Schedules 1/3/A/B/D, Forms 8995 and 1116, and NY IT-201/IT-196 — populated with the numbers `server.py`'s `perform_calculations()` computes from that file (including any K-1s, folded in the same way the app folds them).

```bash
pip install --break-system-packages reportlab   # once
python3 generate_forms.py                                        # defaults to data/tax-inputs-2025-MFJ-actual.json
python3 generate_forms.py data/some-other-file.json               # or name a file explicitly
```

Output goes to `docs/<year>/generated-forms/` (the year comes from the file's `year` field). These are **not** the actual IRS/NY PDF forms — this script has no access to the real fillable PDFs from irs.gov or tax.ny.gov, so it reconstructs each form's layout by hand; only the dollar amounts are computed, not typed in. Review each PDF's "Notes / assumptions" section before relying on it, and treat it as something to transcribe into the real form or e-file software, not a filing-ready document. A few of the notes (e.g. the Form 1116/foreign-tax-credit and cover-summary comparisons) reference specific facts from the 2025 reconciliation against the actual filed return and would need editing if you point the script at a different year's file.

## Generating a first-cut data file from your tax documents

`parse_docs.py` scans a folder of PDFs (consolidated 1099s, Schedule K-1s, 1099-Rs, co-op real-estate-tax/mortgage-interest letters, estimated-tax payment confirmations, W-2s) and writes a first-cut `tax-estimator-inputs` JSON plus a Markdown report of what it found and what it couldn't. It's heuristic and not filing-accurate — review the report and correct/complete the JSON in the app before relying on it.

```bash
pip install --break-system-packages pytesseract Pillow   # once, for the OCR fallback (also needs poppler-utils and tesseract-ocr installed)
python3 parse_docs.py docs/2025                                              # writes data/tax-inputs-2025-parsed.json + .md report
python3 parse_docs.py docs/2026 --base data/tax-inputs-2025-MFJ-actual.json  # carries over household/priors from last year's file
```

What it recognizes: Fidelity consolidated 1099-DIV/1099-INT/1099-B statements (interest, ordinary/qualified/section 199A dividends, foreign tax paid, capital gain distributions, and short/long-term realized gain or loss); Schedule K-1s (boxes 1, 2, 5, 6a, 6b, 7, 8, 9a, 13 — added as a K-1 row); 1099-Rs (gross distribution, guessing IRA vs. pension from the IRA/SEP/SIMPLE checkbox); co-op real-estate-tax/mortgage-interest letters (reported per-share only, never auto-totaled — multiply by your share count yourself); and IRS/NY estimated-tax payment confirmation pages. Anything else, or anything a recognized extractor couldn't confidently read (including most OCR'd/scanned documents), is listed in the report as needing manual entry rather than guessed at. Numbers found in more than one document (e.g. three brokerage accounts) are summed.

## Deploying

`gcp_deploy.sh` deploys to Cloud Run from source using the `Dockerfile` (Python 3.11 slim, port 8080).

## What's modeled

**Federal**
- Ordinary brackets and 0/15/20% rates on qualified dividends and long-term gains
- Capital gain/loss netting with the $3,000 loss limit ($1,500 MFS)
- Standard deduction including the additional amount for each filer 65+
- Itemized deductions from components, used only when they beat the standard deduction:
  - medical above 7.5% of AGI
  - SALT (state/local income taxes + real estate taxes) capped at $40,000 for 2025, $40,400 for 2026, +1%/yr through 2029 ($10,000 from 2030); half for MFS; the cap shrinks by 30% of MAGI over $500,000 (2025) / $505,000 (2026, +1%/yr) but not below $10,000
  - mortgage interest (enter the deductible amount)
  - charity: from 2026 only the amount above 0.5% of AGI counts, and itemized deductions are cut by 2/37 for taxpayers in the 37% bracket
  - "State & local income taxes paid" left blank defaults to the NY + NYC withholding and estimates entered under Payments
- From 2026, non-itemizers deduct cash gifts to charity up to $1,000 ($2,000 MFJ); gifts of stock, to donor-advised funds, or of property don't qualify
- Section 199A deduction on REIT dividends (1099-DIV box 5): 20% of them, limited to 20% of (taxable income − net capital gain)
- Foreign tax credit (1099-DIV box 7): never offsets NIIT. Under $300/$600 with no carryforward, the credit is limited only to regular tax (the no-Form-1116 election). Above that, or with any carryforward, Form 1116 applies: the credit is capped at regular tax × (foreign-source income ÷ taxable income) — enter your foreign-source income (funds typically report this as a % of dividends, or pull it from a prior Form 1116 line 18) or the credit will be understated. A prior-year unused credit carried forward (Form 1116 Schedule B) can be entered and joins this year's foreign tax paid before the limit applies; any part still unused this year is shown as carrying forward again, but isn't tracked automatically across runs
- Capital loss carryforward: prior-year unused short-term and long-term losses (from your own Capital Loss Carryover Worksheet) can be entered and are netted in with this year's gains/losses, keeping their character, before the $3,000/$1,500 yearly loss limit applies. The amount still unused after that limit is shown as an approximate total to carry forward again -- the real worksheet can split it between short-term and long-term differently
- OBBBA senior deduction: $6,000 per filer 65+, 2025–2028, phased out at 6% of MAGI over $75k ($150k MFJ); not available MFS
- Social Security taxability via the provisional-income worksheet (0 / 50 / 85%). MFS assumes the spouses lived together.
- 3.8% Net Investment Income Tax

**Medicare IRMAA** — tier from MAGI (AGI + tax-exempt interest). Because Medicare looks back two years, the selected tax year's MAGI is applied to the premium year two years later (2026 income → 2028 premiums), and the IRMAA card is labeled with that premium year. Official CMS tables live in `IRMAA_DATA` by premium year; any premium year not yet published is projected from the latest official year using `IRMAA_PROJECTION` (thresholds +2.5%/yr rounded to $1,000, the $500k/$750k top thresholds indexed from 2028, surcharges +6%/yr). Add the official table to `IRMAA_DATA` when CMS publishes it and it replaces the projection automatically. MFS uses its own three-tier table.

**Base Medicare premiums** — alongside the IRMAA surcharges, the card shows the standard Part B premium and the Part D "national base beneficiary premium" for the same premium year, from `BASE_PREMIUMS` (projected the same way as IRMAA when CMS hasn't published a year yet, reusing `IRMAA_PROJECTION.premium_growth`). Part B is the actual flat premium everyone pays; Part D's base is a CMS reference figure used only for IRMAA/late-enrollment-penalty math, not what any specific Part D plan charges — actual Part D cost depends entirely on the plan chosen. Medigap Plan G has no federal base rate at all (private, state/carrier/age/health-rated), so it's a plain user-entered field pre-filled with a rough estimate, not computed from anything.

**States** — 28 states, with brackets, deductions/exemptions and credits by filing status (2026 values; see `_meta` and per-state `notes` in `tax_data.json`). Supports sliding standard deductions (WI), income-tiered exemptions (OH, IL), and phased-out personal credits (UT, CA). CO, ID, IA, ND and AZ use the federal deduction. Social Security is exempt in all modeled states.

**New York / NYC** (NYC residency is assumed when NY is selected)
- NY rates by year: the FY2026 budget cut the five lowest rates 0.1 pt for 2026 and another 0.1 pt from 2027
- Supplemental tax ("tax benefit recapture") for NY AGI above $107,650, per the IT-201 tax computation worksheets: lower-bracket benefits are clawed back over each $50,000 of AGI, and above $25M all income is taxed at the top rate. Implemented from the statute's formula, so it adapts to each year's rates (matches the official 2026 worksheets to within $1–2 of rounding).
- NYC brackets by filing status (NYC has no recapture)
- $20,000 per filer (59½+) exclusion on pension, IRA distributions and Roth conversions
- Interest modifications: U.S. government income (from interest or fund dividends, e.g. a government money market fund's U.S. share) is subtracted; out-of-state municipal interest (part of tax-exempt interest) is added
- NY miscellaneous itemized deductions (investment/account fees, tax-prep fees, K-1 portfolio deductions) above 2% of federal AGI, subject to the Pease limit and NY adjustment
- NYC school tax credit (refundable): $125 MFJ / $63 others if federal AGI minus IRA distributions ≤ $250,000, plus the rate-reduction amount (0.171% / 0.228% of NYC taxable income up to $500,000)
- NY itemized deductions (IT-196), chosen independently of the federal return: 10% medical floor (of federal AGI), no SALT cap, state/local income taxes removed, the pre-2018 Pease limit (federal AGI over $408,850 MFJ in 2025; 2026–27 thresholds projected), then NY's itemized deduction adjustment (up to 25% cut from $100k/$150k/$200k NY AGI, 50% above $525k, charity-only above $1M)

**K-1s (partnerships / S-corps)** — add one row per Schedule K-1 under "Income — K-1s"; each row folds into the plain income/deduction fields above rather than needing its own tax logic:
- Box 1 (ordinary business income/loss) and box 2 (net rental real estate income/loss) join "other income" — unless "Loss suspended" is checked, in which case that row's box 1/2 amount is excluded from this year's totals entirely (the app doesn't track the suspended balance for a future year when it might free up)
- Box 5 (interest), box 6a/6b (ordinary/qualified dividends), box 7 (royalties, into other income), and box 8/9a (short/long-term capital gain or loss) join the matching investment fields
- Box 13 portfolio deductions (e.g. code AE — investment/advisory fees passed through a partnership) join NY miscellaneous deductions
- A running per-K-1 summary under "+ Add K-1" shows each entity's net contribution to this year's return

**Payments & balance due** — federal and NY + NYC withholding and estimated payments give the balance due or refund for each. The underpayment check uses the safe harbor: payments of at least the lesser of 90% of this year's tax or 100% of last year's (110% if prior-year AGI > $150k, $75k MFS); no penalty if the balance due is under $1,000 federal / $300 NY. Prior-year inputs are optional.

## Known limitations

- State tables are 2026 values applied to every year (NY excepted). California brackets and credit and Idaho's zero bracket are 2025 figures pending 2026 publication; Arizona's 2026 deduction is an estimate.
- Not modeled: AMT, self-employment tax, credits other than the foreign tax credit and NYC school tax credit (child, EITC, NY resident credit for other states' tax, etc.), the separate-category (passive vs. general) split and country-by-country detail Form 1116 actually requires (foreign-source income is taken as one number), 25%/28% gain rates, blindness deductions, the 60%-of-AGI charity limit, mortgage-debt limits (enter deductible interest), investment interest, suspended passive-loss carryforward balances (a K-1 row only knows this year's suspended amount, not the running total), and the underpayment penalty amount itself (only whether the safe harbor is met; a single prior-year AGI is used for both federal and NY). Capital loss and foreign tax credit carryforwards are entered by hand each year (see above) rather than tracked automatically from one saved file to the next.
- Whether NY applies the new 0.5% charity floor from 2026 is unconfirmed until the 2026 IT-196 instructions are published; the app assumes it does not.
- 2027 federal figures are projections, and all IRMAA figures for premium years 2027+ are projections until CMS publishes them (usually early November of the prior year).
