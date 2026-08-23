# Procurement and Purchase Order Policy

Document version: 2026.1  
Owner: Demo Procurement Policy Owner  
Classification: CONFIDENTIAL  
Effective period: 2026-01-01 through 2026-12-31

This sanitized fixture applies only to the synthetic Accounts Payable v1 review population.

<!-- policy-chunk:procurement-po-required -->
## Purchase-order requirement

A posted standard invoice requires an approved purchase order when its gross amount is at or
above the controlled threshold for its legal entity and currency, unless an approved no-PO
exception reference is present in the governed source data. Threshold equality requires a
purchase order. The executable values are published in rule AP-PO-REQUIRED-2026-1; this document
supplies policy meaning and citation text but does not itself authorize a threshold change.
<!-- /policy-chunk -->

<!-- policy-chunk:procurement-po-variance -->
## Single-invoice purchase-order variance

For an eligible single-invoice purchase order, Accounts Payable compares invoice gross amount
with the approved purchase-order header amount. A review exception exists only when the absolute
rate variance or absolute amount variance is greater than its controlled tolerance. Equality is
within tolerance. Zero approved amount, multi-invoice matching, currency mismatch, or inconsistent
supplier and organization dimensions is excluded or failed according to the governed data rule.
Executable tolerances are published in rule AP-PO-VARIANCE-2026-1.
<!-- /policy-chunk -->
