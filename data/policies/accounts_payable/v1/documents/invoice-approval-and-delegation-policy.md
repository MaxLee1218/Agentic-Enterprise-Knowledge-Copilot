# Invoice Approval and Delegation Policy

Document version: 2026.1  
Owner: Demo Finance Controls Owner  
Classification: CONFIDENTIAL  
Effective period: 2026-01-01 through 2026-12-31

This sanitized fixture describes internal review and escalation authority for synthetic data.

<!-- policy-chunk:invoice-no-po-exception -->
## Approved no-PO exception evidence

A no-PO exception is recognized only when the governed invoice facts state that the exception is
approved and provide a nonblank approved exception reference. A missing or malformed reference
does not prove approval. The analysis records the outcome and its opaque source key; it does not
grant business approval or modify the invoice.
<!-- /policy-chunk -->

<!-- policy-chunk:invoice-materiality -->
## Review materiality and delegation

Controlled materiality labels a detected monetary exception as a finding when exposure is at or
above the applicable currency threshold; lower exposure remains a warning. Materiality never
removes an exception from evidenced counts. A user may request a stricter lower threshold but may
not raise, remove, or change the currency of the governed threshold. Executable values are
published in rule AP-MATERIALITY-2026-1.
<!-- /policy-chunk -->
