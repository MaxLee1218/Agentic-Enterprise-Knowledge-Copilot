# GitHub Repository Presentation Checklist

Use the following values when configuring the repository page.

## About

**Description**

```text
Governed enterprise AI task execution with policy, approval, evidence, recovery and verified reports.
```

**Website**

Leave empty until a maintained public demo or portfolio page exists. Do not point recruiters to an
unavailable local address.

**Topics**

```text
agentic-ai
enterprise-ai
langgraph
fastapi
react
postgresql
human-in-the-loop
llm-evaluation
mcp
ai-governance
```

## Social preview

Upload [`docs/assets/readme/social-preview.png`](assets/readme/social-preview.png) through:

```text
Repository Settings -> General -> Social preview -> Edit
```

GitHub does not currently expose Social Preview upload through the standard `gh repo edit`
command, so this remains one manual repository-setting step.

## Repository settings

- Pin the repository on the GitHub profile.
- Enable the Actions tab and keep the CI workflow visible.
- Enable branch protection for `main` when collaboration begins.
- Require the three CI jobs before merging protected pull requests.
- Create a release only from a clean, reviewed commit with passing CI.

## Suggested first release

Use tag `v0.1.0` after the presentation changes are reviewed and committed.

Suggested title:

```text
v0.1.0 — Governed enterprise task-execution vertical slices
```

Suggested release summary:

```text
Initial portfolio release of Agentic Enterprise Knowledge Copilot, including Supplier Quality and
Accounts Payable workflows, governed tool execution, durable asynchronous runtime, evidence and
verification, React workspace, deterministic reports, tests and evaluation baselines.
```

Do not publish the release from an uncommitted working tree; the tag must identify the exact code
and documentation presented to reviewers.

