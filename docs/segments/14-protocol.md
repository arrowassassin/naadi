# Segment 14 — tutor wire protocol (server ↔ web)

The contract between the `tutor` graph (`apps/api/naadi_api/agent/graphs/tutor.py`) and the assistant UI
(`apps/web/src/assistant/`). The web validates every payload below with zod (`apps/web/src/assistant/protocol.ts`)
and ignores what does not parse. The server sends structured fields only; every sentence the student reads is a
`ui_copy` key formatted on the client (`agent.*`, `canvas.*`).

## Runtime

- Graph id `tutor` on the Aegra runtime (same origin, Agent Protocol routes at the root), through `useStream`
  (`@langchain/langgraph-sdk/react`) with the Supabase access token, `streamMode: ["messages-tuple", "custom", "updates", "values"]`.
- One Aegra thread per conversation. The server writes the matching `assistant_threads` row on the first turn
  (title = the first question, ≤ 80 chars). Thread list, rename, delete and memories are plain REST under
  `/api/assistant/*` (OpenAPI → zod, `@naadi/shared`).

## Input (`submit`)

```ts
interface TutorInput {
  messages: [{ type: "human"; content: string }];   // the student's text, ≤ 4000 chars
  page_context: PageContextIn | null;
  attachments?: AttachmentIn[];                       // ≤ app_config.assistant.max_attachments (4)
  web?: boolean;                                      // the bar's web toggle: allow web search this turn
  ask?: { kind: "hint" | "explain" | "ask" | "summarise" | "flashcards" | "quiz" };
}

interface PageContextIn {
  route: string;                                     // e.g. "/learn/ch05/s03"
  type: "section" | "question" | "flashcard" | "note" | "search" | "today" | "practice" | "cards" | "page";
  id?: string | null;                                // section id, question id, card id, note id, coach suggestion id
  title?: string | null;                             // ≤ 200 chars, display only
  chapter_id?: string | null;
  video?: { media_id: string; t: number } | null;
  question?: { id: string; answered?: boolean; chosen?: string | null } | null;  // the server re-checks the attempt
  exam_mode?: boolean;                               // ignored by the server (it reads active_exam)
}

type AttachmentIn =
  | { kind: "selection"; text: string; surface: { type: "section" | "note" | "question"; id: string } } // ≤ 4000 chars
  | { kind: "image"; data_url: string; text?: string | null; surface?: { type: string; id: string } | null }; // png/jpeg/webp ≤ 1.5 MB (lasso crop, photo)
```

The server **never** trusts `exam_mode`, `question.answered` or `question.chosen`: exam mode comes from
`active_exam(user)`, and `answered`/`chosen` from the student's attempts. `user_id` comes only from the token.

## State (`values`)

```ts
interface TutorValues {
  messages: Message[];                // human / ai / tool (tool messages hold the model-facing text only)
  blocks: Block[];                    // every answer block of the thread, in order
  citations: Citation[];
  canvas: CanvasRef[];                // see the canvas contract (ledger 20261117T033403)
  notice: Notice | null;              // the latest turn's notice (cleared at the start of each turn)
  refusal: { reason: "exam_mode" } | null;
  disclaimer_shown: boolean;
}
interface Block { id: string; message_id: string | null; type: BlockType; data: Record<string, unknown> }
type BlockType = "flowchart" | "image" | "video" | "sources" | "actions" | "card_preview" | "table" | "chart" | "canvas" | "notice";
interface Citation { id: string; message_id: string | null; title: string; source: string; layer: "course" | "notes" | "web";
  url: string | null; target: Record<string, string | null> | null; license: string | null }
type Notice =
  | { kind: "disclaimer" }
  | { kind: "budget_exhausted"; reset_at: string }           // ISO time; client shows agent.quota_reset
  | { kind: "rate_limited"; reset_at: string }
  | { kind: "unavailable" }                                   // no model answered (degraded answer below it)
  | { kind: "turn_cap" }                                      // 4 calls used; the answer is what exists so far
  | { kind: "exam_mode" };
```

`message_id` ties a block or citation to the AI message it belongs to (`null` = the thread level). A reopened
thread renders everything from `values` (fetched from the checkpoint); live turns also stream the same blocks as
custom events so they appear while the answer streams.

