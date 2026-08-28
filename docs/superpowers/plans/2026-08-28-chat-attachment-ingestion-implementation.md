# Chat Attachment Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build durable chat attachment turns that enter the conversation immediately, wait for permanent knowledge-base ingestion, and automatically answer from only the successfully indexed attachments.

**Architecture:** Persist the user turn and attachment placeholders before uploading. Derive readiness from attachment upload state plus `documents.status`, then let an idempotent SSE answer endpoint claim the turn and run document-scoped hybrid retrieval. The Vue store owns optimistic UI, upload progress, polling, recovery, and the existing streaming presentation.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async SQL, PostgreSQL, Temporal, MinIO, Milvus, Elasticsearch, pytest; Vue 3, Pinia, TypeScript, Axios, SSE, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-28-chat-attachment-ingestion-design.md`

## Global Constraints

- Chat attachments are permanent documents in the selected workspace.
- An attachment turn retrieves only its successfully indexed, non-ignored attachments.
- A later turn without attachments uses the existing whole-workspace chat path.
- A busy session allows draft editing but forbids submitting another message.
- The server derives trusted document IDs from attachment relations; clients never choose retrieval document IDs.
- All ignored attachments produce `empty`, never a silent fallback to whole-workspace retrieval.
- Maximum attachments per turn defaults to 5 and is validated server-side.
- Attachment turns require a non-blank question; server-configured MIME and file-size limits are authoritative.
- Existing `/documents/upload` and ordinary `/chat` behavior must remain compatible.
- Do not run `git add`, `git commit`, `git reset`, change Git identity, or perform any other state-changing Git operation. Git commits are exclusively performed by the user when explicitly requested.
- Preserve all pre-existing uncommitted changes in both repositories.

---

## File Structure

### Backend: `arc-knowledge-ai`

- Create `app/domain/chat_turn.py`: attachment/turn enums, DTOs, readiness calculation.
- Create `app/infrastructure/postgres/repositories/chat_turn_repo.py`: transactional turn creation, attachment relations, answer claim, status mutation.
- Create `app/services/chat_turn_service.py`: authorization-aware orchestration for upload, retry, ignore, cancel, status and answer.
- Create `app/api/routers/chat_turn.py`: HTTP and SSE contracts.
- Modify `app/domain/memory.py`: additive processing fields on `Message`.
- Modify `app/domain/retrieval.py`: `document_ids` retrieval scope.
- Modify `app/infrastructure/milvus/client.py`: document ID terms in vector filter.
- Modify `app/infrastructure/elasticsearch/client.py`: document ID terms in BM25 filter.
- Modify both retrieval search stages and `app/workflows/rag_orchestrator.py`: propagate document scope and bypass incompatible cache entries.
- Modify `app/services/chat_service.py`: answer an already-persisted user turn, omit semantic memories for attachment-scoped answers, synchronously persist the assistant result and citations.
- Create `app/infrastructure/postgres/repositories/answer_repo.py`: atomically persist an assistant message, citations and session message count.
- Modify `app/memory/assembler.py`: strict attachment-evidence prompt mode.
- Modify `app/infrastructure/postgres/repositories/session_repo.py`: map new message fields and allow a caller-supplied message ID where required.
- Modify `app/services/session_service.py` and `app/api/routers/session.py`: return attachments and processing state with history.
- Modify `app/services/document_service.py`: safe retry of failed ingestion using the existing MinIO object.
- Modify `app/main.py`: register the chat-turn router.
- Modify `scripts/migrate.py`: additive schema migration.

### Frontend: `arc-knowledge-web`

- Create `src/components/chat/DraftAttachmentChip.vue`: local, removable file before send.
- Create `src/components/chat/MessageAttachmentCard.vue`: persistent upload/ingestion/success/failure UI.
- Modify `src/components/chat/ChatInput.vue`: select local files and emit `{content, files}` without starting ingestion.
- Modify `src/components/chat/MessageBubble.vue`: render persistent attachments and turn actions.
- Modify `src/types/chat.ts`: attachment, readiness and processing types.
- Modify `src/api/chat.ts`: turn CRUD/upload/status/action clients.
- Modify `src/utils/sse.ts`: reusable stream parser and turn-answer SSE.
- Modify `src/stores/chat.ts`: optimistic turns, uploads, polling, auto-answer, recovery and session lock.
- Modify `src/views/chat/index.vue`: new send payload and draft-preserving busy state.
- Add `vitest.config.ts` and focused store/state tests; update `package.json` only for the test runner.

---

### Task 1: Schema, Domain States, and Readiness Calculation

**Files:**
- Create: `app/domain/chat_turn.py`
- Modify: `app/domain/memory.py`
- Modify: `scripts/migrate.py`
- Test: `tests/unit/domain/test_chat_turn.py`

**Interfaces:**
- Produces `AttachmentStatus`, `TurnReadiness`, `TurnProcessingStatus`, `AttachmentDeclaration`, `AttachmentView`, `AssistantAnswerView`, `ChatTurnView`, and `compute_readiness()`.
- Later tasks consume `compute_readiness(attachments: Sequence[AttachmentView]) -> TurnReadiness`.

- [ ] **Step 1: Write failing readiness tests**

```python
from app.domain.chat_turn import AttachmentStatus, AttachmentView, TurnReadiness, compute_readiness


