# Space authorization implementation plan

**Goal:** Implement tenant membership, group relationships, space grants and consistent resource authorization across backend and frontend.
**Architecture:** PostgreSQL owns all relationships. Fixed capabilities live in a centralized AuthorizationService. No OpenFGA dependency, no permission cache, no historical data backfill.
**Stack:** FastAPI, async SQLAlchemy/PostgreSQL, Vue 3/Pinia/Ant Design.
**Spec:** ../specs/2026-09-04-tenant-group-space-permission-design.md; user amendment: no effective existing data, use fresh initialization.

## Constraints and decisions

- Preserve user changes; work on feat/space-authorization in both existing checkouts so current local dependencies remain usable. Do not push or reset data.
- Registration creates a new tenant with a generated ID and its first admin, public VIEWER space and private MANAGER space. It cannot claim membership by supplying a tenant header. Existing users join another tenant only through tenant-admin membership management.
- User email is globally normalized and unique. Access JWT binds sub, tenant_id, membership_id and type=access; validate all against active DB records on every request.
- Login with one membership returns TokenPair; multiple memberships returns selection_token + memberships (no access token). POST /auth/switch-tenant accepts a selection/access bearer plus {tenant_id} and returns a fresh TokenPair. Selection tokens cannot call resource APIs.
- GET /me/context: {user_id,email,tenant_id,membership_id,tenant_role,tenant_name}. GET /me/tenants: array {tenant_id,tenant_name,membership_id,tenant_role}.
- Tokens refreshed only while User/Tenant/Membership remain active. No X-Tenant-Id development fallback.
- Roles VIEWER, EDITOR, MANAGER; capabilities space.read, space.manage, document.upload, document.delete_own, document.retry_own, document.delete_any, document.retry_any. Each document returns capabilities document.read/document.delete/document.retry computed for its creator.
- Tenant admin is control-plane only. Public grants require tenant admin; other grants require space manager. Explicit reason-bearing takeover writes MANAGER and audit atomically.
- Grant changes and last-manager checks serialize with tenant/space locks; last tenant admin protected. Group changes reject cycles and cross-tenant relations.
- Files/derived data inherit space. Sessions additionally remain user-owned. Permission checks precede storage/LLM/cache calls. Lists filter before pagination and count. No cross-space memory or chunk hydration.

## Task 1: Schema and authorization core (root)

Files: scripts/migrate.py; app/domain/authorization.py; app/infrastructure/postgres/repositories/authorization_repo.py; app/services/authorization_service.py; tests/unit/services/test_authorization_service.py.
- [x] Add negative and positive role/owner/membership tests; run to observe failing behavior.
- [x] Implement active-membership resolution, ancestor group CTE, filtered grants and list access in one shared query. Keep typed composite FKs.
- [x] Add fresh versioned schema initialization without destructive resets or legacy backfill.
- [x] Verify policy and schema constraints against isolated test data.

## Task 2: Identity and management (root)

Files: app/domain/user.py; user_repo.py; auth_service.py; api/dependencies.py; api/routers/auth.py; api/routers/access.py; app/main.py.
- [x] Test missing/expired/selection tokens and inactive contexts, registration and multi-tenant selection.
- [x] Implement global identities, transactional initialization, refresh validation and context routes.
- [x] Implement tenant members/groups, space grants and reason-bearing takeover with audit.
- [x] Cover last-admin/manager protection and ancestor changes.

## Task 3: Frontend contract and management (delegated)

Files: frontend src/api, src/stores/auth.ts, spaces.ts, views/document/index.vue, views/auth, components/layout, components/chat/ChatInput.vue; new views/permissions/index.vue and tests.
- [x] Update auth/context/tenant selection and clear tenant-scoped stores on switches/logout.
- [x] Display capabilities, grant sources, creation presets and owner-limited document actions.
- [x] Implement admin members/groups and space sharing/takeover using the API contract in frontend-task.md.
- [x] Run targeted failing tests, implement, run vitest and vue-tsc/build. Do not modify backend or spawn agents.

## Task 4: Resource enforcement (root)

Files: space/document/session/chat/chat_turn/retrieval services and routers; chunk/session/memory repositories; memory manager/extractor/Milvus collection.
- [x] Test unauthorized requests cause no content reads, uploads, workflow execution, retrieval or cache access.
- [x] Enforce authorization at service entry; add router preflight for streaming and upload side effects.
- [x] Filter all lists before pagination, scope chunk hydration and memory to requested space, protect history/citations.
- [x] Verify authorized processing and document creator propagation through both upload paths.

## Task 5: Review and handoff (root + reviewer)

- [x] Run backend relevant/full unit tests, isolated PostgreSQL authorization integration tests, frontend tests/build.
- [x] Review combined changes for missing permission paths, inconsistent contracts and unsafe initialization.
- [x] Update spec fresh-initialization amendment and report checks and actual runtime limitations.

## Progress

- Baseline: both repos clean before edits. Git uses command-scoped safe.directory due sandbox account ownership. No global Git changes.
- No historical data operations authorized or needed; schema changes will not silently drop an existing schema.
- 2026-09-07: Schema/core, identity/admin UI and resource enforcement implemented in working trees. Full backend suite passes 444 tests, including six real PostgreSQL tests using disposable random schemas; no application schema or data was reset. Frontend passes 121 tests and production build.
- Frontend task and fix round complete: 121 component/unit tests and production typecheck/build pass (frontend-report.md). Review found already-aborted SSE and late polling callbacks; five regression tests added for fixes.
- Ruling: Keep existing feature-branch checkouts and do not commit/push or delete any worktree — preserves the shared workspace and installed dependencies; changes remain reviewable but are not integrated into main/master.
- Ruling: Disable legacy global reset/model-pricing writes via tenant APIs; tenant admins may only manage their own tenant — a tenant role cannot authorize cross-company destructive/global changes; old admin buttons receive 403 until replaced with operator tooling.
- Ruling: Use a new arc_memories_v4 Milvus collection — avoids silently reusing unscoped memory vectors; old unused collection remains untouched.
- Ruling: Access-token identity endpoints validate current membership as well; revoked members must log in again to select another tenant — closes malformed binding bypass while retaining short-lived tenant-selection tokens.
- Ruling: Shared resource tests explicitly stub the authorization boundary only when testing pre-existing workflow orchestration; separate permission and JWT regressions exercise real service policy with fake persistence. Live PostgreSQL tests verify schema and SQL authorization semantics in isolated schemas.