## Custom events

```ts
{ type: "block"; block: Block }                // also used for notices: block.type === "notice", block.data = Notice
{ type: "citation"; citation: Citation }
{ type: "tool"; tool: string; status: "running" | "done" | "error" | "declined" }   // status line ("Searching your notes…")
```

## Block data

| type | data |
|---|---|
| `flowchart` | `{ mermaid: string }` (validated server-side, ≤ 4000 chars) — rendered with mermaid; error state if it fails |
| `image` | `{ url: string (https), title: string, license: string \| null, credit: string \| null, link: string \| null, width?, height? }` |
| `video` | `{ provider: "youtube"; video_id: string; media_id: string; title: string; start_s: number \| null }` (IFrame player only) |
| `sources` | `{ items: Citation[] }` |
| `actions` | `{ items: { action: "open_block" \| "open_canvas" \| "ask"; label_key: string; payload: Record<string, unknown> }[] }` — `open_block` payload `{block_id, n}` → `/practice/blocks/{block_id}/{n}` (or the app's block route) |
| `card_preview` | `{ cards: { front: string; back: string }[] }` (after a confirmed `make_flashcards`, links to Cards) |
| `table` | `{ columns: { key: string; label_key: string \| null; label: string \| null }[]; rows: Record<string, string \| number \| null>[] }` |
| `chart` | `{ chart: "bar" \| "line"; x: string; series: { key: string; label_key: string \| null; label: string \| null }[]; rows: Record<string, string \| number \| null>[]; unit: "percent" \| "count" \| "seconds" \| null }` |
| `canvas` | `CanvasRef` (opens the canvas; the chip reopens it) |
| `notice` | `Notice` |

Model text is markdown (GFM, KaTeX `$…$`, fenced `mermaid`), rendered with Streamdown (https links/images only).

## Interrupts (every write tool)

`interrupt` value:

```ts
interface ConfirmRequest {
  type: "confirm";
  tool: "save_note" | "append_to_note" | "update_note" | "make_flashcards" | "build_block" | "add_to_plan"
      | "edit_chapter_section" | "remember" | "forget" | string;
  preview: Preview;
  editable: boolean;     // "Edit" shown only when true
}
type Preview =
  | { kind: "note"; mode: "create" | "append" | "update"; note_id: string | null; anchor: { type: string; id: string | null; label: string | null }; doc: TipTapDoc }
  | { kind: "cards"; cards: { front: string; back: string }[] }
  | { kind: "block"; count: number; mode: "tutor" | "timed"; systems: string[]; disciplines: string[]; status: "unused" | "incorrect" | "flagged" | "correct" | "all" }
  | { kind: "plan_item"; day: string; title: string; item_kind: string; target_minutes: number | null }
  | { kind: "chapter_diff"; section_id: string; heading: string; target: "revision" | "overlay_note"; ops: { op: "replace" | "insert_after" | "delete"; index: number; before: TipTapNode | null; after: TipTapNode | null }[] }
  | { kind: "memory"; key: string; fact: string }
  | { kind: "forget"; key: string; fact: string };
```

Resume (`submit(null, { command: { resume } })`): `{ decision: "accept" }`, `{ decision: "cancel" }`, or
`{ decision: "edit", args }` where `args` are the tool's arguments as edited (note: `{doc}`; cards: `{cards}`;
plan: `{day, title}`; memory: `{fact}`). An edit is re-validated, re-previewed and applied without a second
confirm. Cancel writes nothing and leaves no trace in any table.

## REST (`/api/assistant`, signed-in, feature flag `assistant`, 409 `exam_active` during an exam)

- `GET /threads?q=&limit=` → `{ items: { id, aegra_thread_id, title, created_at, last_message_at, page_ref }[] }`, newest first, `q` matches title.
- `PATCH /threads/{id}` `{ title }` → the thread. `DELETE /threads/{id}` → 204 (deletes the Aegra thread, its checkpoints and the row).
- `GET /memories` → `{ items: { key, fact, created_at }[], suggestions: { key, fact, created_at }[], max_items }`.
- `DELETE /memories/{key}` → 204. `POST /memories/suggestions/{key}` `{ decision: "keep" | "discard" }` → 204.
