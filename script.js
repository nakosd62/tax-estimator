document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('calculator-form');
    const yearSelect = document.getElementById('year');
    const inputs = form.querySelectorAll('input[type="number"]');

    const YEAR_INFO = {
        '2025': { fedStandard: 31500, stateStandard: 16050, projected: false },
        '2026': { fedStandard: 32200, stateStandard: 16050, projected: false },
        '2027': { fedStandard: 33000, stateStandard: 16050, projected: true },
    };

    const formatDeduction = (amount) =>
        new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(amount);

    const updateYearLabels = (year) => {
        const info = YEAR_INFO[year] || YEAR_INFO['2026'];
        document.getElementById('year-badge').innerText = `${year} Tax Year`;
        document.getElementById('year-projected-badge').style.display = info.projected ? 'inline' : 'none';
        document.getElementById('irmaa-title').innerText = `Medicare IRMAA Tier (${year})`;
        document.getElementById('fed-standard-help').innerText =
            `${year} MFJ Standard Deduction is ${formatDeduction(info.fedStandard)}.`;
        document.getElementById('year-help').innerText = info.projected
            ? '2027 brackets and IRMAA thresholds are projected (~2.5% inflation over 2026).'
            : 'Brackets and IRMAA thresholds for the selected tax year.';
        document.title = `NYC Tax & IRMAA Estimator (${year})`;
    };

    // Currency Formatter
    const formatCurrency = (val) => {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: 'USD',
            maximumFractionDigits: 2
        }).format(val);
    };

    // Percentage Formatter
    const formatPercent = (val) => {
        return new Intl.NumberFormat('en-US', {
            style: 'percent',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(val);
    };

    // Calculate functions
    const triggerCalculation = async () => {
        const year = yearSelect.value;
        const formData = { year };

        inputs.forEach(input => {
            formData[input.name] = input.value === "" ? null : parseFloat(input.value);
        });

        // Simple validation check before submitting
        if (formData.qualified_dividends > formData.ordinary_dividends) {
            // Qualified dividends cannot exceed ordinary dividends
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
                showError('Could not parse server response. Restart with: python3 server.py');
                console.error('JSON parse error:', parseError, raw.slice(0, 200));
                return;
            }

            if (!response.ok) {
                showError(data.error || 'Calculation failed.');
                console.error("API error:", data);
                return;
            }

            hideError();
            updateUI(data);

        } catch (e) {
            showError(window.location.protocol === 'file:'
                ? 'Open via the local server: python3 server.py → http://localhost:8001'
                : 'Could not reach the calculator server. Run: python3 server.py');
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

    // Update UI elements
    const updateUI = (data) => {
        if (data.year && yearSelect.value !== data.year) {
            yearSelect.value = data.year;
        }
        updateYearLabels(data.year || yearSelect.value);

        // KPI Summary Cards
        document.getElementById('kpi-total-tax').innerText = formatCurrency(data.total_tax);
        document.getElementById('kpi-eff-rate').innerText = `${formatPercent(data.effective_rate)} Effective Rate`;
        document.getElementById('kpi-net-income').innerText = formatCurrency(data.net_income);
        document.getElementById('kpi-agi').innerText = `on ${formatCurrency(data.agi)} AGI`;

        // Detailed Liabilities
        document.getElementById('liab-fed-tax').innerText = formatCurrency(data.fed_tax);
        document.getElementById('liab-niit-tax').innerText = formatCurrency(data.niit);
        document.getElementById('liab-nys-tax').innerText = formatCurrency(data.nys_tax);
        document.getElementById('liab-nyc-tax').innerText = formatCurrency(data.nyc_tax);

        // IRMAA details
        document.getElementById('irmaa-tier-badge').innerText = `Tier ${data.irmaa_tier}`;
        document.getElementById('irmaa-magi-val').innerText = formatCurrency(data.irmaa_magi);
        document.getElementById('irmaa-part-b-val').innerText = formatCurrency(data.irmaa_part_b);
        document.getElementById('irmaa-part-d-val').innerText = formatCurrency(data.irmaa_part_d);
        
        const annualSurcharge = (data.irmaa_part_b + data.irmaa_part_d) * 12;
        document.getElementById('irmaa-annual-total').innerText = 
            `Total annual surcharge: ${formatCurrency(annualSurcharge)}/yr (per person)`;

        // Adjust IRMAA Meter Marker
        // Meter limits ranges roughly from Tier 1 (218k) to Tier 6 (750k+)
        const marker = document.getElementById('irmaa-marker-current');
        const steps = document.querySelectorAll('.irmaa-bracket-step');
        
        // Remove active class from all steps
        steps.forEach((step, idx) => {
            if (idx + 1 === data.irmaa_tier) {
                step.classList.add('active');
            } else {
                step.classList.remove('active');
            }
        });

        // Set position percentage
        let positionPct = 0;
        if (data.irmaa_tier === 1) {
            positionPct = 8;
        } else if (data.irmaa_tier === 2) {
            positionPct = 25;
        } else if (data.irmaa_tier === 3) {
            positionPct = 42;
        } else if (data.irmaa_tier === 4) {
            positionPct = 58;
        } else if (data.irmaa_tier === 5) {
            positionPct = 75;
        } else {
            positionPct = 92;
        }
        marker.style.left = `${positionPct}%`;

        // Accordion Totals
        document.getElementById('acc-tax-fed-ord').innerText = formatCurrency(data.fed_ord_tax);
        document.getElementById('acc-tax-fed-pref').innerText = formatCurrency(data.fed_pref_tax);
        document.getElementById('acc-tax-nys').innerText = formatCurrency(data.nys_tax);
        document.getElementById('acc-tax-nyc').innerText = formatCurrency(data.nyc_tax);

        // Recapture warning
        const note = document.getElementById('recapture-note');
        if (data.agi > 107650) {
            note.style.display = 'block';
        } else {
            note.style.display = 'none';
        }

        // Render Tables
        renderBracketTable('table-fed-ord', data.fed_ord_breakdown);
        renderBracketTable('table-fed-pref', data.fed_pref_breakdown);
        renderBracketTable('table-nys', data.nys_breakdown);
        renderBracketTable('table-nyc', data.nyc_breakdown);
    };

    // Render tables rows helper
    const renderBracketTable = (tableId, breakdown) => {
        const tbody = document.querySelector(`#${tableId} tbody`);
        tbody.innerHTML = '';

        if (!breakdown || breakdown.length === 0) {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td colspan="4" style="text-align: center; color: var(--text-secondary);">No income taxed in this category.</td>`;
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

    // Attach listeners for reactive auto-calculation
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        triggerCalculation();
    });

    inputs.forEach(input => {
        input.addEventListener('input', () => {
            triggerCalculation();
        });
    });

    yearSelect.addEventListener('change', () => {
        updateYearLabels(yearSelect.value);
        triggerCalculation();
    });

    updateYearLabels(yearSelect.value);
    triggerCalculation();
});
