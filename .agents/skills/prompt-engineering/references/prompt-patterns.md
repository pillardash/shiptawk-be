# Prompt Patterns

Use these patterns to make coding prompts more reliable.

## 1. Mission-first pattern

Start with one sentence that defines the job.

Example:

```text
You are implementing a production-ready repository monitoring workflow for a SaaS app.
```

## 2. Stack lock pattern

Name the exact stack and style constraints.

Example:

```text
Use Next.js App Router, TypeScript, Supabase, and Inngest. Prefer server-side secure patterns, strong typing, and modular services.
```

## 3. Invariant pattern

Repeat rules that must never be violated.

Example:

```text
Important invariant:
Only user-selected repositories may trigger monitoring or posting workflows.
Enforce this rule across UI, API, database queries, and background jobs.
```

## 4. Deliverable pattern

Force concrete output.

Example:

```text
Return actual files and implementations, including route handlers, query helpers, and SQL migration code. Do not return pseudo-code.
```

## 5. Stage boundary pattern

Constrain the current prompt to one stage.

Example:

```text
For this stage, only implement GitHub OAuth connection, repository fetch, and repository persistence. Do not implement webhook ingestion yet.
```

## 6. Self-review pattern

Append this when code quality matters.

Example:

```text
After implementing, review your output and improve correctness, typing, maintainability, production safety, and consistency.
```