def attachment(status: AttachmentStatus, ignored: bool = False) -> AttachmentView:
    return AttachmentView(
        attachment_id="a1", client_id="c1", file_name="x.pdf",
        mime_type="application/pdf", file_size=10, document_id=None,
        status=status, ignored=ignored, error_message=None,
    )


def test_readiness_is_ingesting_while_any_attachment_is_pending():
    assert compute_readiness([
        attachment(AttachmentStatus.INDEXED),
        attachment(AttachmentStatus.INGESTING),
    ]) is TurnReadiness.INGESTING


def test_readiness_is_blocked_for_non_ignored_failure():
    assert compute_readiness([attachment(AttachmentStatus.FAILED)]) is TurnReadiness.BLOCKED


def test_readiness_is_ready_when_all_active_attachments_are_indexed():
    assert compute_readiness([
        attachment(AttachmentStatus.INDEXED),
        attachment(AttachmentStatus.FAILED, ignored=True),
    ]) is TurnReadiness.READY


def test_readiness_is_empty_when_every_attachment_is_ignored():
    assert compute_readiness([
        attachment(AttachmentStatus.FAILED, ignored=True),
    ]) is TurnReadiness.EMPTY
```

- [ ] **Step 2: Run the tests and confirm import failure**

Run:

```bash
cd /mnt/e/Coding/knowledge_base/arc-knowledge-ai
.venv/bin/pytest tests/unit/domain/test_chat_turn.py -v
```

Expected: collection fails because `app.domain.chat_turn` does not exist.

- [ ] **Step 3: Implement the domain state module**

Use string enums with these exact values:

```python
class AttachmentStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    INGESTING = "ingesting"
    INDEXED = "indexed"
    FAILED = "failed"
    IGNORED = "ignored"


class TurnReadiness(StrEnum):
    INGESTING = "ingesting"
    BLOCKED = "blocked"
    READY = "ready"
    EMPTY = "empty"


class TurnProcessingStatus(StrEnum):
    WAITING_FILES = "waiting_files"
    ANSWERING = "answering"
    COMPLETED = "completed"
    ANSWER_FAILED = "answer_failed"
    CANCELLED = "cancelled"
```

`compute_readiness()` must ignore ignored attachments, return `EMPTY` when no active attachments remain, return `BLOCKED` before `INGESTING` when any active attachment failed, and return `READY` only when every active attachment is indexed.

Define immutable domain DTOs with these fields:

```python
@dataclass(frozen=True)
class AttachmentDeclaration:
    client_id: str
    file_name: str
    mime_type: str
    file_size: int


@dataclass(frozen=True)
class AssistantAnswerView:
    message_id: str
    content: str
    citations: list[dict]


@dataclass(frozen=True)
class ChatTurnView:
    turn_id: str
    session_id: str
    space_id: str
    query: str
    readiness: TurnReadiness
    processing_status: TurnProcessingStatus
    processing_error: str | None
    attachments: list[AttachmentView]
    assistant: AssistantAnswerView | None
