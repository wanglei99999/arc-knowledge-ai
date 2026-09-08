# Frontend permission implementation contract

Work only in E:/Coding/knowledge_base/arc-knowledge-web (feature branch already created). Backend root agent owns implementation of this contract. No additional agents. No commits/push unless asked by root. Existing Vue/Pinia/AntD styling and APIs should be preserved where possible. Implement meaningful tests first and run targeted vitest, then full tests/build. User authorized implementation of complete permission feature, not merely a mockup.

## Auth
- POST /auth/register {email,password,tenant_name} creates user plus a new tenant; returns {user_id,email}; subsequent login selects its membership.
- POST /auth/login {email,password} => either {access_token,refresh_token,token_type} or {selection_token,memberships:[{tenant_id,tenant_name,membership_id,tenant_role}]}.
- POST /auth/switch-tenant {tenant_id}, Authorization selection_token OR current access_token => TokenPair. Do not publish/store selection_token as a resource access token.
- POST /auth/refresh {refresh_token} -> {access_token,token_type}; existing logout shape unchanged.
- GET /me/context => {user_id,email,tenant_id,membership_id,tenant_role,tenant_name}; GET /me/tenants => membership summaries array above. tenant_role TENANT_ADMIN or MEMBER.
- Remove X-Tenant-Id as identity source; no VITE_TENANT_ID auth bypass.
- Tenant switching/logout must cancel chat streams and clear/reset space/chat/archive/search/document state before rendering new context (full page navigation after token swap acceptable if secure and requests cannot repopulate stale state). Handle reload auth context and revoked access.

## Spaces and documents
- Existing GET /spaces array adds effective_role, capabilities:string[], visibility_summary ('public','team','private'), permission_sources:[{subject_type,group_id,tenant_membership_id,role}]. Keep space_id,space_key,name,status,created_by.
- POST /spaces {name,preset:'private'|'group'|'public',group_id?:string,group_role?:'VIEWER'|'EDITOR'} => SpaceOut. public only tenant admin.
- PATCH /spaces/{id} {name}; DELETE existing archive; POST /spaces/{id}/restore.
- GET /spaces/{id}/grants => list of {id,subject_type,group_id,tenant_membership_id,role,source,created_at}; PUT /spaces/{id}/grants {subject_type:'TENANT'|'GROUP'|'MEMBER',group_id?:string,tenant_membership_id?:string,role:'VIEWER'|'EDITOR'|'MANAGER'} => grant row; DELETE /spaces/{id}/grants/{grant_id} => 204.
- Space capabilities: space.read, space.manage, document.upload, document.delete_own, document.retry_own, document.delete_any, document.retry_any. Use backend capabilities, not your own role mapping. Tenant-admin-only control plane is separate.
- GET /documents requires space_id. Each existing item adds created_by_membership_id and capabilities ['document.read', optionally 'document.delete','document.retry']. POST /documents/{id}/retry added. VIEWER cannot upload chat attachments either; ordinary chat still allowed with space.read.
- On 403/404 re-fetch spaces and clear inaccessible selected space and its visible data. Prevent unhandled stale requests/polls repopulating UI.

## Organization/sharing administration
- GET /tenant/members => [{membership_id,user_id,email,role,status}], tenant admin only.
- GET /tenant/directory => {members:[{membership_id,email}],groups:[{id,name,parent_group_id,status}]}, active members may query minimal same-tenant sharing directory (no private contents).
- POST /tenant/members {email,role:'MEMBER'|'TENANT_ADMIN'} => membership; adds existing registered user to tenant, unknown email gives 404.
- PATCH /tenant/members/{membership_id} {role?:...,status?:'active'|'suspended'|'left'} => membership. Last admin ->409.
- GET /groups => [{id,name,parent_group_id,status}]; POST /groups {name,parent_group_id?:string}; PATCH /groups/{id} {name?:string,parent_group_id?:string|null}; DELETE /groups/{id} archives. Admin only. Cycles or nonempty-child archive ->409.
- GET /groups/{id}/members => [{membership_id,email}]; POST /groups/{id}/members {membership_id}; DELETE /groups/{id}/members/{membership_id}.
- GET /tenant/spaces => all space metadata (same shape basics as ordinary spaces, no content access implied).
- POST /tenant/spaces/{space_id}/takeover {reason:nonempty string} => {message}; explicit MANAGER grant with audit.
- GET /tenant/audit => recent [{id,action,resource_type,resource_id,metadata,created_at,actor_membership_id}].
- Sharing UI should use tenant/directory for pickers, grant list for sources; expose admin-only TENANT grant option.
- Add intuitive permission management page/nav, either extend current admin layout or new /permissions. Distinguish private space metadata visibility from content permission. Include name/preset/group creation UI in existing space creation experience.

Report changes, tests, concerns to E:/Coding/knowledge_base/arc-knowledge-ai/docs/superpowers/plans/frontend-report.md. Root will review and integrate. Read existing relevant code before changes.
