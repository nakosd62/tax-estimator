document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('calculator-form');
    const yearSelect = document.getElementById('year');
    const stateSelect = document.getElementById('state');
    const filingStatusSelect = document.getElementById('filing_status');
    const inputs = form.querySelectorAll('input[type="number"]');

    // Federal standard deductions by tax year and filing status
    const YEAR_INFO = {
        '2025': {
            projected: false,
            fedStandard: { MFJ: 31500, SINGLE: 15750, MFS: 15750, HOH: 23600 }
        },
        '2026': {
            projected: false,
            fedStandard: { MFJ: 32200, SINGLE: 16100, MFS: 16100, HOH: 24150 }
        },
        '2027': {
            projected: true,
            fedStandard: { MFJ: 33000, SINGLE: 16500, MFS: 16500, HOH: 24750 }
        },
    };

    const formatDeduction = (amount) =>
        new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(amount);

    const updateYearLabels = (year, state, filingStatus) => {
        const info = YEAR_INFO[year] || YEAR_INFO['2026'];
        const statusKey = filingStatus || 'MFJ';
        const stdDeduction = info.fedStandard[statusKey] || info.fedStandard['MFJ'];

        // Safe updates with null checks
        const yearBadge = document.getElementById('year-badge');
        if (yearBadge) yearBadge.innerText = `${year} Tax Year`;

        const yearProjBadge = document.getElementById('year-projected-badge');
        if (yearProjBadge) yearProjBadge.style.display = info.projected ? 'inline' : 'none';
        
        const stateOption = stateSelect ? stateSelect.querySelector(`option[value="${state}"]`) : null;
        const stateText = stateOption ? stateOption.text.split(' (')[0] : state;
        
        const stateBadge = document.getElementById('state-badge');
        if (stateBadge) stateBadge.innerText = `${stateText} Resident`;

        // Update Filing Status Badge
        const statusOption = filingStatusSelect ? filingStatusSelect.querySelector(`option[value="${statusKey}"]`) : null;
        const statusBadge = document.getElementById('status-badge');
        if (statusBadge && statusOption) {
            statusBadge.innerText = statusOption.text;
        }

        const irmaaTitle = document.getElementById('irmaa-title');
        if (irmaaTitle) irmaaTitle.innerText = `Medicare IRMAA Tier (${year})`;
        
        const statusTextShort = statusOption ? statusOption.text : 'MFJ';
        const fedHelp = document.getElementById('fed-standard-help');
        if (fedHelp) {
            fedHelp.innerText = `${year} ${statusTextShort} Standard Deduction is ${formatDeduction(stdDeduction)}.`;
        }

        const yearHelp = document.getElementById('year-help');
        if (yearHelp) {
            yearHelp.innerText = info.projected
                ? '2027 brackets and IRMAA thresholds are projected (~2.5% inflation over 2026).'
                : 'Brackets and IRMAA thresholds for selected parameters.';
        }

        document.title = `Tax Estimator`;
    };

    const formatCurrency = (val) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 2
        }).format(val || 0);
    };

    const formatPercent = (val) => {
        return new Intl.NumberFormat('en-US', {
            style: 'percent',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(val || 0);
    };

    const triggerCalculation = async () => {
        const year = yearSelect.value;
        const state = stateSelect.value;
        const filing_status = filingStatusSelect ? filingStatusSelect.value : 'MFJ';
        const formData = { year, state, filing_status };

        inputs.forEach(input => {
            formData[input.name] = input.value === "" ? 0 : parseFloat(input.value);
        });

        if (formData.qualified_dividends > formData.ordinary_dividends) {
            document.getElementById('qualified_dividends').setCustomValidity('Qualified dividends cannot exceed total ordinary dividends.');
            form.reportValidity();
            return;
        } else {
            document.getElementById('qualified_dividends').setCustomValidity('');
        }

        try {
            const response = await fetch('/api/calculate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });

            const raw = await response.text();
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
            updateUI(data);

        } catch (e) {
            showError('Could not reach calculator server.');
            console.error("Error during calculation:", e);
        }
    };

    const showError = (message) => {
        const banner = document.getElementById('error-banner');
        if (banner) {
            banner.textContent = message;
            banner.hidden = false;
        }
    };

    const hideError = () => {
        const banner = document.getElementById('error-banner');
        if (banner) {
            banner.hidden = true;
        }
    };

    const updateUI = (data) => {
        const currentYear = data.year || yearSelect.value;
        const currentState = data.state || stateSelect.value;
        const currentFilingStatus = data.filing_status || (filingStatusSelect ? filingStatusSelect.value : 'MFJ');
        
        updateYearLabels(currentYear, currentState, currentFilingStatus);

        // Dynamic State Labels
        const stateLabel = document.getElementById('liab-state-label');
        if (stateLabel) stateLabel.innerText = `${currentState} State Income Tax`;
        
        const stateTitle = document.getElementById('summary-state-title');
        if (stateTitle) stateTitle.innerText = `${currentState} State Tax Brackets`;

        // Handle Local Tax UI visibility (NYC specific)
        const localContainer = document.getElementById('liab-local-container');
        const localAccordion = document.getElementById('accordion-local');
        if (data.nyc_tax !== undefined && currentState === 'NY' && data.nyc_tax > 0) {
            if (localContainer) localContainer.style.display = 'list-item';
            if (localAccordion) localAccordion.style.display = 'block';
            
            const liabNycTax = document.getElementById('liab-nyc-tax');
            if (liabNycTax) liabNycTax.innerText = formatCurrency(data.nyc_tax);
            
            const accTaxNyc = document.getElementById('acc-tax-nyc');
            if (accTaxNyc) accTaxNyc.innerText = formatCurrency(data.nyc_tax);
            
            renderBracketTable('table-nyc', data.nyc_breakdown);
        } else {
            if (localContainer) localContainer.style.display = 'none';
            if (localAccordion) localAccordion.style.display = 'none';
        }

        // KPI Summary Cards
        const kpiTotalTax = document.getElementById('kpi-total-tax');
        if (kpiTotalTax) kpiTotalTax.innerText = formatCurrency(data.total_tax);

        const kpiEffRate = document.getElementById('kpi-eff-rate');
        if (kpiEffRate) kpiEffRate.innerText = `${formatPercent(data.effective_rate)} Effective Rate`;

        const kpiNetIncome = document.getElementById('kpi-net-income');
        if (kpiNetIncome) kpiNetIncome.innerText = formatCurrency(data.net_income);

        const kpiAgi = document.getElementById('kpi-agi');
        if (kpiAgi) kpiAgi.innerText = `on ${formatCurrency(data.agi)} AGI`;

        // Liabilities
        const liabFedTax = document.getElementById('liab-fed-tax');
        if (liabFedTax) liabFedTax.innerText = formatCurrency(data.fed_tax);

        const liabNiitTax = document.getElementById('liab-niit-tax');
        if (liabNiitTax) liabNiitTax.innerText = formatCurrency(data.niit);

        const liabNysTax = document.getElementById('liab-nys-tax');
        if (liabNysTax) liabNysTax.innerText = formatCurrency(data.nys_tax);

        // IRMAA details
        const irmaaBadge = document.getElementById('irmaa-tier-badge');
        if (irmaaBadge) irmaaBadge.innerText = `Tier ${data.irmaa_tier}`;

        const irmaaMagi = document.getElementById('irmaa-magi-val');
        if (irmaaMagi) irmaaMagi.innerText = formatCurrency(data.irmaa_magi);

        const irmaaPartB = document.getElementById('irmaa-part-b-val');
        if (irmaaPartB) irmaaPartB.innerText = formatCurrency(data.irmaa_part_b);

        const irmaaPartD = document.getElementById('irmaa-part-d-val');
        if (irmaaPartD) irmaaPartD.innerText = formatCurrency(data.irmaa_part_d);
        
        const annualSurcharge = (data.irmaa_part_b + data.irmaa_part_d) * 12;
        const irmaaAnnualTotal = document.getElementById('irmaa-annual-total');
        if (irmaaAnnualTotal) {
            irmaaAnnualTotal.innerText = `Total annual surcharge: ${formatCurrency(annualSurcharge)}/yr (per person)`;
        }

        // Meter step calculation
        const marker = document.getElementById('irmaa-marker-current');
        const steps = document.querySelectorAll('.irmaa-bracket-step');
        steps.forEach((step, idx) => {
            if (idx + 1 === data.irmaa_tier) {
                step.classList.add('active');
            } else {
                step.classList.remove('active');
            }
        });

        if (marker) {
            const posMap = { 1: 8, 2: 25, 3: 42, 4: 58, 5: 75, 6: 92 };
            marker.style.left = `${posMap[data.irmaa_tier] || 8}%`;
        }

        // Accordion Totals
        const accFedOrd = document.getElementById('acc-tax-fed-ord');
        if (accFedOrd) accFedOrd.innerText = formatCurrency(data.fed_ord_tax);

        const accFedPref = document.getElementById('acc-tax-fed-pref');
        if (accFedPref) accFedPref.innerText = formatCurrency(data.fed_pref_tax);

        const accNys = document.getElementById('acc-tax-nys');
        if (accNys) accNys.innerText = formatCurrency(data.nys_tax);

        // Render Bracket Tables
        renderBracketTable('table-fed-ord', data.fed_ord_breakdown);
        renderBracketTable('table-fed-pref', data.fed_pref_breakdown);
        renderBracketTable('table-nys', data.nys_breakdown);
    };

    const renderBracketTable = (tableId, breakdown) => {
        const tbody = document.querySelector(`#${tableId} tbody`);
        if (!tbody) return;
        tbody.innerHTML = '';

        if (!breakdown || breakdown.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td colspan="4" style="text-align: center; color: var(--text-secondary);">No state income tax for this jurisdiction.</td>`;
            tbody.appendChild(tr);
            return;
        }

        breakdown.forEach(row => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${row.range}</td>
                <td>${formatCurrency(row.taxable)}</td>
                <td>${(row.rate * 100).toFixed(2)}%</td>
                <td>${formatCurrency(row.tax)}</td>
            `;
            tbody.appendChild(tr);
        });
    };

    form.addEventListener('submit', (e) => {
        e.preventDefault();
    });

    ['change', 'input'].forEach(evt => {
        if (yearSelect) yearSelect.addEventListener(evt, triggerCalculation);
        if (stateSelect) stateSelect.addEventListener(evt, triggerCalculation);
        if (filingStatusSelect) filingStatusSelect.addEventListener(evt, triggerCalculation);
    });

    inputs.forEach(input => input.addEventListener('input', triggerCalculation));

    triggerCalculation();
});