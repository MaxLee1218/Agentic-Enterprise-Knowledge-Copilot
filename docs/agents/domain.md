# Domain documentation

This repository uses one domain-documentation context: a root `GLOSSARY.md` when present,
and architectural decisions in `docs/adr/`.

Before exploring domain behavior, read the glossary if it exists and the ADRs relevant to the
change. If a glossary is absent, continue with the frozen design's terminology; create glossary
entries only when domain-modeling work actually resolves a term.

For Supplier Quality behavior and evaluations, first read all seven authority documents listed
in AGENTS.md section 1.1. For async runtime work, also read the architecture and ADRs listed in
section 1.2. These authority requirements take precedence over this navigation guide.

Use the existing Task, TaskContract, TaskPlan, ToolResult, EvidenceItem, ApprovalRequest, and
Artifact terminology. Surface any conflict between proposed behavior and an accepted design
before implementation. Task completion and an evaluation case matching an expected refusal are
different outcomes; retain that distinction in metrics and issue acceptance criteria.