```

- [ ] **Step 4: Extend Message without breaking history**

Add nullable fields with defaults:

```python
processing_status: str | None = None
processing_error: str | None = None
client_request_id: str | None = None
```

Update `_row_to_message()` in `session_repo.py` later in Task 2; existing constructors remain valid because defaults are nullable.

- [ ] **Step 5: Add additive migration DDL**

In `scripts/migrate.py`, add three `ALTER TABLE messages ADD COLUMN IF NOT EXISTS` statements, the partial unique index on `(tenant_id, user_id, client_request_id)`, and the full `message_attachments` table from the approved spec. Use `upload_status` values `pending`, `uploaded`, and `failed`; use `ignored BOOLEAN NOT NULL DEFAULT FALSE`.

- [ ] **Step 6: Run focused and migration syntax checks**

```bash
.venv/bin/pytest tests/unit/domain/test_chat_turn.py -v
.venv/bin/python -m py_compile app/domain/chat_turn.py scripts/migrate.py
```

Expected: all tests pass and compilation exits 0.

- [ ] **Step 7: User checkpoint (no Git operation)**

Report created/modified files, test counts, and the exact DDL added. Stop if migration changes any existing column destructively.

---

### Task 2: Turn Repository and Transactional Idempotency

**Files:**
- Create: `app/infrastructure/postgres/repositories/chat_turn_repo.py`
- Modify: `app/infrastructure/postgres/repositories/session_repo.py`
- Test: `tests/unit/infrastructure/test_chat_turn_repo.py`

**Interfaces:**
- Consumes Task 1 enums and DTOs.
- Produces `ChatTurnRepository.create_turn()`, `get_turn()`, `link_document()`, `mark_upload_failed()`, `set_ignored()`, `cancel_turn()`, `claim_answer()`, and `fail_answer()`.

- [ ] **Step 1: Write repository contract tests with a fake DB context**

Cover these exact behaviors in named tests:

- `test_create_turn_reuses_client_request_id`
- `test_get_turn_rejects_other_tenant_or_user`
- `test_link_document_is_idempotent`
- `test_claim_answer_updates_only_waiting_or_failed_turn`
- `test_cancel_rejects_answering_or_completed_turn`
- `test_session_message_queries_require_tenant_and_user`

Capture SQL and parameters by monkeypatching `get_session` with the same async context-manager pattern used elsewhere. Assert every select/update contains `tenant_id` and, for turn ownership, `user_id` through the joined session/message relation.

- [ ] **Step 2: Run and confirm the repository is missing**

```bash
.venv/bin/pytest tests/unit/infrastructure/test_chat_turn_repo.py -v
```

Expected: import failure for `ChatTurnRepository`.

- [ ] **Step 3: Implement create_turn as one transaction**

Use this public signature:

```python
async def create_turn(
    self,
    *,
    client_request_id: str,
    session_id: str,
    tenant_id: str,
    user_id: str,
    space_id: str,
    query: str,
    attachments: list[AttachmentDeclaration],
) -> ChatTurnView:
```

Within one DB session:

1. Lock/validate the session by session, tenant, user and space.
2. Return the existing turn for the same client request ID.
3. Insert the user message with `processing_status='waiting_files'`.
4. Insert all attachment placeholders using a batch execute.
5. Return a hydrated view.

- [ ] **Step 4: Implement status hydration and state mutations**

The hydration query must left join `message_attachments` to `documents`. Map status according to the spec instead of copying document state into the attachment table. `claim_answer()` must use a conditional update and return false when another request already claimed the turn:

```sql
UPDATE messages
SET processing_status = 'answering', processing_error = NULL
WHERE message_id = :turn_id
  AND tenant_id = :tenant_id
  AND processing_status IN ('waiting_files', 'answer_failed')
RETURNING message_id
```

The service will lock and recheck readiness before calling this update.

- [ ] **Step 5: Update SessionRepository message mapping**

Select the three new message columns in all message queries and map them through `_row_to_message()`. Existing non-attachment messages must return `None` for these fields. Although the existing message methods already accept `user_id`, add `AND user_id = :user_id` to every message query and pass the parameter; the new test must fail if the user predicate is omitted.

- [ ] **Step 6: Run repository and existing memory tests**

```bash
.venv/bin/pytest tests/unit/infrastructure/test_chat_turn_repo.py tests/unit/services -v
```

Expected: new repository tests and existing service tests pass.

- [ ] **Step 7: User checkpoint (no Git operation)**

Provide the repository public method list and show that no staged or committed Git change was made.

---

### Task 3: Chat Turn Creation, Upload, Retry, Ignore, Add, and Cancel APIs

**Files:**
- Create: `app/services/chat_turn_service.py`
- Create: `app/api/routers/chat_turn.py`
- Modify: `app/services/document_service.py`
- Modify: `app/infrastructure/postgres/repositories/chunk_repo.py`
- Modify: `app/main.py`
- Test: `tests/unit/services/test_chat_turn_service.py`
- Test: `tests/unit/api/test_chat_turn_api.py`

**Interfaces:**
- Produces the non-answer endpoints under `/chat/turns`.
- Produces `DocumentService.retry_ingestion(document_id, tenant_id) -> IngestResult`.

- [ ] **Step 1: Write service tests for authorization and state transitions**

Tests must prove these named cases:

- `test_create_rejects_more_than_five_attachments`
- `test_create_rejects_blank_question`
- `test_create_rejects_disallowed_mime_or_declared_size`
- `test_upload_rejects_attachment_from_other_user`
- `test_upload_rejects_actual_name_mime_or_size_mismatch`
- `test_upload_reuses_existing_document_link`
- `test_ignore_changes_readiness_without_deleting_document`
- `test_add_attachment_reopens_empty_turn`
- `test_cancel_unlocks_waiting_turn`
- `test_retry_cleans_partial_indexes_before_new_workflow`

Use fakes for MinIO, Temporal, Milvus, Elasticsearch and repositories; unit tests must make no network calls.

- [ ] **Step 2: Run and confirm missing service/router failures**

```bash
.venv/bin/pytest tests/unit/services/test_chat_turn_service.py tests/unit/api/test_chat_turn_api.py -v
```

- [ ] **Step 3: Implement request/response Pydantic models and routes**

Expose these exact endpoints:

```text
POST  /chat/turns
PUT   /chat/turns/{turn_id}/attachments/{attachment_id}
GET   /chat/turns/{turn_id}
POST  /chat/turns/{turn_id}/attachments
POST  /chat/turns/{turn_id}/attachments/{attachment_id}/retry
PATCH /chat/turns/{turn_id}/attachments/{attachment_id}
POST  /chat/turns/{turn_id}/cancel
```

All endpoints use `require_user`. Create/upload return 201/202; ownership misses return 404 rather than exposing foreign IDs; invalid state transitions return 409.

- [ ] **Step 4: Implement upload orchestration**

`upload_attachment()` must:

1. Validate ownership and the declared attachment.
2. Read and validate actual bytes/MIME/name.
3. Build a MinIO key with the turn's trusted tenant/space.
4. Upload, create the document, link the attachment, then start ingestion.
5. Record `upload_status='failed'` and a safe error string if upload fails.

Do not accept `space_id` or `document_id` from this upload request.

- [ ] **Step 5: Implement safe ingestion retry**

Add `ChunkRepository.reset_failed_document()` to set `status='pending'`, `chunk_count=0`, and `error_message=NULL` for the matching failed tenant document. Before starting a new unique Temporal workflow run, call the existing delete-by-document functions for Milvus and Elasticsearch and delete PostgreSQL chunks. Reuse the existing MinIO object; never delete it during retry.

- [ ] **Step 6: Register the router and run API tests**

```bash
.venv/bin/pytest tests/unit/services/test_chat_turn_service.py tests/unit/api/test_chat_turn_api.py -v
.venv/bin/python -m py_compile app/api/routers/chat_turn.py app/services/chat_turn_service.py
```

Expected: all focused tests pass.

- [ ] **Step 7: User checkpoint (no Git operation)**

List route status codes, authorization checks and retry cleanup order.

---

### Task 4: Document-Scoped Hybrid Retrieval

**Files:**
- Modify: `app/domain/retrieval.py`
- Modify: `app/infrastructure/milvus/client.py`
- Modify: `app/infrastructure/elasticsearch/client.py`
- Modify: `app/pipeline/stages/retrieval/vector_search_stage.py`
- Modify: `app/pipeline/stages/retrieval/keyword_search_stage.py`
- Modify: `app/workflows/rag_orchestrator.py`
- Test: `tests/unit/infrastructure/test_document_scope_filters.py`
- Test: `tests/unit/stages/test_search_stage_document_scope.py`
- Test: `tests/unit/workflows/test_rag_orchestrator_document_scope.py`

**Interfaces:**
- Adds `document_ids: list[str]` to `RetrievalQuery` and `RAGOrchestrator.retrieve()`.
- Adds optional `document_ids` to `search_vectors()` and `bm25_search()`.

- [ ] **Step 1: Write failing pure filter tests**

```python
def test_milvus_filter_includes_document_scope():
    expr = _build_search_filter("t1", "s1", document_ids=[DOC1, DOC2])
    assert 'document_id in [' in expr
    assert DOC1 in expr and DOC2 in expr


