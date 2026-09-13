# Private Beta Instrumentation Coverage

The first-party `product_events` ledger is metadata-only. It stores tenant, actor, product,
bounded resource identifiers, schema version, retention flags, an idempotency key, and a
payload fingerprint. It does not store comments, generated bodies, evidence, URLs, query
strings, prompts, credentials, provider receipts, or arbitrary metadata.

## Server-Wired Events

- `product_created`: atomic with creation; keyed by product ID.
- `github_installation_attached`: workspace-scoped and atomic with the verified installation
  attachment; keyed by the retained connection and external installation identity. Replays do not
  create another event.
- `repository_monitoring_enabled`: workspace-scoped and emitted only for explicit `false` to
  `true` opt-in. A monotonic repository tracking generation distinguishes a future re-enable;
  discovery, sync, repeated enable requests, and disable requests do not emit it.
- `product_profile_approved`: atomic with approval; keyed by profile ID and approved version.
- `website_connected`: atomic with first source creation; keyed by website-source ID.
- `website_initial_crawl_completed`: atomic with the first successful crawl only; keyed by
  website-source ID and references the initial successful run.
- `search_console_connected`: atomic with connection/property persistence; keyed by connection
  and product IDs.
- `search_property_selected`: atomic with source selection and initial-sync creation; keyed by
  search-source ID.
- `weekly_report_generated`: atomic with canonical plan finalization; keyed by plan ID and exact
  plan revision. A finalized zero-action plan with explicit no-recommendation records is marked as
  a valid no-recommendation report.
- `recommendation_accepted` and `recommendation_dismissed`: atomic with the immutable decision
  and command receipt; keyed by recommendation and decision transition.
- `weekly_report_viewed`: written atomically by the canonical plan-view service. It includes
  the actor-scoped report ordinal and second/fourth-return flags.
- `recommendation_feedback_submitted`: written atomically when the current actor's usefulness
  feedback is replaced.
- `asset_edited`, `asset_approved`, and `asset_rejected`: atomic with the command receipt and
  exact prepared-asset revision; event resource revisions always identify the affected revision.
- `action_dismissed`, `action_started`, and `action_completed`: atomic with the canonical action
  transition and command receipt; keyed by action and transition.
- `measurement_completed`: atomic with a successful authoritative measurement comparison;
  unavailable or inconclusive collection attempts do not emit it.
- `weekly_email_delivered`: atomic with a confirmed delivered status; failed and uncertain
  provider outcomes do not emit it.
- `search_baseline_viewed`, `recommendation_detail_viewed`, `measurement_viewed`, and
  `compatibility_route_viewed`: accepted by the browser event endpoint after tenant and
  resource validation. Browser callers cannot submit domain-outcome events.

`weekly_report_viewed` is listed in the browser event enum for contract clarity but is rejected
by the generic event endpoint. The canonical plan-view endpoint is its only write authority.

All server events use `source=server` and metadata-only resource coordinates. Their semantic keys
are derived from persisted resource IDs, transitions, and revisions rather than caller-provided
idempotency keys. The event insert participates in the domain transaction, so rollback removes
both state and event and command/crawl/workflow replay does not create another event.

The ledger always requires a valid workspace. Only `github_installation_attached` and
`repository_monitoring_enabled` require `product_id = NULL`; every other event requires a product,
with the conditional product foreign key enforcing ownership when present. Workspace event writes
also verify the exact workspace-owned integration connection or tracked repository resource.
