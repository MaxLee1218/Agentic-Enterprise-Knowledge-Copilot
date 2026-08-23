# Accounts Payable Policy

Document version: 2026.1  
Owner: Demo Finance Policy Owner  
Classification: CONFIDENTIAL  
Effective period: 2026-01-01 through 2026-12-31

This sanitized fixture defines the controlled processing language for the synthetic Accounts
Payable v1 dataset. It contains no real supplier, employee, bank, tax, or payment-reference data.

<!-- policy-chunk:ap-policy-invoice-processing -->
## Invoice processing and duplicate review

Accounts Payable records each posted standard invoice with its supplier, invoice date, currency,
gross amount, purchase-order reference when applicable, and approved due date. Exact duplicate
review uses the controlled normalized invoice number together with supplier, invoice date,
currency, and gross amount. A duplicate finding is a review signal only; it does not cancel,
modify, approve, or pay an invoice. Analysts escalate evidenced exceptions for manual review and
retain the governed task, calculation, and source references.
<!-- /policy-chunk -->

<!-- policy-chunk:ap-policy-scope-and-escalation -->
## Scope and escalation

The investigation is read-only and limited to the authorized tenant, legal entities, business
units, suppliers, currencies, invoice-date range, and fixed data snapshot. Unsupported settlement
shapes and incomplete source records remain visible as reason-coded exclusions. Aggregate and
detail reports follow the user's server-authorized finance scope. Reports provide internal
management evidence and recommendations; they are not transaction approval or payment authority.
<!-- /policy-chunk -->