def test_es_filter_includes_document_scope():
    assert {"terms": {"document_id": [DOC1, DOC2]}} in _build_search_filters(
        "t1", "s1", document_ids=[DOC1, DOC2]
    )
```

Also assert no document clause is added for `None` or an empty list.

- [ ] **Step 2: Run the focused tests and confirm signature failures**

```bash
.venv/bin/pytest tests/unit/infrastructure/test_document_scope_filters.py -v
```

- [ ] **Step 3: Implement safe UUID-normalized filters**

Normalize every document ID with `uuid.UUID`, return canonical strings, and reject invalid values before building Milvus expressions. Keep tenant and space conditions mandatory. Add ES `terms` beside those mandatory conditions.

- [ ] **Step 4: Propagate document_ids through both search stages**

Pass `query.document_ids` into both infrastructure calls. Preserve current metadata filters so document scope and metadata conditions can coexist.

- [ ] **Step 5: Propagate through the orchestrator and cache rules**

Add this parameter:

```python
document_ids: list[str] | None = None
```

Set `RetrievalQuery.document_ids=document_ids or []`. In `chat()`, semantic cache is usable only when there is no history, no metadata filter, and no document scope:

```python
use_cache = not history and not metadata_filters and not document_ids
```

- [ ] **Step 6: Run retrieval regression tests**

```bash
.venv/bin/pytest \
  tests/unit/infrastructure/test_document_scope_filters.py \
  tests/unit/stages/test_search_stage_document_scope.py \
  tests/unit/workflows/test_rag_orchestrator_document_scope.py \
  tests/unit/stages/test_search_stage_metadata_filters.py -v
