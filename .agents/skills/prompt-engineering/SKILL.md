---
name: prompt-engineering-codex
description: improve, structure, and stage prompts for codex or chatgpt when the user wants better coding prompts, cleaner implementation instructions, phased build plans, stronger guardrails, better vibe-coding prompts, or reusable prompt templates for software development. use for requests such as “write a better codex prompt”, “turn this spec into prompts”, “make this prompt produce real code”, “help me prompt codex for nextjs/supabase/inngest”, or “refine my implementation prompt”.
---

# Prompt Engineering for Codex

Follow this skill when the task is to create or improve prompts that another coding model will use.

## Core principle

Do not write one giant vague prompt unless the user explicitly wants that.
Default to a staged prompt pack:

1. foundation/context prompt
2. schema/data prompt (if applicable)
3. integration prompt
4. feature implementation prompt
5. review/refactor prompt
6. polish/debug prompt

The goal is to help Codex produce concrete, production-oriented code instead of pseudo-code, filler architecture, or generic advice.

## What to optimize for

Every prompt you create should improve these six things:

- **specificity**: clear stack, boundaries, constraints, and deliverables
- **sequencing**: one stage at a time unless the user wants a single combined prompt
- **invariants**: hard rules that must hold everywhere
- **output shape**: what files, code, schema, or explanation must be returned
- **anti-drift**: reminders to avoid pseudo-code, placeholders, and hand-wavy abstractions
- **iteration**: include a follow-up improvement prompt when useful

## Default workflow

### 1) Extract the build target

Infer and restate these quietly in the prompt package:

- product or feature goal
- target stack
- architecture constraints
- non-negotiable rules
- preferred code quality level
- what “done” means

If the user already gave this information, do not ask again. Build from it.

### 2) Choose the prompt mode

Use one of these modes:

- **scaffold mode**: create initial app structure and primitives
- **implementation mode**: build a real feature end to end
- **integration mode**: wire external APIs, auth, webhooks, jobs, db
- **refactor mode**: improve existing code without changing behavior too much
- **debug mode**: fix broken code or incorrect behavior
- **review mode**: audit for correctness, typing, safety, and maintainability
- **prompt-pack mode**: generate multiple prompts for staged vibe-coding

If unsure, use prompt-pack mode.

### 3) Build the prompt from reusable sections

Compose prompts from these blocks in this order.

#### A. Role + mission

Tell Codex what it is doing in one sentence.

Example:

`You are implementing a production-ready Next.js feature for a SaaS app.`

#### B. Product context

State what the product does and why the feature matters.
Keep this short.

#### C. Tech stack

Name exact stack and conventions.

Example:

- Next.js App Router
- TypeScript
- Supabase
- Inngest
- server-first secure patterns

#### D. Invariants

List hard rules as non-negotiable.

Example:

- users must explicitly select repos to monitor
- unselected repos must never trigger ingestion or posting workflows
- approval is required before posting to X

#### E. Scope for this stage

Say what Codex should do now, not everything eventually desired.

#### F. Required deliverables

Force concrete output.

Example:

- actual files and implementations
- SQL migration
- route handlers
- typed helper functions
- minimal explanation

#### G. Anti-pattern guardrails

Explicitly ban weak behavior.

Use phrases like:

- do not give pseudo-code
- do not return placeholder architecture only
- do not leave TODO logic for core flows
- do not explain broadly when code should be written

#### H. Quality bar

Specify standards:

- strong typing
- maintainable code
- idempotency where needed
- error handling
- secure defaults
- consistent naming

#### I. Optional follow-up instruction

When useful, append:

`After implementing, review your own output and improve correctness, typing, maintainability, and production safety.`

### 4) Prefer stage-specific prompts over mega-prompts

For medium or large builds, produce a set of prompts instead of one long prompt.

Good sequence:

1. master brief
2. scaffold
3. database/schema
4. auth/integration
5. core workflow
6. async jobs/webhooks
7. UI for review/approval
8. polish/refactor

### 5) Add an invariant block for system-critical rules

When the product has a rule that must be enforced throughout the stack, create a repeated invariant block and instruct the user to include it in multiple prompts.

Example invariant block:

```text
Important invariant:
A GitHub connection does not mean all repositories are monitored.
Monitoring is opt-in per repository.
Only repositories explicitly selected by the user can trigger ingestion, scoring, draft generation, or posting workflows.
Enforce this invariant across UI, API, DB queries, and background jobs.
```

### 6) Match prompt strictness to the task

Use these defaults:

- **implementation/integration**: highly specific and directive
- **review/refactor**: focused and constrained
- **brainstorming**: looser but still grounded in the stack
- **bug fixing**: minimal scope, reproducible failure, direct fix target

## Output patterns

### Pattern 1: Single high-quality prompt

Use when the task is narrow.

Structure:

1. short one-line mission
2. product context
3. stack
4. requirements
5. invariants
6. deliverables
7. anti-patterns
8. quality bar

### Pattern 2: Staged prompt pack

Use when the task is medium or large.
For each prompt include:

- title
- when to use it
- actual prompt body

### Pattern 3: Repair prompt

Use when the user says Codex output is weak.
Target the failure mode directly.

Examples of repair focus:

- too abstract
- too much pseudo-code
- ignored invariant
- weak typing
- over-explains instead of implementing
- broke existing behavior

## Failure modes to correct

If the user gives a weak prompt or says results are poor, improve against these common problems:

- **too broad** → split into stages
- **too vague** → add stack, deliverables, and invariants
- **no output contract** → require exact file/code outputs
- **architecture fluff** → demand implementation
- **model drift** → repeat the critical rule block
- **unsafe automation** → add approval/safety requirements
- **poor maintainability** → require modular design and typing

## Prompt-writing rules

- Prefer imperative language.
- Prefer short sections over long paragraphs.
- Keep prompts dense with constraints, not motivational prose.
- Mention exact technologies and architectural boundaries.
- Force Codex to choose maintainable code over clever code.
- If the user says “vibe code,” still keep prompts concrete and production-oriented.
- If the request is for a coding workflow, include a final self-review prompt.
- When the user already has a strong idea, do not rewrite the product spec from scratch; convert it into execution prompts.

## Default response behavior for this skill

When using this skill, produce one of these deliverables depending on the request:

### A. Prompt improvement

Return:

1. improved prompt
2. short note on what changed
3. optional stronger version if useful

### B. Prompt pack

Return:

1. recommended prompt order
2. prompts grouped by stage
3. repeated invariant block if applicable
4. one follow-up “review and improve” prompt

### C. Prompt diagnosis

Return:

1. what is wrong with the current prompt
2. revised prompt
3. one-liner explaining why the revision is better

## House style for prompts you generate

Use direct wording like:

- implement
- build
- enforce
- return
- persist
- validate
- do not

Avoid fluffy wording like:

- create an amazing
- think deeply about
- elegantly
- robust and scalable without specifics
- leverage best practices without naming them

## References

Use these only when helpful:

- [prompt patterns](references/prompt-patterns.md)
- [anti-patterns and fixes](references/anti-patterns.md)
- [codex templates](references/codex-templates.md)
