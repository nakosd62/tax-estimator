document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('calculator-form');
    const yearSelect = document.getElementById('year');
    const stateInput = document.getElementById('state');
    const filingStatusSelect = document.getElementById('filing_status');
    const filers65Select = document.getElementById('filers_65');
    const filers59Select = document.getElementById('filers_59');
    const numberInputs = form.querySelectorAll('input[type="number"]');
    // Plan G premium lives in the IRMAA card, outside <form>, since it's not a tax input —
    // just a figure displayed alongside the Medicare premiums it doesn't affect the calc.
    const planGInput = document.getElementById('plan_g_premium');

    // Inputs where a blank value means "use the default / not provided" rather than zero.
    const BLANK_MEANS_DEFAULT = new Set(['salt_income', 'prior_agi', 'prior_fed_tax', 'prior_ny_tax']);
    const STATE_NAMES = { NY: 'New York' };

    const $ = (id) => document.getElementById(id);
    const setText = (id, text) => { const el = $(id); if (el) el.innerText = text; };

    const formatCurrency = (val, digits = 2) =>
        new Intl.NumberFormat('en-US', {
            style: 'currency', currency: 'USD', maximumFractionDigits: digits
        }).format(val || 0);
    const money0 = (val) => formatCurrency(val, 0);
    const moneyK = (val) => `$${Math.round((val || 0) / 1000)}K`;
    const signed0 = (val) => (val < 0 ? `(${money0(-val)})` : money0(val));

    const formatPercent = (val) =>
        new Intl.NumberFormat('en-US', {
            style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2
        }).format(val || 0);

    // ------------------------------------------------------------------
    // Form helpers
    // ------------------------------------------------------------------

    // "Both" only makes sense on a joint return.
    const syncJointOnlyOptions = () => {
        const joint = filingStatusSelect.value === 'MFJ';
        [filers65Select, filers59Select].forEach(sel => {
            const both = sel.querySelector('option[data-joint-only]');
            if (!both) return;
            both.disabled = !joint;
            if (!joint && sel.value === '2') sel.value = '1';
        });
    };

    // ------------------------------------------------------------------
    // K-1 rows (repeatable — each is its own object, not a named field)
    // ------------------------------------------------------------------
    const k1RowsEl = $('k1-rows');
    const k1Template = $('k1-row-template');
    const K1_NUMERIC_FIELDS = [
        'ordinary_income', 'net_rental', 'interest', 'ord_dividends',
        'qual_dividends', 'royalties', 'st_cap_gain', 'lt_cap_gain', 'portfolio_deductions',
    ];

    const addK1Row = (values = {}) => {
        const node = k1Template.content.firstElementChild.cloneNode(true);
        node.querySelector('[data-k1-field="label"]').value = values.label || '';
        K1_NUMERIC_FIELDS.forEach(f => {
            const el = node.querySelector(`[data-k1-field="${f}"]`);
            const v = values[f];
            el.value = (v === null || v === undefined || v === '') ? '0' : String(v);
        });
        node.querySelector('[data-k1-field="suspended"]').checked = Boolean(values.suspended);
        k1RowsEl.appendChild(node);
    };

    const readK1Rows = () => Array.from(k1RowsEl.querySelectorAll('.k1-row')).map(row => {
        const entry = { label: row.querySelector('[data-k1-field="label"]').value || '' };
        K1_NUMERIC_FIELDS.forEach(f => {
            const el = row.querySelector(`[data-k1-field="${f}"]`);
            entry[f] = el.value === '' ? 0 : parseFloat(el.value);
        });
        entry.suspended = row.querySelector('[data-k1-field="suspended"]').checked;
        return entry;
    });

    const writeK1Rows = (list) => {
        k1RowsEl.innerHTML = '';
        (Array.isArray(list) ? list : []).forEach(addK1Row);
    };

    const collectFormData = () => {
        const formData = {
            year: yearSelect.value,
            state: stateInput ? stateInput.value : 'NY',
            filing_status: filingStatusSelect.value,
            filers_65: parseInt(filers65Select.value, 10) || 0,
            filers_59: parseInt(filers59Select.value, 10) || 0,
        };
        numberInputs.forEach(input => {
            if (input.value === '') {
                formData[input.name] = BLANK_MEANS_DEFAULT.has(input.name) ? null : 0;
            } else {
                formData[input.name] = parseFloat(input.value);
            }
        });
        formData.k1s = readK1Rows();
        formData.plan_g_premium = planGInput && planGInput.value !== '' ? parseFloat(planGInput.value) : 0;
        return formData;
    };

    // "Part of" fields can't exceed their parent field.
    const SUBSET_RULES = [
        ['qualified_dividends', 'ordinary_dividends', 'Qualified dividends cannot exceed total ordinary dividends.'],
        ['sec199a_dividends', 'ordinary_dividends', 'Section 199A (REIT) dividends are part of ordinary dividends, so they cannot exceed them.'],
        ['treasury_interest', ['interest', 'ordinary_dividends'], 'U.S. government income is part of taxable interest or dividends, so it cannot exceed their total.'],
        ['oos_muni_interest', 'tax_exempt', 'Out-of-state muni interest is part of tax-exempt interest, so it cannot exceed it.'],
    ];
    const validateSubsets = (formData) => {
        let ok = true;
        SUBSET_RULES.forEach(([child, parent, msg]) => {
            const el = $(child);
            if (!el) return;
            const limit = (Array.isArray(parent) ? parent : [parent]).reduce((t, k) => t + (formData[k] || 0), 0);
            if ((formData[child] || 0) > limit) {
                el.setCustomValidity(msg);
                if (ok) el.reportValidity();
                ok = false;
            } else {
                el.setCustomValidity('');
            }
        });
        return ok;
    };

    // ------------------------------------------------------------------
    // Calculation round-trip
    // ------------------------------------------------------------------
    let requestSeq = 0;

    const triggerCalculation = async () => {
        syncJointOnlyOptions();
        const formData = collectFormData();
        if (!validateSubsets(formData)) return;

        const seq = ++requestSeq;
        try {
            const response = await fetch('/api/calculate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(formData)
            });
            const raw = await response.text();
            if (seq !== requestSeq) return; // a newer request superseded this one

            let data;
            try {
                data = JSON.parse(raw);
            } catch (parseError) {
                showError('Could not parse server response.');
                console.error('JSON parse error:', parseError, raw.slice(0, 200));
                return;
            }
            if (!response.ok) {
                showError(data.error || 'Calculation failed.');
                return;
            }
            hideError();
            updateUI(data, formData);
        } catch (e) {
            showError('Could not reach calculator server.');
            console.error('Error during calculation:', e);
        }
    };

    const showError = (message) => {
        const status = $('io-status');
        if (status) status.textContent = '';
        const banner = $('error-banner');
        if (banner) {
            banner.textContent = message;
            banner.hidden = false;
        }
    };
    const hideError = () => {
        const banner = $('error-banner');
        if (banner) banner.hidden = true;
    };

    // ------------------------------------------------------------------
    // Help popups: every .input-help[data-help-for] is shown as a hover/focus
    // popup on the label or heading it points to, via a small "i" icon.
    // ------------------------------------------------------------------
    const helpTip = $('help-tip');
    let helpAnchor = null;

    const hideHelp = () => {
        if (!helpTip) return;
        helpTip.hidden = true;
        helpAnchor = null;
    };

    const showHelp = (anchor) => {
        const text = anchor.dataset.helpText;
        if (!helpTip || !text) return;
        helpAnchor = anchor;
        helpTip.textContent = text;
        helpTip.hidden = false;
        const r = anchor.getBoundingClientRect();
        const tw = helpTip.offsetWidth;
        const th = helpTip.offsetHeight;
        let left = Math.min(Math.max(8, r.left), window.innerWidth - tw - 8);
        let top = r.bottom + 6;
        if (top + th > window.innerHeight - 8) top = Math.max(8, r.top - th - 6);
        helpTip.style.left = `${left}px`;
        helpTip.style.top = `${top}px`;
    };

    const syncHelp = () => {
        const byTarget = new Map();
        document.querySelectorAll('.input-help[data-help-for]').forEach(help => {
            const target = document.querySelector(help.dataset.helpFor);
            if (!target) return;
            if (!byTarget.has(target)) byTarget.set(target, []);
            const text = help.textContent.trim();
            if (text) byTarget.get(target).push(text);
        });
        byTarget.forEach((texts, target) => {
            const text = texts.join('\n\n');
            target.dataset.helpText = text;
            let icon = target.querySelector(':scope > .help-icon');
            if (!icon) {
                icon = document.createElement('span');
                icon.className = 'help-icon';
                icon.tabIndex = 0;
                icon.setAttribute('role', 'button');
                icon.textContent = 'i';
                icon.addEventListener('focus', () => showHelp(target));
                icon.addEventListener('blur', hideHelp);
                icon.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); });
                target.appendChild(icon);
                target.classList.add('has-help');
                target.addEventListener('mouseenter', () => showHelp(target));
                target.addEventListener('mouseleave', hideHelp);
            }
            icon.hidden = !text;
            icon.setAttribute('aria-label', text ? `Help: ${text}` : 'Help');
            if (helpAnchor === target) {
                if (text) showHelp(target); else hideHelp();
            }
        });
    };

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideHelp(); });
    window.addEventListener('scroll', hideHelp, { passive: true });

    // ------------------------------------------------------------------
    // Rendering
    // ------------------------------------------------------------------
    const updateHelpText = (data, formData) => {
        // Social Security
        const ss = formData.social_security || 0;
        setText('ss-help', ss > 0
            ? `Federally taxable: ${money0(data.taxable_social_security)} (${formatPercent(data.taxable_social_security / ss)}). Not taxed by NY.`
            : '');

        // NY retirement exclusions
        const parts = [];
        if (data.ny_retirement_exclusion > 0) parts.push(`${money0(data.ny_retirement_exclusion)} excluded (59½+)`);
        setText('ny-exclusion-help', parts.length ? `NY: ${parts.join('; ')}.` : '');

        // SALT source
        setText('salt-help', data.salt_income_auto
            ? `Using NY + NYC withholding and estimates: ${money0(data.salt_income_used)}. Add any prior-year balance you paid this year.`
            : '');

        // Deduction decision lines
        const fi = data.fed_itemized;
        let fedText = `Federal: itemized ${money0(fi.total)} vs standard ${money0(data.fed_standard_deduction)}`;
        if (data.fed_additional_65 > 0) fedText += ` (incl. ${money0(data.fed_additional_65)} age 65+)`;
        fedText += data.fed_deduction_type === 'itemized' ? ' → itemizing.' : ' → standard.';
        if (fi.salt_paid > fi.salt_cap) fedText += ` SALT capped at ${money0(fi.salt_cap)}.`;
        setText('fed-deduction-help', fedText);

        const ny = data.ny_itemized;
        if (ny) {
            setText('ny-deduction-help',
                `NY: itemized ${money0(ny.total)} vs standard ${money0(data.state_standard_deduction)}` +
                (data.state_itemizing ? ' → itemizing.' : ' → standard.'));
        }

        // Foreign tax credit
        const ftcPaid = formData.foreign_tax_paid || 0;
        let ftcText = '';
        if (ftcPaid > 0 || data.ftc_carryforward > 0) {
            ftcText = `Credit applied: ${money0(data.foreign_tax_credit)}` +
                (data.ftc_carryforward > 0 ? ` (incl. ${money0(data.ftc_carryforward)} carried forward)` : '') + '.';
            if (data.form1116_required) {
                ftcText += ` Above ${money0(data.foreign_tax_no_form_limit)} or with a carryforward, Form 1116 applies: limited to ${(data.ftc_ratio * 100).toFixed(1)}% of your regular tax (your foreign-source income ÷ taxable income), i.e. ${money0(data.form1116_limit)}.`;
            }
            if (data.ftc_carryforward_unused > 0) {
                ftcText += ` ${money0(data.ftc_carryforward_unused)} unused — carries forward to next year.`;
            }
        }
        setText('ftc-help', ftcText);

        // Nudge to fill in foreign-source income once Form 1116 actually applies.
        const fsiHelp = (data.form1116_required && (formData.foreign_source_income || 0) === 0)
            ? `Form 1116 applies here but foreign-source income is $0 — the credit above is understated (limited to $0) until you enter it.`
            : '';
        setText('ftc-1116-help', fsiHelp);

        // Capital loss carryforward used this year
        const clSt = formData.cl_carryforward_st || 0;
        const clLt = formData.cl_carryforward_lt || 0;
        let clText = '';
        if (clSt > 0 || clLt > 0) {
            clText = `Applied this year: ${money0(clSt + clLt)} of prior-year capital loss.`;
            if (data.cl_carryforward_remaining > 0) {
                clText += ` About ${money0(data.cl_carryforward_remaining)} still unused — carries forward again (check your own Capital Loss Carryover Worksheet for the exact short-term/long-term split).`;
            }
        }
        setText('cl-carryforward-help', clText);
    };

    const setCell = (id, text, cls = '') => {
        const el = $(id);
        if (!el) return;
        el.textContent = text;
        el.className = cls;
    };

    const renderPayments = (data) => {
        const cols = { fed: data.payments.federal, ny: data.payments.ny };
        Object.entries(cols).forEach(([key, p]) => {
            setCell(`pay-total-${key}`, money0(p.total_tax));
            setCell(`pay-withholding-${key}`, money0(p.withholding));
            setCell(`pay-estimated-${key}`, money0(p.estimated));
            setCell(`pay-balance-${key}`, signed0(p.balance), p.balance > 0 ? 'warn' : 'ok');
            setCell(`pay-required-${key}`, money0(p.safe_harbor.required));
            setCell(`pay-status-${key}`,
                p.safe_harbor.met ? 'Safe harbor met' : `Short ${money0(p.safe_harbor.shortfall)}`,
                p.safe_harbor.met ? 'ok' : 'warn');
        });
        ['total', 'balance'].forEach(k => {
            const row = $(`pay-${k}-fed`);
            if (row) row.parentElement.classList.add('emph');
        });

        const fedBasis = data.payments.federal.safe_harbor.basis;
        const nyBasis = data.payments.ny.safe_harbor.basis;
        setText('pay-help',
            `Safe-harbor minimum is the lesser of 90% of this year's tax or 100% of last year's (110% if prior-year AGI > $150k). ` +
            `Using ${fedBasis} (federal) and ${nyBasis} (NY). Enter prior-year figures to include that test. ` +
            `No penalty if the balance due is under $1,000 (federal) / $300 (NY).`);

        const balance = data.payments.federal.balance + data.payments.ny.balance;
        setText('kpi-balance-label', balance >= 0 ? 'Balance Due' : 'Refund');
        setText('kpi-balance', money0(Math.abs(balance)));
    };

    const renderDeductions = (data) => {
        const fi = data.fed_itemized;
        const ny = data.ny_itemized || {};
        const dash = '—';
        const rows = {
            medical: [fi.medical, ny.medical],
            taxes: [fi.salt, ny.taxes],
            mortgage: [fi.mortgage_interest, ny.mortgage_interest],
            charity: [fi.charity, ny.charity],
            misc: [0, ny.misc],
            limits: [-fi.limitation, -((ny.pease || 0) + (ny.adjustment || 0))],
            itemized: [fi.total, ny.total],
            standard: [data.fed_standard_deduction, data.state_standard_deduction],
        };
        Object.entries(rows).forEach(([k, [f, n]]) => {
            setCell(`ded-${k}-fed`, f ? signed0(f) : dash);
            setCell(`ded-${k}-ny`, n ? signed0(n) : dash);
        });
        const fedUsed = `${money0(data.fed_deduction)} ${data.fed_deduction_type === 'itemized' ? '(itemized)' : '(standard)'}`;
        const nyUsed = `${money0(data.state_deduction)} ${data.state_itemizing ? '(itemized)' : '(standard)'}`;
        setCell('ded-used-fed', fedUsed);
        setCell('ded-used-ny', nyUsed);
        const extra = (data.senior_deduction || 0) + (data.nonitemizer_charity || 0);
        setCell('ded-extra-fed', extra ? money0(extra) : dash);
        setCell('ded-extra-ny', dash);
        setCell('ded-qbi-fed', data.qbi_deduction ? money0(data.qbi_deduction) : dash);
        setCell('ded-qbi-ny', dash);
        ['itemized', 'used'].forEach(k => {
            const cell = $(`ded-${k}-fed`);
            if (cell) cell.parentElement.classList.add('emph');
        });

        // Notes on what shrank the numbers
        const notes = [];
        if (fi.salt_paid > fi.salt_cap) notes.push(`SALT paid ${money0(fi.salt_paid)} capped at ${money0(fi.salt_cap)}`);
        if (fi.charity_floor > 0) notes.push(`charity reduced by 0.5% of AGI (${money0(fi.charity_floor)})`);
        if (ny.income_taxes_removed > 0) notes.push(`NY excludes ${money0(ny.income_taxes_removed)} of income taxes`);
        const taxesRow = $('ded-taxes-fed');
        if (taxesRow) taxesRow.title = notes.join('; ');
    };

    const updateUI = (data, formData) => {
        const stateText = STATE_NAMES[data.state] || data.state;
        setText('liab-state-label', `${stateText} State Income Tax`);
        setText('summary-state-title', `${stateText} State Tax Brackets`);
        setText('irmaa-title', `Medicare IRMAA (${data.irmaa_premium_year})`);

        updateHelpText(data, formData);

        // K-1 summary: what each entity added to this year's return.
        const k1Summary = $('k1-summary');
        if (k1Summary) {
            const entries = data.k1_entries || [];
            if (!entries.length) {
                k1Summary.innerHTML = '';
            } else {
                k1Summary.innerHTML = entries.map(k1 => {
                    const parts = [`net this year: ${signed0(k1.net_this_year)}`];
                    if (k1.suspended && (k1.ordinary_income + k1.net_rental) !== 0) {
                        parts.push(`${signed0(k1.ordinary_income + k1.net_rental)} suspended (not counted)`);
                    }
                    return `<div>${k1.label || 'K-1'} — ${parts.join('; ')}</div>`;
                }).join('');
            }
        }

        // Local (NYC) tax
        const localContainer = $('liab-local-container');
        const localAccordion = $('accordion-local');
        const showLocal = data.state === 'NY' && data.nyc_tax > 0;
        if (localContainer) localContainer.style.display = showLocal ? 'list-item' : 'none';
        if (localAccordion) localAccordion.style.display = showLocal ? 'block' : 'none';
        if (showLocal) {
            setText('liab-nyc-tax', formatCurrency(data.nyc_tax));
            setText('acc-tax-nyc', formatCurrency(data.nyc_tax));
            renderBracketTable('table-nyc', data.nyc_breakdown);
        }

        // KPIs
        setText('kpi-total-tax', formatCurrency(data.total_tax));
        setText('kpi-eff-rate', formatPercent(data.effective_rate));
        setText('kpi-agi', formatCurrency(data.agi));

        // Liabilities
        setText('liab-fed-tax', formatCurrency(data.fed_tax));
        const ftcRow = $('liab-ftc-row');
        if (ftcRow) ftcRow.style.display = data.foreign_tax_credit > 0 ? 'list-item' : 'none';
        setText('liab-ftc', `(${formatCurrency(data.foreign_tax_credit)})`);
        setText('liab-niit-tax', formatCurrency(data.niit));
        setText('liab-fed-total', formatCurrency(data.fed_total_tax));
        setText('liab-nys-tax', formatCurrency(data.nys_tax));
        const schoolRow = $('liab-school-row');
        if (schoolRow) schoolRow.style.display = data.nyc_school_credit > 0 ? 'list-item' : 'none';
        setText('liab-school', `(${formatCurrency(data.nyc_school_credit)})`);
        const schoolLabel = $('liab-school-label');
        if (schoolLabel) schoolLabel.title = `Fixed ${money0(data.nyc_school_credit_fixed)} + rate reduction ${money0(data.nyc_school_credit_rate)} (refundable)`;
        setText('liab-ny-total', formatCurrency(data.ny_total_tax));

        renderPayments(data);
        renderDeductions(data);

        // Medicare Expenses: base premiums, IRMAA surcharges, Medigap, total
        setText('irmaa-title', `Medicare Expenses (${data.irmaa_premium_year})`);
        setText('part-b-base-val', formatCurrency(data.part_b_base));
        setText('part-d-base-val', formatCurrency(data.part_d_base));
        setText('irmaa-part-b-val', formatCurrency(data.irmaa_part_b));
        setText('irmaa-part-d-val', formatCurrency(data.irmaa_part_d));
        setText('irmaa-magi-val', money0(data.irmaa_magi));

        const tierFrom = moneyK(data.irmaa_floor);
        const tierTo = data.irmaa_limit == null ? null : moneyK(data.irmaa_limit);
        setText('irmaa-magi-label', `MAGI (Tier ${data.irmaa_tier}: ${tierFrom}–${tierTo || '∞'})`);

        const planG = formData.plan_g_premium || 0;
        setText('medicare-total-val',
            formatCurrency(data.part_b_base + data.part_d_base + data.irmaa_part_b + data.irmaa_part_d + planG));

        const roomToNext = data.irmaa_limit == null ? null : Math.max(0, data.irmaa_limit - data.irmaa_magi);
        setText('irmaa-help',
            `Medicare premiums in ${data.irmaa_premium_year} are based on ${data.year} MAGI (AGI + tax-exempt interest). ` +
            `Surcharges are per person, on top of the standard premiums. ` +
            `Part D's base is CMS's reference figure for IRMAA/penalty math, not what any specific drug plan actually charges. ` +
            `Plan G is a private Medigap policy with no federal base rate; enter your own premium above.` +
            (roomToNext != null ? ` ${money0(roomToNext)} of room before the next tier.` : '') +
            (data.irmaa_projected || data.base_premiums_projected ? ` ${data.irmaa_premium_year} figures are projected until Medicare publishes them.` : ''));

        // Accordion totals & tables
        setText('acc-tax-fed-ord', formatCurrency(data.fed_ord_tax));
        setText('acc-tax-fed-pref', formatCurrency(data.fed_pref_tax));
        setText('acc-tax-nys', formatCurrency(data.nys_tax));
        renderBracketTable('table-fed-ord', data.fed_ord_breakdown, 'No ordinary taxable income.');
        renderBracketTable('table-fed-pref', data.fed_pref_breakdown, 'No preferential income.');
        renderBracketTable('table-nys', data.nys_breakdown, 'No state income tax.');
        syncHelp();
    };

    const renderBracketTable = (tableId, breakdown, emptyText = 'Nothing to show.') => {
        const tbody = document.querySelector(`#${tableId} tbody`);
        if (!tbody) return;
        tbody.innerHTML = '';

        if (!breakdown || breakdown.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 4;
            td.style.textAlign = 'center';
            td.style.color = 'var(--text-secondary)';
            td.textContent = emptyText;
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        breakdown.forEach(row => {
            const tr = document.createElement('tr');
            [
                row.range,
                row.taxable == null ? '—' : formatCurrency(row.taxable),
                row.rate == null ? '—' : `${(row.rate * 100).toFixed(3).replace(/0$/, '')}%`,
                formatCurrency(row.tax),
            ].forEach(text => {
                const td = document.createElement('td');
                td.textContent = text;
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    };

    // ------------------------------------------------------------------
    // Export / import inputs as JSON
    // ------------------------------------------------------------------
    const FILE_FORMAT = 'tax-estimator-inputs';
    const FILE_VERSION = 1;

    const slug = (text) => text.toLowerCase()
        .replace(/&amp;|&/g, 'and').replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');

    // Every named field in the form, grouped by its section heading.
    const formSections = () => Array.from(form.querySelectorAll('.form-section')).map(section => ({
        key: slug(section.querySelector('h3').textContent),
        fields: Array.from(section.querySelectorAll('input[name], select[name]')).filter(el => el.type !== 'file'),
    }));

    const readField = (el) => {
        if (el.type === 'checkbox') return el.checked;
        if (el.type === 'number') return el.value === '' ? null : parseFloat(el.value);
        if (el.name === 'filers_65' || el.name === 'filers_59') return parseInt(el.value, 10);
        return el.value;
    };

    const buildPayload = () => {
        const inputs = {};
        formSections().forEach(({ key, fields }) => {
            inputs[key] = {};
            fields.forEach(el => { inputs[key][el.name] = readField(el); });
        });
        inputs.k1s = readK1Rows();
        return {
            format: FILE_FORMAT,
            version: FILE_VERSION,
            exported_at: new Date().toISOString(),
            inputs,
        };
    };

    const defaultFileName = () =>
        `tax-inputs-${yearSelect.value}-${filingStatusSelect.value}-${new Date().toISOString().slice(0, 10)}.json`;

    // Fallback when the server can't save (e.g. the public deployment): browser download.
    const downloadInputs = () => {
        const blob = new Blob([JSON.stringify(buildPayload(), null, 2) + '\n'], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = defaultFileName();
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setStatus(`Downloaded ${a.download}`);
    };

    // Accepts the grouped export format, or a flat {field: value} object.
    const flattenInputs = (obj, out = {}) => {
        Object.entries(obj || {}).forEach(([k, v]) => {
            if (v !== null && typeof v === 'object' && !Array.isArray(v)) flattenInputs(v, out);
            else out[k] = v;
        });
        return out;
    };

    const writeField = (el, value) => {
        if (el.type === 'checkbox') {
            el.checked = Boolean(value);
        } else if (el.type === 'number') {
            if (value === null || value === '') { el.value = ''; return true; }
            const n = Number(value);
            if (!Number.isFinite(n)) return false;
            el.value = String(n);
        } else if (el.tagName === 'SELECT') {
            const v = String(value);
            if (!Array.from(el.options).some(o => o.value === v)) return false;
            el.value = v;
        } else {
            el.value = String(value);
        }
        return true;
    };

    // Resets every input to empty/zero, so a loaded file fully defines the form.
    const clearForm = () => {
        formSections().forEach(({ fields }) => fields.forEach(el => {
            if (el.type === 'checkbox') el.checked = false;
            else if (el.type === 'number') el.value = BLANK_MEANS_DEFAULT.has(el.name) ? '' : '0';
            else if (el.tagName === 'SELECT' && el.name.startsWith('filers_')) el.value = '0';
        }));
        writeK1Rows([]);
    };

    const setCurrentFile = (label) => {
        const el = $('current-file');
        if (el) el.textContent = label ? `📄 ${label}` : '';
        currentFileName = label && !label.includes('(') ? label.replace(/^data\//, '') : null;
    };
    let currentFileName = null;

    const importInputs = (text, fileName, { quiet = false } = {}) => {
        let data;
        try {
            data = JSON.parse(text);
        } catch (e) {
            showError(`${fileName} is not valid JSON.`);
            return false;
        }
        if (data && data.format && data.format !== FILE_FORMAT) {
            showError(`${fileName} is not a Tax Estimator inputs file.`);
            return false;
        }
        const values = flattenInputs(data && data.inputs ? data.inputs : data);
        const fields = new Map();
        formSections().forEach(({ fields: fs }) => fs.forEach(el => fields.set(el.name, el)));

        let loaded = 0;
        if ('k1s' in values) {
            writeK1Rows(values.k1s);
            delete values.k1s;
            loaded++;
        }

        // Filing status first, so the "Both" options are enabled before ages are set.
        const order = Object.keys(values).sort((a, b) => (a === 'filing_status' ? -1 : b === 'filing_status' ? 1 : 0));
        const unknown = [];
        const invalid = [];
        order.forEach(name => {
            const el = fields.get(name);
            if (!el) { unknown.push(name); return; }
            if (name === 'filing_status') {
                writeField(el, values[name]);
                syncJointOnlyOptions();
                loaded++;
                return;
            }
            if (writeField(el, values[name])) loaded++; else invalid.push(name);
        });
        const missing = Array.from(fields.keys()).filter(n => !(n in values));

        if (loaded === 0) {
            showError(`${fileName} doesn't contain any Tax Estimator inputs.`);
            return false;
        }
        let msg = `Imported ${loaded} inputs from ${fileName}`;
        const notes = [];
        if (missing.length) notes.push(`${missing.length} not in file (left unchanged)`);
        if (unknown.length) notes.push(`ignored unknown: ${unknown.join(', ')}`);
        if (invalid.length) notes.push(`invalid values skipped: ${invalid.join(', ')}`);
        if (notes.length) msg += ` — ${notes.join('; ')}`;
        if (!quiet || notes.length) setStatus(msg);
        triggerCalculation();
        return true;
    };

    let statusTimer = null;
    const setStatus = (msg) => {
        const el = $('io-status');
        if (!el) return;
        el.textContent = msg;
        el.title = msg;
        clearTimeout(statusTimer);
        statusTimer = setTimeout(() => { el.textContent = ''; }, 8000);
    };

    // --- Saving to / loading from the server's data/ folder (local runs only) ---
    let serverSaves = false;
    let pendingOverwrite = null;

    const api = async (url, options = {}) => {
        const res = await fetch(url, options);
        let body = {};
        try { body = await res.json(); } catch (e) { /* non-JSON */ }
        return { ok: res.ok, status: res.status, body };
    };

    const checkServerSaves = async () => {
        try {
            const { ok, body } = await api('/api/inputs');
            serverSaves = ok && body.enabled === true;
        } catch (e) {
            serverSaves = false;
        }
        const exportBtn = $('btn-export');
        if (exportBtn) exportBtn.title = serverSaves ? 'Save all inputs to the data/ folder' : 'Download all inputs as a JSON file';
    };

    const savePanel = $('save-panel');
    const saveName = $('save-name');

    const openSavePanel = () => {
        pendingOverwrite = null;
        saveName.value = currentFileName || defaultFileName();
        savePanel.hidden = false;
        saveName.focus();
        saveName.setSelectionRange(0, saveName.value.length - '.json'.length);
        $('btn-save-confirm').textContent = 'Save';
    };
    const closeSavePanel = () => {
        savePanel.hidden = true;
        pendingOverwrite = null;
    };

    const saveToServer = async () => {
        const name = saveName.value.trim();
        if (!name) { saveName.focus(); return; }
        const overwrite = pendingOverwrite === name;
        try {
            const { ok, status, body } = await api('/api/inputs', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, data: buildPayload(), overwrite }),
            });
            if (status === 409) {
                pendingOverwrite = name;
                $('btn-save-confirm').textContent = 'Overwrite';
                setStatus(`${body.name} already exists — click Overwrite to replace it.`);
                return;
            }
            if (!ok) {
                showError(body.error || 'Could not save.');
                return;
            }
            closeSavePanel();
            hideError();
            setStatus(`Saved data/${body.name}`);
            setCurrentFile(`data/${body.name}`);
        } catch (e) {
            showError('Could not reach the server to save.');
        }
    };

    const importMenu = $('import-menu');
    const btnImport = $('btn-import');
    const importFile = $('import-file');

    const closeImportMenu = () => {
        importMenu.hidden = true;
        btnImport.setAttribute('aria-expanded', 'false');
    };

    const menuItem = (label, sub, onClick) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'menu-item';
        item.setAttribute('role', 'menuitem');
        const main = document.createElement('span');
        main.textContent = label;
        item.appendChild(main);
        if (sub) {
            const small = document.createElement('span');
            small.className = 'menu-sub';
            small.textContent = sub;
            item.appendChild(small);
        }
        item.addEventListener('click', () => { closeImportMenu(); onClick(); });
        return item;
    };

    const loadFromServer = async (name) => {
        try {
            const { ok, body } = await api(`/api/inputs/${encodeURIComponent(name)}`);
            if (!ok) { showError(body.error || `Could not load ${name}.`); return; }
            clearForm();
            if (importInputs(JSON.stringify(body.data), `data/${body.name}`)) setCurrentFile(`data/${body.name}`);
        } catch (e) {
            showError('Could not reach the server to load the file.');
        }
    };

    const openImportMenu = async () => {
        importMenu.innerHTML = '';
        let files = [];
        try {
            const { ok, body } = await api('/api/inputs');
            if (ok && body.enabled) files = body.files || [];
        } catch (e) { /* offline: fall through to file picker only */ }

        if (files.length) {
            const head = document.createElement('div');
            head.className = 'menu-head';
            head.textContent = 'Saved in data/';
            importMenu.appendChild(head);
            files.forEach(f => {
                const when = new Date(f.modified).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
                importMenu.appendChild(menuItem(f.name, when, () => loadFromServer(f.name)));
            });
            const sep = document.createElement('div');
            sep.className = 'menu-sep';
            importMenu.appendChild(sep);
        } else if (serverSaves) {
            const empty = document.createElement('div');
            empty.className = 'menu-head';
            empty.textContent = 'No saved files in data/ yet';
            importMenu.appendChild(empty);
        }
        importMenu.appendChild(menuItem('From another file…', null, () => importFile.click()));
        importMenu.hidden = false;
        btnImport.setAttribute('aria-expanded', 'true');
        const first = importMenu.querySelector('.menu-item');
        if (first) first.focus();
    };

    const btnExport = $('btn-export');
    if (btnExport) btnExport.addEventListener('click', () => (serverSaves ? openSavePanel() : downloadInputs()));
    $('btn-save-confirm').addEventListener('click', saveToServer);
    $('btn-save-cancel').addEventListener('click', closeSavePanel);
    saveName.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); saveToServer(); }
        if (e.key === 'Escape') closeSavePanel();
    });
    saveName.addEventListener('input', () => {
        pendingOverwrite = null;
        $('btn-save-confirm').textContent = 'Save';
    });

    btnImport.addEventListener('click', (e) => {
        e.stopPropagation();
        if (!importMenu.hidden) closeImportMenu(); else openImportMenu();
    });
    document.addEventListener('click', (e) => {
        if (!importMenu.hidden && !importMenu.contains(e.target)) closeImportMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !importMenu.hidden) { closeImportMenu(); btnImport.focus(); }
    });
    importFile.addEventListener('change', () => {
        const file = importFile.files && importFile.files[0];
        if (!file) return;
        file.text().then(text => {
            if (importInputs(text, file.name) && serverSaves) {
                setCurrentFile(`${file.name} (not in data/)`);
                setStatus(`Imported ${file.name}. Export it to data/ to have it reload next time.`);
            }
        });
        importFile.value = '';   // allow re-importing the same file
    });

    syncHelp();

    form.addEventListener('submit', (e) => e.preventDefault());

    [yearSelect, filingStatusSelect, filers65Select, filers59Select]
        .filter(Boolean)
        .forEach(el => el.addEventListener('change', triggerCalculation));
    numberInputs.forEach(input => input.addEventListener('input', triggerCalculation));
    if (planGInput) planGInput.addEventListener('input', triggerCalculation);

    k1RowsEl.addEventListener('input', triggerCalculation);
    k1RowsEl.addEventListener('change', triggerCalculation);
    k1RowsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-remove-k1');
        if (!btn) return;
        btn.closest('.k1-row').remove();
        triggerCalculation();
    });
    $('btn-add-k1').addEventListener('click', () => { addK1Row(); triggerCalculation(); });

    // On load: restore the last file loaded/saved in data/ (if any); otherwise start from zeros.
    const restoreLastFile = async () => {
        await checkServerSaves();
        if (serverSaves) {
            try {
                const { ok, body } = await api('/api/inputs/last');
                if (ok && body.name && body.data) {
                    clearForm();
                    if (importInputs(JSON.stringify(body.data), `data/${body.name}`, { quiet: true })) {
                        setCurrentFile(`data/${body.name}`);
                        return;
                    }
                }
            } catch (e) { /* fall through to a blank form */ }
        }
        triggerCalculation();
    };
    restoreLastFile();
});
