# NYC Resident Tax Liability Estimator (2026)

A command-line tool written in Python to estimate the total progressive income tax liability (Federal, NY State, and NYC Local) for **Married Filing Jointly (MFJ)** couples living in New York City. The tool calculates taxes directly based on your input taxable income.

## 2026 Assumptions & Parameters

### Brackets (MFJ)

#### 1. Federal (IRS 2026)
*   **10%**: `$0` to `$24,800`
*   **12%**: `$24,801` to `$100,800`
*   **22%**: `$100,801` to `$211,400`
*   **24%**: `$211,401` to `$403,550`
*   **32%**: `$403,551` to `$512,450`
*   **35%**: `$512,451` to `$768,700`
*   **37%**: `$768,701` or more

#### 2. NY State (2026 Statutory Rates)
*   **3.90%**: `$0` to `$17,150`
*   **4.40%**: `$17,151` to `$23,400`
*   **5.15%**: `$23,401` to `$27,900`
*   **5.40%**: `$27,901` to `$161,550`
*   **5.90%**: `$161,551` to `$430,800`
*   **6.85%**: `$430,801` to `$2,155,350`
*   **9.65%**: `$2,155,351` to `$5,000,000`
*   **10.30%**: `$5,000,001` to `$25,000,000`
*   **10.90%**: `$25,000,001` or more

#### 3. NYC Local Tax
*   **3.078%**: `$0` to `$21,600`
*   **3.762%**: `$21,601` to `$45,000`
*   **3.819%**: `$45,001` to `$90,000`
*   **3.876%**: `$90,001` or more

---

## How to Run

1. Make the script executable:
   ```bash
   chmod +x estimator.py
   ```

2. Run with Taxable Income:
   ```bash
   python3 estimator.py 117800
   ```

