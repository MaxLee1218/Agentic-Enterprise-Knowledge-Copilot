# Issue tracker

Specifications and implementation tickets live in
[GitHub Issues](https://github.com/MaxLee1218/Agentic-Enterprise-Knowledge-Copilot/issues).
Repository identity: `MaxLee1218/Agentic-Enterprise-Knowledge-Copilot`.

## Read and publish work

Use the connected GitHub tools for searches, reads, issue creation, and additive label changes.
The `gh` CLI is an alternative when installed and authenticated. For multiline CLI bodies,
write a temporary file and pass `--body-file`.

If a connector denies a write with `Resource not accessible by integration`, use the authenticated
GitHub UI for the already-authorized action. Repository ownership does not imply that the
connector has write scope. Verify successful UI writes by reading the resulting issue.

Before creation, search existing issues by the feature's distinctive title and scope.
After a successful write, read the issue and verify its number, body, labels, and URL.
After an uncertain write, reconcile existing issues before retrying to avoid duplicates.

Publish one issue per specification and one issue per implementation slice. Apply
`ready-for-agent` to fully specified work. Read an implementation issue's full body,
comments, parent specification, and open blockers before starting it.

## Parent and blocking relationships

Use GitHub's native sub-issue relationship for implementation tickets under their specification.
Use native issue dependencies to record execution blockers, and create blockers first.
The GitHub UI can manage these relationships when the connected tool does not expose them.
If a native relationship is unavailable, include `Part of #N` and `Blocked by #N`
references in the child body and report that the fallback is textual.

An issue may be specified and labelled ready-for-agent while still blocked. Start a ticket
only when every blocker is completed. Do not infer readiness from the label alone.
Preserve existing parent bodies and states when publishing children.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Current evaluation work

The Supplier Quality value pilot uses one specification and three ordered implementation slices:
offline P01 with an independent oracle, budgeted live P01, and the complete ten-scenario report.
The specification is #1; implementation tickets are #2, #3, and #4, with #3 blocked by #2 and
#4 blocked by #3. The [pilot plan](../supplier-quality-value-pilot.md) records their authoritative links.
