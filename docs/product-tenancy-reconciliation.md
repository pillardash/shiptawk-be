# Product Tenancy Reconciliation

`0001_initial_product_schema` is a predeployment baseline for a destructive reset that has been
explicitly accepted outside this repository. The backend has never deployed. After the first
deployment, this revision is immutable and every schema change must be incremental.

The fresh-reset migration mode was reconfirmed on 2026-07-21. Existing legacy product data may be
discarded; FastAPI parity and the coordinated frontend cutover are still required before writes
are opened.

## Compatibility assumptions

- The supplied `../supabase-schema.sql` is the compatibility authority available to this task.
  No real Supabase instance was accessed, so schema drift outside that file remains unknown.
- Existing workflow table and column names are preserved for repos, drafts, normalized events,
  evaluations, candidates, generation runs, feedback, social accounts, achievement digests, and
  changelog data. GitHub raw ingress is the intentional exception: it has no tenant owner, stores
  only ciphertext plus its key version, and fans out through `github_raw_event_consumers`.
- All tenant-owned tables now require `workspace_id`. The active frontend does not currently
  provide it, so the reset and frontend cutover must be coordinated; old writes will fail until
  they include the owning workspace.
- Legacy `user_id` remains where the active frontend queries or inserts owner-linked workflow
  data. It is a temporary compatibility field and is never an authorization boundary.
- `users.github_id` and `users.github_username` remain temporarily because active frontend
  onboarding, settings, jobs, and queries read them. No user-level GitHub or Twitter token or
  expiry column is retained.
- `social_accounts` remains only because active frontend queries still use it. It is isolated from
  backend domain APIs and must be migrated to encrypted `integration_connections`.
- Legacy frontend reads and writes of `github_raw_events` are not compatible with this baseline.
  Keep the legacy webhook as the sole authority until a coordinated cutover to restricted backend
  ingestion; do not expose raw bodies through a compatibility API or log them during cutover.
- Supabase `auth.uid()` RLS policies were not copied. FastAPI must authorize workspace membership
  server-side before any tenant read or write.

## Cutover checks

1. Reset only the intended fresh Supabase project and run `alembic upgrade head`.
2. Provision each user through OAuth so personal workspace and owner membership are created in
   the same transaction.
3. Update every retained frontend write to include `workspace_id`, while temporarily preserving
   matching `user_id` values.
4. Cut GitHub ingestion over atomically so one global delivery can create one consumer per
   authorized workspace; do not dual-write or enable a backend webhook before that rollout.
5. Migrate social credentials into encrypted `integration_connections`, verify delivery, then
   remove the `social_accounts` bridge in a later migration.
6. Compare the deployed schema to metadata with `alembic check`, verify row ownership, and run
   negative cross-workspace tests before enabling product writes.
