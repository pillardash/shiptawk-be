# Codex Templates

## Template: staged implementation prompt

```text
You are implementing a production-ready feature for a SaaS app.

Product context:
[short description]

Tech stack:
- [stack]

Important invariants:
- [rule 1]
- [rule 2]

For this stage:
- [current scope only]

Requirements:
- [functional requirements]
- [data requirements]
- [security or workflow requirements]

Return:
- actual files and implementations
- typed helpers
- route handlers / server actions where appropriate
- minimal explanation

Do not:
- return pseudo-code
- leave TODOs in core flows
- stop at architecture notes

After implementing, review your own output and improve correctness, typing, maintainability, and production safety.
```

## Template: refactor prompt

```text
Refactor the current implementation for correctness, strong typing, maintainability, and production safety.
Preserve behavior unless a bug or unsafe pattern requires change.
Remove dead code, tighten interfaces, improve naming, and simplify weak abstractions.
Return concrete code changes, not a broad explanation.
```

## Template: repair prompt when Codex is too abstract

```text
Do not give pseudo-code or high-level architecture notes.
Write the actual files and implementations needed for this stage.
If a decision is needed, choose a reasonable production-oriented default and proceed.
```
