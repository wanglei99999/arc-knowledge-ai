# Frontend space authorization implementation report

Checkout: `E:/Coding/knowledge_base/arc-knowledge-web`, branch `feat/space-authorization`. No commits, pushes, or subagents. Backend files were not edited; this requested report is the only write outside the frontend checkout.

## Implemented

- Registration sends a new organization name. Login supports either token pairs or an in-memory tenant selection challenge; selection tokens never become resource access tokens. The selected membership is hydrated from `/me/context` before protected route rendering. The header lists `/me/tenants` and switches with `/auth/switch-tenant`.
- Removed tenant/user environment identity and `X-Tenant-Id` from HTTP/SSE. Chat runtime storage uses the authenticated tenant and membership. The HTTP refresh path is bounded to one retry and excludes auth endpoints, avoiding refresh recursion.
- Tenant switch/logout abort every identity-bound HTTP request and SSE stream, synchronously hide the old app, clear persisted identity/selected space/chat runtime, cancel chat runtime timers, and navigate to a fresh page. An aborted context stays aborted until that page load, so old callbacks/polls cannot issue requests under the new token. Logout still sends its refresh-token revocation request after resource cancellation.
- `/permissions` is linked from the header. It implements private/team/public space creation, team role selection, space rename/archive, effective role/source display, manager grant listing/add/update/revoke, and directory-backed group/member pickers. Public/TENANT sharing controls are admin-only. Sidebar creation also supports presets/groups; sidebar archive uses `space.manage`.
- Admin organization management includes adding registered users, changing membership roles/statuses, creating/editing/reparenting/archiving groups, and adding/removing group members. It exposes metadata-only space administration, reason-required explicit takeover, and recent audit records. Per root clarification, tenant admins can archive/restore metadata rows without content grants; no `space.read` is inferred.
- Documents require a selected space. Upload uses `document.upload`; delete/retry buttons use each document's capabilities. Added document retry API. List/chunk results arriving after a space change/unmount are ignored; visible results/drawers/upload tasks clear on space change. Polling stops when context disappears.
- Chat attachment selection/submission/recovery uses `document.upload`; viewer ordinary chat remains available with `space.read`. Recovery upload controls hide when unavailable. Clearing an inaccessible selected space clears visible chat messages/sessions and stops streams/timers. Search state clears on space change and stale responses are discarded.
- HTTP and SSE 403/404 content failures re-fetch accessible spaces. Revoked selected spaces are cleared; HTTP also replaces the page once selection becomes inaccessible. Missing capabilities fail closed in upload/manage UI.

## Verification evidence

Final full run (2026-09-07 02:40 local tool timestamp):

```
npm test
Test Files  19 passed (19)
Tests       116 passed (116)
exit 0

npm run build
vue-tsc && vite build
5568 modules transformed
built in 6.95s
exit 0

git -c core.safecrlf=false diff --check
exit 0, no output
```

Meaningful red-to-green regressions were observed for selection-token storage isolation, login membership hydration, removed selected spaces, viewer attachment staging, unscoped document listing, stale document responses, viewer file-turn creation, clearing inaccessible conversation state, request invalidation with logout revocation, hidden attachment recovery controls, forbidden SSE revalidation, and admin metadata restore without content capabilities. Existing test fixtures were updated only for the intentional authenticated-capability/header contract. New permissions page tests exercise ordinary-viewer versus tenant-admin behavior using the real page and stores, with external API responses mocked.

## Integration limits and review notes

- Unit/component tests and production typecheck/build are complete. No live backend/browser end-to-end run was performed by this agent; root should verify register/login with multiple memberships and real backend directory/grant/admin endpoints in the integrated environment.
- Space/document capability fields are optional in TypeScript to preserve compatibility with existing fixtures, but UI authorization checks deny absent capabilities.
- Existing dashboard demo metrics and its dev-reset UI were outside this implementation; the backend must continue enforcing administrator authorization on legacy admin endpoints. The `/admin` route and sidebar entry now require tenant admin.
- SSE retains the previous explicit error behavior for HTTP 401; it does not transparently refresh/replay a stream. Ordinary HTTP requests refresh once. A failed SSE request never emits completion.
- The manager archive recovery path remains the existing archived-chat settings flow; tenant administrators additionally have the metadata directory restore control.
- After editing one's own admin membership, a full page reload may be needed to refresh the local role if a subsequent admin-only refresh is denied; backend authorization remains authoritative.

No backend API changes were required beyond root's explicit admin metadata archive/restore clarification.

## Review follow-up: late stream and polling cancellation

Completed 2026-09-07, final verification at 10:12 local tool timestamp.

- `src/utils/sse.ts` now checks whether the identity signal is already aborted before reading auth state or starting a fetch. This closes the late callback path that could otherwise issue an old turn request with a newly saved tenant token.
- `src/stores/chat.ts` captures runtime generation and runtime object identity for `pollTurn` and `resumePendingTurns`. Both success and failure paths discard stale completions after reset/revocation. `resetVisibleContext` increments the generation before clearing runtimes. `schedulePoll` retrieves an existing runtime, never recreates a removed one, and checks identity/space/generation/object identity before scheduling and again when its timer fires.
- Five focused regressions were observed failing before these fixes: late SSE fetch after identity invalidation, poll success/error after space revocation, and pending-turn hydration success/error after space revocation. All now pass; error cases previously issued four requests after six seconds, and success cases previously repopulated messages and started an answer stream.
- Focused verification: 52 tests across `src/utils/sse.test.ts` and `src/stores/chat.test.ts`, all passing.
- Final complete suite: **121 tests across 19 files, all passing**. `npm run build` passed (`vue-tsc` plus Vite, 5568 modules, built in 7.42 seconds). `git -c core.safecrlf=false diff --check` passed with no output.
- This follow-up changed only those two production files, their two test files, and this report. No commits, pushes, or subagents.