```

Expected: document and metadata scope tests all pass.

- [ ] **Step 7: User checkpoint (no Git operation)**

Show the generated Milvus and ES filters using fake UUIDs and confirm clients cannot submit this scope directly.

---

### Task 5: Idempotent Attachment Answer and Synchronous Critical Persistence

**Files:**
- Modify: `app/services/chat_service.py`
- Modify: `app/memory/extractor.py`
- Modify: `app/memory/assembler.py`
- Create: `app/infrastructure/postgres/repositories/answer_repo.py`
- Modify: `app/infrastructure/postgres/repositories/session_repo.py`
- Modify: `app/api/routers/chat_turn.py`
- Test: `tests/unit/services/test_attachment_answer.py`
- Test: `tests/unit/infrastructure/test_answer_repo.py`

**Interfaces:**
- Produces `ChatTurnService.stream_answer(turn_id, tenant_id, user_id) -> AsyncIterator[str | list]`.
- Produces `MemoryExtractor.persist_answer() -> Message`; background `run_post_persist()` performs compression/extraction only.
- Produces `AnswerRepository.save_assistant_with_citations() -> Message`, which writes the assistant row, session count, citation rows and the source turn's `completed` status in one database transaction.

- [ ] **Step 1: Write failing answer lifecycle tests**

Cover these named cases:

- `test_answer_rejects_ingesting_or_empty_turn`
- `test_answer_claim_allows_only_one_concurrent_request`
- `test_answer_uses_only_repository_document_ids`
- `test_attachment_answer_does_not_save_user_message_twice`
- `test_attachment_answer_omits_semantic_memory_lookup`
- `test_attachment_answer_uses_strict_source_prompt`
- `test_done_is_emitted_after_assistant_and_citations_are_saved`
- `test_stream_failure_marks_answer_failed`
- `test_stream_cancellation_marks_answer_failed_and_reraises`
- `test_persist_answer_rolls_back_assistant_when_citation_insert_fails`

Use a fake async token generator and an event list to assert persistence occurs before the SSE wrapper receives completion.

- [ ] **Step 2: Run tests and confirm missing lifecycle behavior**

```bash
.venv/bin/pytest tests/unit/services/test_attachment_answer.py -v
```

- [ ] **Step 3: Make assistant and citation persistence atomic**

Implement `AnswerRepository.save_assistant_with_citations()` using one `async with get_db()` block. Within that transaction, insert the assistant message, batch-insert its citation rows, increment the session message count, and conditionally change the source user turn from `answering` to `completed`. Let a citation insert or conditional status-update failure escape so the context manager rolls back every operation. Do not call `SessionRepository.add_message()` or `CitationRepository.save()` from this method because each opens its own transaction.

- [ ] **Step 4: Split critical persistence from background memory work**

Implement:

```python
async def persist_answer(
    self, *, session_id: str, tenant_id: str, user_id: str,
    assistant_response: str, space_id: str | None,
    citations: list[dict],
) -> Message:
```

It delegates the atomic write to `AnswerRepository` and returns only after the assistant message and citations are durable. Move compression and semantic memory extraction into a separate background method that receives the already-saved assistant message; do not save it again.

- [ ] **Step 5: Add an existing-user-message path to ChatService**

For attachment turns:

1. Do not call `_manager.save_user_message()`.
2. Load working memory, where the pre-saved user message is already present.
3. Do not call `load_semantic_memories()`.
4. Retrieve with trusted `document_ids`.
5. Call `ContextAssembler.build` with `strict_sources=True` so its system prompt says that conclusions may use only the supplied attachment excerpts, must state when evidence is insufficient, and must not fill document facts from model knowledge.
6. Stream tokens and collect citations.
7. Await `persist_answer()`.
8. After atomic answer persistence has also marked the turn completed, schedule only non-critical memory work.

Keep ordinary `_stream_with_memory()` behavior unchanged except for reusing the new persistence primitive safely.

- [ ] **Step 6: Add `/answer` SSE route and failure state handling**

The route calls `stream_answer()` and uses the existing JSON SSE shape. Catch ordinary generation errors, mark `answer_failed` with a safe client-facing message, and emit the safe SSE error. Catch `asyncio.CancelledError` separately, mark `answer_failed`, then re-raise cancellation. A repeated call while `answering` returns 409; a completed turn returns 409 with an explicit already-completed detail.

- [ ] **Step 7: Run answer and ordinary chat regressions**

```bash
.venv/bin/pytest tests/unit/infrastructure/test_answer_repo.py tests/unit/services/test_attachment_answer.py tests/unit/services tests/unit/workflows -v
```

Expected: attachment lifecycle tests and existing chat/workflow tests pass.

- [ ] **Step 8: User checkpoint (no Git operation)**

Report exactly when the assistant row, citations, processing status and `[DONE]` are produced.

---

### Task 6: Session History Hydration and Backend Integration Contract

**Files:**
- Modify: `app/services/session_service.py`
- Modify: `app/api/routers/session.py`
- Modify: `app/infrastructure/postgres/repositories/citation_repo.py`
- Test: `tests/unit/api/test_session_attachments.py`
- Create: `tests/integration/conftest.py`
- Test: `tests/integration/test_chat_turn_flow.py`

**Interfaces:**
- Extends `MessageOut` additively with `processing_status`, `processing_error`, and `attachments`.
- No-attachment historical messages return `attachments=[]`.

- [ ] **Step 1: Write response-shape tests**

Assert a mixed session returns ordinary messages unchanged and attachment turns with their hydrated attachment state. Verify citations remain attached only to assistant messages.

- [ ] **Step 2: Run and confirm missing fields**

```bash
.venv/bin/pytest tests/unit/api/test_session_attachments.py -v
```

- [ ] **Step 3: Batch-load attachments to avoid N+1**

Add a repository method:

```python
async def get_by_message_ids(
    self, message_ids: list[str], tenant_id: str
) -> dict[str, list[AttachmentView]]:
```

`SessionService.get_messages()` should fetch messages once, citations once, and attachments once. Change `CitationRepository.get_by_message_ids()` to require `tenant_id` and include it in the SQL predicate. The router assembles the additive response and no repository lookup may rely on message IDs alone.

- [ ] **Step 4: Add an integration flow with faked external providers**

The integration test must exercise:

```text
create turn -> upload/link fake document -> pending status
document becomes indexed -> readiness ready
claim answer -> fake scoped retrieval -> assistant persisted -> completed
reload session -> attachments + assistant + citations present
```

Create a `chat_turn_test_app` fixture in `tests/integration/conftest.py`. It must build a small FastAPI app, include the chat-turn and session routers, override `require_user` with a fixed tenant/user, and replace the routers' service objects with stateful in-memory fakes for turn storage, document ingestion, scoped retrieval and answer persistence. Drive the app with `httpx.AsyncClient` and `ASGITransport`. The fixture must not import the production lifespan and must make no PostgreSQL, MinerU, DeepSeek, Milvus, Elasticsearch, MinIO or Temporal connection.

- [ ] **Step 5: Run backend feature suite**

```bash
.venv/bin/pytest \
  tests/unit/domain/test_chat_turn.py \
  tests/unit/infrastructure/test_chat_turn_repo.py \
  tests/unit/services/test_chat_turn_service.py \
  tests/unit/services/test_attachment_answer.py \
  tests/unit/api/test_chat_turn_api.py \
  tests/unit/api/test_session_attachments.py -v
