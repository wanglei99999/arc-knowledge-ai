# Backend authorization implementation status

2026-09-07, feature branch feat/space-authorization; uncommitted working-tree changes. User authorized implementation and stated no effective existing data. No application database reset or migration has been executed; database verification used disposable random schemas only.

Implemented global users, tenant memberships, groups/ancestor grants, fixed capability policy, reason-bearing audited takeover and tenant metadata administration. JWT access/selection token purposes and DB membership binding are checked. Refresh cannot restore revoked identity. Registration transaction initializes new tenant/admin/public/private spaces; members can join multiple tenants and groups.

Resource services now check before files/storage/workflows/retrieval/cache/LLM; upload router preflights before MinIO writes. File actions distinguish own versus any creator. Session content/history/attachments/answer claims require both session ownership and current space read. Document listing requires space. Session lists filter allowed spaces before pagination/count. Chunk hydration and citation snapshots are tenant+space scoped. Semantic memory reads/writes include space; vectors use arc_memories_v4. Document deletion attribution and audit share the status transaction. Cache invalidation clears exact and vector entries for the affected space.

Legacy tenant admin routes require active tenant-admin identity, deny other-tenant IDs, return tenant-only stats, and reject global reset/model-pricing mutations. Runtime smoke registration no longer relies on X-Tenant-Id, and supports explicit tenant selection for multi-membership accounts.

## Verification so far

- TDD core authorization matrix: 11 initial tests failed before new service, passed after implementation.
- Resource denial regressions: list/delete/retry/status/download, search, session history/mutations, chat preflight/turn; denied operations never reach downstream IO.
- Real JWT HTTP regressions: 34 passed. Seven reproduced malformed/mismatched binding failures before fix; all pass after fix.
- Admin unauthenticated boundary: five failing endpoints reproduced, all rejected with 401 after fix.
- Cache scope and runtime smoke: two regressions reproduced before fix; all 10 covering tests pass after fix.
- Atomic document deletion/audit: two tests red before implementation, green after; audit error rolls back metadata transaction.
- Full unit+integration command: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-full --tb=short`.
- Real PostgreSQL verification completed against six disposable random-schema tests. They cover fresh/idempotent schema, typed tenant FKs, group inheritance/direction, grant revocation/last-manager/admin rollback, takeover/ownership, scoped chunk hydration, and rejection of cross-tenant document/session/message relationships. No `public` schema or application rows were initialized, reset or deleted.
- Third-party parser emits existing SWIG deprecation warnings in observability test; not treated as evidence of clean warning-free output.
- Latest full backend regression: **444 passed, 2 existing SWIG warnings** (43.17 s, exit 0), including the six live PostgreSQL tests. `git diff --check` passes. Frontend: **121 passed** (6.35 s), production typecheck/Vite build passes (6.84 s).
- Original orchestration tests now explicitly opt into a test-only allowed-actor fixture. Security tests do not use it. No production auth bypass added.

## Review focus / remaining limits

PostgreSQL authorization behavior is verified, but there is no production-readiness claim without MinIO/Milvus/Redis/Temporal/LLM live checks. There has been no browser end-to-end run against a live backend. Legacy broad reset is intentionally disabled, not replaced with a dangerous CLI workaround. Migration is fresh-only and refuses legacy user schema. Existing files/session workflows retain their established lifecycle semantics.
