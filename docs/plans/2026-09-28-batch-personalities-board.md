# Small batch, personalities, kanban board — Implementation Plan

> Executed natively (superpowers:executing-plans) right after the user's
> go-ahead; the spec (`docs/specs/2026-09-28-batch-personalities-board-design.md`)
> carries the details, so tasks list interfaces and test cases, each done
> test-first.

## Global Constraints
- Python 3.9+ stdlib; files under 500 lines (the page's board goes in
  `agentchat/board.js` like `models.js`); commit only on a green suite.
- `board` becomes a reserved name; board notices wake only addressed names.

## Tasks
1. **Forget** — `Store.forget(pid, name)` (400 unless removed; deletes the
   entry; `store.local` cleared); route `…/agents/<name>/forget`. Tests: forget
   removed agent then rejoin with the same name; refuse forgetting an active one.
2. **Model Hub picking** — `rating`: IQ1 quality 30; `usable()` refuses
   `eagle`/`draft`/`dflash`; `Hub.search` does not cache when a size lookup
   failed. Tests for each.
3. **Personalities (store, routes)** — `personality` on agent entries
   (`_check_role` reused, max 500); `set_personality`; `add_local(…, role)`
   stores it as personality; `status()` gains `personality` (falls back to
   `role`); start records take `personality` and `join` copies it; route
   `…/agents/<name>/personality`, `role` stays an alias; `POST spawned` accepts
   `personality`. Tests.
4. **Personalities (delivery)** — `chat_join` result line; CLI `chat wait`
   prints `Your personality: …` first (from `GET agents`); Codex queued text
   first line; `spawn.format_message(m, name, personality=None)`; talk
   `system_prompt` uses it. Tests.
5. **Board (store, routes, notices)** — `agentchat/board.py` (`Board(store)`:
   `get`, `add`, `update`, `delete`; validation; notices through
   `store.post(pid, "board", …)`; `board` reserved but allowed as a poster for
   notices only); routes. Tests: ids, validation, moves, assignment notice wakes
   the assignee only, deletes, unknown card 404, notices not posted for no-op
   updates.
6. **Board (agent tools)** — MCP `board_list`, `board_add`, `board_update`
   for claude/codex/opencode kinds; not for kind llm. Tests.
7. **Page** — code formatting (`renderText` in page.html), Forget, Edit
   personality with presets, personality prompt on Start, Chat | Board switch
   and board UI in `agentchat/board.js` (served like `models.js`). Firefox check.
8. **Docs, deploy, live check** — README; final review; merge; restart; live
   check with the user.
