# Boilerplate Provenance

- Source repository: `/home/mrprotocoll/Documents/projects/fastapi-boilerplate`
- Source revision: `cd84bdbf1e87a53ff85a66698611903331bad5ee`
- Derived date: `2026-07-19`
- Synchronization policy: selective commits only

The source revision includes the reusable async SQLAlchemy conversion, PostgreSQL CI,
coverage enforcement, and associated documentation. Shiptawk product changes are kept
as a separate working-tree delta from that committed baseline.

The generic browser OAuth/session delta was applied selectively on 2026-07-19 while still
uncommitted in this product repository. Product-specific GitHub registration, OAuth-only policy,
workspace provisioning, and schema decisions remain Shiptawk-owned changes.

The generic single-file warning/error logging delta was applied selectively on 2026-09-19.

Future reusable changes should normally be implemented and verified in the boilerplate
first, then applied deliberately here. Product-specific changes must not be copied back.
