# Anti-Patterns and Fixes

## Anti-pattern: One giant vague prompt

Weak:

```text
Build my SaaS app with Next.js and Supabase.
```

Fix:

- split into stages
- add stack details
- add invariants
- define current stage deliverables

## Anti-pattern: Architecture-only output

Weak:

```text
Design the system and explain the approach.
```

Fix:

```text
Write the actual files and implementations. Do not stop at architecture notes or pseudo-code.
```

## Anti-pattern: Missing hard rule

Weak:

- core product rule appears once in a long prompt

Fix:

- isolate the rule in an invariant block
- repeat it in relevant stage prompts

## Anti-pattern: No output contract

Weak:

- prompt says “help me build” but not what to return

Fix:

- require specific outputs such as SQL, route handlers, typed helpers, components, tests

## Anti-pattern: Too much abstraction

Weak:

- “use best practices”

Fix:

- name the best practices that matter: strong typing, error handling, idempotency, secure defaults, maintainable modules
