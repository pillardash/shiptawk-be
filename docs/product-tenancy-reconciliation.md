# Product Tenancy Reconciliation

The `20260719_0003` migration is intended only for fresh/local PostgreSQL databases derived
from this backend's Alembic history. It must not be run against production as written.

The legacy Supabase schema has owner-linked `products.user_id` rows but no workspaces. Its
`users` shape and authentication identifiers also differ from the backend boilerplate's
password-authenticated `users` table. No reliable production mapping can be inferred from
the repository alone.

Before production migration:

1. Reconcile backend user IDs with live Supabase/Auth identities and define rollback.
2. Define how legacy owners map to workspaces and active memberships.
3. Add `workspace_id` to the live products table using a staged nullable/backfill/validate/
   non-null rollout while preserving all product IDs and `user_id` values.
4. Compare live constraints, indexes, RLS policies, and dependent foreign keys with the
   model and generate a production-specific additive migration.
5. Verify counts and ownership with cross-workspace negative queries before enabling reads.

This slice performs no backfill and introduces no FastAPI write authority.
