# Payment Terms Policy

Document version: 2026.1  
Owner: Demo Treasury Policy Owner  
Classification: CONFIDENTIAL  
Effective period: 2026-01-01 through 2026-12-31

This sanitized fixture defines payment-timing and overpayment review for the synthetic v1 slice.

<!-- policy-chunk:payment-timing -->
## Due date, late payment, and material early payment

The source-approved due date is authoritative for v1 review. For exactly one eligible settled
payment, payment after the due date is late by the positive calendar-day difference. Payment
before the due date is materially early when the calendar-day difference is at or above the
controlled threshold. Equality at the early-payment threshold is an exception. Unpaid, reversed,
void, partial, or multiple-payment shapes are not inferred; they are reason-coded exclusions.
The executable early-day value is published in rule AP-MATERIAL-EARLY-2026-1.
<!-- /policy-chunk -->

<!-- policy-chunk:payment-overpayment -->
## Overpayment tolerance

For exactly one eligible settled payment in the same currency as the invoice, overpayment amount
is payment amount minus invoice gross amount. A review exception exists only when the positive
difference is greater than the controlled currency tolerance; equality is within tolerance.
This analysis is read-only and cannot initiate recovery or payment action. Executable tolerances
are published in rule AP-OVERPAYMENT-2026-1.
<!-- /policy-chunk -->
