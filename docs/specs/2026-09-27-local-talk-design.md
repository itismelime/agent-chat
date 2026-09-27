# agent-chat: talking local LLMs

Status: draft for review, 2026-09-27. Roadmap feature 6, part 2 of 3. Builds
on `2026-09-27-local-models-design.md` (the models, their context and
thinking settings) and the core spec (wake rules, names, statuses).

## Goal

The user adds an installed local model to a project as a member. It answers
in the chat like Claude and Codex, following the same addressing rules, and
shows in the member list. It only talks: no files, no commands (part 3).

## Non-goals

Tools or file access, conversation summaries (the user chose recent
messages only), several replies in parallel, streaming replies, local
members that start other agents.

## Members

- Added with `POST /api/projects/<id>/locals {model, name, role?}`. `model`
  must be installed in the Ollama in use; `name` follows the core naming
  rules (lower-cased, unique per project); `role` is optional text up to
  500 characters.
- Stored as a normal agent entry with `kind: "llm"` plus `"model"`,
  `"role"`, and in-memory state (generating, error, agent streak).
- `POST /api/projects/<id>/agents/<name>/role {role}` changes the role
  (`null` or empty clears it).
- Remove / Add back work as for any agent. The name stays reserved.

## Waking and replying

Posting a message already calls `store.deliver` for Codex agents with a
thread. A second hook, `store.talk(pid, name, message)`, is called for each
local member (not removed, not the sender) that the core rule wakes: a user
message wakes every member, an agent message only the names it addresses.

One background worker (`agentchat/talk.py`) takes these in order, one reply
at a time (Ollama loads one model at a time):

1. **Messages for others.** If the message addresses names and this member is
   not one of them, it is read, not answered.
2. **Catch up once.** When several wake-ups for one member are queued, it
   answers only the newest.
3. **Loop guard.** A member answers at most 3 agent messages in a row; the
   streak resets on any user message. After that it stays quiet until the
   user writes.
4. **What the model gets.** System message:
   `You are <name>, a local model in the chat of project <project> with the
   user and <other members, comma-separated>. Messages are shown as
   "name: text". Reply as <name> only, briefly, in plain text, without your
   name in front. A message without @ is for everyone; with @names only
   those reply.` followed by `Your role: <role>` if set. Then the newest
   messages, oldest first: its own as `assistant` (text only), everyone
   else's as `user` with `name: text`, as many as fit in 3/4 of the model's
   context at 3 characters per token (the rest is left for the answer).
5. **The request.** `POST /api/chat` with the model's `num_ctx` and `think`
   for use `talk` (off unless set, see the local-models spec),
   `options.num_predict: 1024`, not streamed.
6. **Posting.** The answer (without any thinking text, stripped, a leading
   `<name>:` removed) is posted as the member. An empty answer is not posted.
   The member's cursor moves to the answered message.

## Status

- `busy` (Working) while it generates, else `waiting` (Available).
- `offline` with an `error` field when the last attempt failed (Ollama not
  reachable, model not found, out of memory); the next success clears it.
- Removed members stay `removed`.
- `GET .../agents` entries gain `model` and `error` for local members.

## Page

- The start menu (**+ Agent**, right-click on the member list, `Alt+A`) gets
  **Add local model ▸** followed by one entry per installed model; the
  entries are greyed out, with a reason, when Ollama does not answer or has
  no models. Choosing one asks for a name (suggested: the model name before
  `:`, lower-cased, non-name characters removed, e.g. `qwen3:0.6b` →
  `qwen3`) and an optional role.
- `/local <model> <name>` in the message box does the same (no role).
- A local member's line: its name, then `local LLM · <model>`; an Offline
  one has the error as its tooltip.
- Right-click a local member: **Edit role** (asks, prefilled), **Remove** /
  **Add back**.
- The shortcut overlay lists `/local`.

## Failure behaviour

- Model not installed, name taken or invalid, role too long: `400`/`409`.
- Model deleted later: the member goes Offline with Ollama's error when next
  woken.
- A service restart loses queued wake-ups; members answer the next message.
- A reply that takes longer than 10 minutes fails like any Ollama error.

## Testing

- Prompt building: the system message (with and without role and other
  members), the budget (newest first, cut at 3/4 of the context, own
  messages as `assistant`), name prefix stripped.
- Worker: skips messages for others, catches up once, the loop guard counts
  and resets, status busy while generating, offline with error on failure
  and cleared after, nothing posted for an empty answer.
- Routes: add (model checks against the fake Ollama), role, status fields.
- Firefox: the menu entries, the name and role prompts, the member line.
- Live, with the user: `qwen3:0.6b` in a scratch project answers the user,
  stays quiet for `@someone-else`, and appears Working while answering.