```

- [ ] **Step 6: Run full backend quality gates**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check app tests
```

Expected: zero pytest failures and zero Ruff errors introduced by the feature.

- [ ] **Step 7: User checkpoint (no Git operation)**

Provide test totals and any pre-existing unrelated failures separately; do not conceal them or modify unrelated files.

---

### Task 7: Frontend Types, API Client, SSE Reuse, and Test Harness

**Files:**
- Modify: `src/types/chat.ts`
- Modify: `src/api/chat.ts`
- Modify: `src/utils/sse.ts`
- Modify: `package.json`
- Create: `vitest.config.ts`
- Test: `src/api/chat.test.ts`
- Test: `src/utils/sse.test.ts`

**Interfaces:**
- Produces `AttachmentVO`, `TurnReadiness`, `TurnProcessingStatus`, `ChatTurnVO`, and turn API functions.
- Produces `streamTurnAnswer(turnId, callbacks)` using the common SSE parser.

- [ ] **Step 1: Add the test runner and failing API/SSE tests**

Add dev dependencies `vitest`, `@vue/test-utils`, and `jsdom`; add script:

```json
"test": "vitest run"
```

Tests assert route paths, multipart payloads, and that `{error: "safe message"}` SSE events invoke `onError` instead of silently completing.

- [ ] **Step 2: Run tests and confirm missing APIs**

```bash
cd /mnt/e/Coding/knowledge_base/arc-knowledge-web
npm run test -- --run src/api/chat.test.ts src/utils/sse.test.ts
```

- [ ] **Step 3: Define exact frontend state types**

```ts
export type AttachmentStatus = 'pending_upload' | 'uploading' | 'ingesting' | 'indexed' | 'failed' | 'ignored'
export type TurnReadiness = 'ingesting' | 'blocked' | 'ready' | 'empty'
export type TurnProcessingStatus = 'waiting_files' | 'answering' | 'completed' | 'answer_failed' | 'cancelled'

export interface AttachmentVO {
  attachment_id: string
  client_id: string
  document_id: string | null
  file_name: string
  mime_type: string
  file_size: number
  status: AttachmentStatus
  ignored: boolean
  error_message: string | null
  progress?: number
}

export interface ChatTurnVO {
  turn_id: string
  session_id: string
  space_id: string
  query: string
  readiness: TurnReadiness
  processing_status: TurnProcessingStatus
  processing_error: string | null
  attachments: AttachmentVO[]
  assistant: MessageVO | null
}

export interface CreateChatTurnPayload {
  client_request_id: string
  session_id: string
  query: string
  attachments: Array<Pick<AttachmentVO, 'client_id' | 'file_name' | 'mime_type' | 'file_size'>>
}
```

Extend `MessageVO` with `processing_status?`, `processing_error?`, and `attachments?: AttachmentVO[]`.

- [ ] **Step 4: Implement typed API functions**

Provide:

```ts
createChatTurn(payload: CreateChatTurnPayload): Promise<ChatTurnVO>
uploadTurnAttachment(turnId, attachmentId, file, onProgress): Promise<ChatTurnVO>
getChatTurn(turnId): Promise<ChatTurnVO>
addTurnAttachment(turnId, declaration): Promise<AttachmentVO>
retryTurnAttachment(turnId, attachmentId): Promise<ChatTurnVO>
ignoreTurnAttachment(turnId, attachmentId): Promise<ChatTurnVO>
cancelChatTurn(turnId): Promise<ChatTurnVO>
```

- [ ] **Step 5: Refactor SSE parsing once**

