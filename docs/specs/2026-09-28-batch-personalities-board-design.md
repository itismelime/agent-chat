# agent-chat: small batch, personalities, kanban board

Status: approved in chat 2026-09-28 ("LGTM go ahead and build"); the user
asked for all three in one go. Builds on the core, spawn, local-models,
local-talk and local-agents specs.

## 1. Small batch

- **Forget.** `POST /api/projects/<id>/agents/<name>/forget {}` deletes the
  entry of a removed agent (400 if it is not removed); its name is free again;
  its messages stay. Page: right-click a Removed agent → **Forget** (asks to
  confirm).
- **Code formatting.** In the page, fenced blocks (```` ``` ```` … ```` ``` ````,
  an optional language word after the opening fence is ignored) render as a
  monospace box with a **Copy** button; `` `inline` `` renders as inline code.
  Nothing else is interpreted. Built with DOM nodes and `textContent` only.
- **Model Hub picking.** `IQ1…` files get quantisation quality 30 (below Q2);
  files whose names contain `eagle`, `draft` or `dflash` are never a best
  file; search results are not cached when any size lookup failed.
- Already done before this spec: Remove also stops an agent started from the
  page (`35893dd`).

## 2. Personalities

- Every agent entry may have `personality` (text, at most 500 characters).
  For local members it replaces `role` (existing `role` values are read as
  the personality; the role route stays as an alias).
- Set with `POST /api/projects/<id>/agents/<name>/personality {personality}`
  (`null`/empty clears). Page: right-click → **Edit personality** with presets
  (Reviewer, Architect, Tester, Terse helper) or custom text; when starting an
  agent from the page (Claude, Codex, OpenCode) the page asks for an optional
  personality, stored on the start record and copied to the agent at join.
- Presets (page only): Reviewer "You review changes critically: correctness
  first, then clarity. Point to exact lines."; Architect "You think about
  structure and trade-offs before code, and keep designs small."; Tester
  "You look for how things break and write the smallest test that shows it.";
  Terse helper "You answer in as few words as possible."
- Delivered: in the `chat_join` result (`Your personality: …`) and with every
  wake-up: as the first line of the `chat wait` output (Claude) and of the
  queued text (Codex), inside OpenCode's typed line as
  ` Your personality: <p>.` before `Reply with chat_post.` (within the 2000
  character limit), and in local members' system message.

## 3. Kanban board

- One board per project in `projects/<id>/board.json`:
  `{"next": n, "cards": {"<n>": {"title", "description", "column",
  "assignee", "created_by", "updated"}}}`. Columns fixed: `todo`,
  `doing`, `review`, `done` (shown To do, In progress, Review, Done).
- API: `GET /api/projects/<id>/board`;
  `POST …/board/cards {title, description?, column?, assignee?, by}` → 201;
  `POST …/board/cards/<n> {by, title?, description?, column?, assignee?}`;
  `POST …/board/cards/<n>/delete {by}`. `by` is `user` or a joined agent;
  titles 1–200 characters, descriptions up to 4000, assignee `user`, a
  project agent, or null.
- Every change posts a notice from the reserved sender `board`, which wakes
  only addressed names (like an agent message): `#3 "Fix login" added by kit
  (To do)`, `kit moved #3 "Fix login" to Review`, `@kit you were assigned #3
  "Fix login" by user`, `user deleted #3 "Fix login"`, `kit edited #3`.
  `board` is reserved as a name and cannot be joined or posted as through the
  API.
- Agent tools (Claude, Codex, OpenCode; not local members): `board_list()`,
  `board_add(title, description?, assignee?)`, `board_update(id, column?,
  assignee?, title?, description?)`.
- Page: a **Chat | Board** switch in the header; the board shows four columns
  of cards (number, title, assignee), drag and drop between columns, **+** per
  column to add, click a card to edit (title, description, assignee, delete).
  Refreshes with the chat (every 2 s).

## Testing

Tests first for each part: Forget route and name reuse; code formatting via a
small pure JS function checked in Firefox; rating changes; personality storage,
delivery in join, wait output, Codex text, OpenCode line, local prompt; board
store (ids, validation, notices, wake rules), routes, MCP tools; Firefox check
of the board and personalities; live check with the user (an agent takes a
card, moves it, reports back).