Extract an internal `consumeChatStream(url, body, callbacks)` used by both ordinary `/chat` and `/chat/turns/{id}/answer`. Explicitly recognize `json.error` and call `onError(new Error(json.error))`; do not wait for `[DONE]` after an error event.

- [ ] **Step 6: Run tests and TypeScript build**

```bash
npm run test
npm run build
```

Expected: tests pass and Vue TypeScript build exits 0.

- [ ] **Step 7: User checkpoint (no Git operation)**

Report dependency additions and the stable TypeScript interfaces consumed by the store.

---

### Task 8: Pinia Turn Orchestration, Polling, and Recovery

**Files:**
- Modify: `src/stores/chat.ts`
- Test: `src/stores/chat.test.ts`

**Interfaces:**
- Consumes Task 7 APIs/types.
- Produces `pendingFiles`, `activeTurn`, `sessionBusy`, per-session drafts, `submitTurn()`, `replaceFailedUpload()`, `retryAttachment()`, `ignoreAttachment()`, `cancelTurn()`, and `resumePendingTurn()`.

- [ ] **Step 1: Write store tests with fake timers and mocked API**

Cover:

```ts
it('moves files into an optimistic user message immediately')
it('keeps an optimistic error card when turn creation fails')
it('uploads attachments concurrently and preserves progress')
it('asks for a replacement local file after upload-stage failure')
it('polls until ready then starts exactly one answer stream')
it('stops on blocked and exposes retry and ignore')
it('restores waiting turn after session history reload')
it('polls an answering turn after reload until completed or answer_failed')
it('keeps sessionBusy true while waiting or answering')
it('unlocks only on completed or cancelled')
it('stops polling when switching sessions')
it('keeps drafts isolated by session')
```

- [ ] **Step 2: Run and confirm missing store behavior**

```bash
npm run test -- --run src/stores/chat.test.ts
```

- [ ] **Step 3: Separate ordinary and attachment sends**

Keep existing `sendMessage(content)` for no-file turns. Add:

```ts
async function submitTurn(content: string, files: File[]): Promise<void>
```

It creates a session when needed, inserts an optimistic user message with attachment cards, calls `createChatTurn`, replaces optimistic IDs with server IDs, then starts concurrent uploads. If turn creation fails, keep the optimistic user message in the message area, mark it failed, expose a resend action that reuses the same `client_request_id`, and do not put its text/files back into the composer.

- [ ] **Step 4: Implement one polling loop per active session**

Use a single stored timeout handle rather than overlapping intervals. Schedule the next poll only after the previous `getChatTurn()` resolves, using a 2,000 ms base delay plus random jitter in the range -250 to +250 ms. Stop on blocked, empty, completed, cancelled, session switch, or store disposal. When ready and processing status permits, call `answerTurn()` once. After reload, keep polling an `answering` turn until the backend reports `completed` or `answer_failed`; never start a second answer stream while it remains `answering`.

- [ ] **Step 5: Implement answer and action recovery**

`answerTurn()` appends one assistant placeholder and streams into it. On answer failure, keep successful attachments and expose “重新生成回答”. For a failed attachment with no `document_id`, `replaceFailedUpload()` must ask the user to reselect the local file and call the original upload API; for a failed attachment with a `document_id`, `retryAttachment()` calls the server-side ingestion retry API without re-uploading. `ignoreAttachment()` refreshes the turn then resumes polling. `resumePendingTurn()` scans hydrated history for the newest non-terminal attachment user message.

- [ ] **Step 6: Preserve drafts while locking sends**

Expose:

```ts
const sessionBusy = computed(() =>
  activeTurn.value != null &&
  !['completed', 'cancelled'].includes(activeTurn.value.processing_status)
)
```

Store drafts by session ID (and a separate new-chat key) so switching sessions cannot send one conversation's draft into another. Do not clear draft text as a side effect of `sessionBusy`; clear only the active session's accepted submission.

- [ ] **Step 7: Run store tests and build**

```bash
npm run test -- --run src/stores/chat.test.ts
npm run build
```

- [ ] **Step 8: User checkpoint (no Git operation)**

Report timer lifecycle, idempotency behavior, and which states lock/unlock sending.

---

### Task 9: Attachment Message UI and Busy Draft Interaction

**Files:**
- Create: `src/components/chat/DraftAttachmentChip.vue`
- Create: `src/components/chat/MessageAttachmentCard.vue`
- Modify: `src/components/chat/ChatInput.vue`
- Modify: `src/components/chat/MessageBubble.vue`
- Modify: `src/views/chat/index.vue`
- Test: `src/components/chat/ChatInput.test.ts`
- Test: `src/components/chat/MessageAttachmentCard.test.ts`

**Interfaces:**
- `ChatInput` emits `send: [{ content: string; files: File[] }]`.
- `MessageAttachmentCard` emits `retry`, `ignore`, `add`, and `cancel` actions through its parent.

- [ ] **Step 1: Write component tests**

Assert:

- Draft files render inside the composer before send and can be removed.
- Selecting a file does not call `/documents/upload`.
- Send emits text plus files and clears selected files.
- Busy mode leaves textarea enabled, preserves typed draft, disables button, and blocks Enter submission.
- Upload-stage failure renders a red alert icon and a “重新选择文件” action; ingestion-stage failure renders “重试入库” and “忽略”.
- Indexed attachment renders a success check.
- Empty/blocked turn renders add/cancel exits.

- [ ] **Step 2: Run and confirm current component failures**

```bash
npm run test -- --run \
  src/components/chat/ChatInput.test.ts \
  src/components/chat/MessageAttachmentCard.test.ts
```

- [ ] **Step 3: Refactor ChatInput to draft-only attachment selection**

Remove direct imports and calls to `uploadDocument`. Keep the file picker and accepted MIME validation, but store `File[]` locally. `canSend` requires non-empty text, a selected space, not streaming, and not `sessionBusy`. The textarea `disabled` attribute must not use `sessionBusy`.

- [ ] **Step 4: Build persistent attachment cards**

Use the existing graphite/paper design language:

- Uploading: progress bar and percentage.
- Ingesting: neutral spinner and “正在入库”.
- Indexed: green check and “已就绪”.
- Failed: red `CircleAlert`, safe error text, “重试”, “忽略”.
- Empty/blocked aggregate: “补充附件”, “取消本轮”.

Buttons need accessible labels containing the file name.

- [ ] **Step 5: Wire view and message actions**

Change `handleSend` to route file-bearing payloads to `store.submitTurn()` and plain text to `store.sendMessage()`. Keep `draft` unchanged when the store is busy; clear it only after the send handler accepts the request.

- [ ] **Step 6: Run component tests and build**

```bash
npm run test
npm run build
```

- [ ] **Step 7: Manual browser accessibility check**

At `http://localhost:3300/chat`, keyboard-test file selection, send, disabled submission, retry/ignore, and visible focus. Confirm the textarea remains editable while the send button has `disabled` and an explanatory label/title.

- [ ] **Step 8: User checkpoint (no Git operation)**

Show the live UI state sequence before moving to end-to-end migration.

---

### Task 10: Migration, End-to-End Verification, and Operational Handoff

**Files:**
- Modify only if verification finds a feature-specific defect in files already listed above.
- Test artifact: use a small local PDF; do not add private documents to the repository.

**Interfaces:**
- Validates the complete approved spec across both projects and local services.

- [ ] **Step 1: Back up and run the additive migration**

Create a PostgreSQL custom-format backup outside the repository, verify it is non-empty, then run the migration:

```bash
cd /mnt/e/Coding/knowledge_base/arc-knowledge-ai
chat_attachment_backup=/tmp/arc-knowledge-before-chat-attachments.dump
docker compose exec -T postgres pg_dump -U arc -d arc_knowledge -Fc > "$chat_attachment_backup"
test -s "$chat_attachment_backup"
ls -lh "$chat_attachment_backup"
.venv/bin/python scripts/migrate.py
```

Verify `messages` new nullable columns and `message_attachments` exist. Do not drop or rewrite existing rows.

- [ ] **Step 2: Restart only affected services**

```bash
systemctl --user restart arc-api.service arc-worker.service arc-web.service
systemctl --user is-active arc-api.service arc-worker.service arc-web.service
curl -fsS http://127.0.0.1:8000/health | python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ok"'
```

Expected: all three `is-active` lines print `active` and the health assertion exits 0.

- [ ] **Step 3: Run all automated gates fresh**

```bash
cd /mnt/e/Coding/knowledge_base/arc-knowledge-ai
.venv/bin/pytest -q
.venv/bin/ruff check app tests

cd /mnt/e/Coding/knowledge_base/arc-knowledge-web
npm run test
npm run build
```

Record exact pass/fail counts. Do not claim completion if any feature-related failure remains.

- [ ] **Step 4: Verify the successful attachment flow**

1. Select a workspace and attach one small PDF.
2. Enter a question whose answer is unique to that PDF.
3. Send and confirm the message/file card immediately leave the composer.
4. Type a draft while ingestion runs and confirm sending is blocked.
5. Observe upload, ingesting, indexed, and automatic answering states.
6. Confirm every citation `doc_id` equals the uploaded attachment document ID.
7. Confirm the document appears permanently in Document Management.

- [ ] **Step 5: Verify persistence and recovery**

Reload during ingestion and confirm polling resumes. Reload after completion and confirm user message, attachment state, assistant answer and citations return from the session API.

- [ ] **Step 6: Verify failure exits**

Use a controlled fake/test failure, not a damaged production index. Confirm red alert, safe error, retry without re-upload for ingestion failure, ignore, add replacement attachment, cancel, and session unlock.

- [ ] **Step 7: Verify ordinary-chat regression**

Send a message without attachments. Confirm it uses the existing whole-workspace route, streams normally, and does not create attachment rows.

- [ ] **Step 8: Final user checkpoint (no Git operation)**

Provide:

- Files changed in each repository.
- Migration result.
- Backend test totals, frontend test totals and build result.
- Successful and failed-flow evidence.
- Known limitations from the spec.
- A reminder that no Git commit was created and only the user may commit.
