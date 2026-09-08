# Crew board — control plane protocol

Production mindset: this is a **distributed work control plane**, not a chat log.
Agents do **not** stream every thought. They emit **lifecycle events** + sparse **heartbeats**.

Inspired by: OpenClaw/Mission Control heartbeats, Claude agent-view state groups,
Kubernetes lease/heartbeat patterns, and SQS/visibility-timeout style "still working" signals.

---

## 1. Dual clocks (critical)

| Channel | When | Frequency |
|---------|------|-----------|
| **Lifecycle event** | State machine transition only | Immediate (on change) |
| **Heartbeat** | Agent is `running` or `waiting_human` | Every **5–15 min** while still in that state |
| **Idle** | Nothing to do | **No updates** (silence = healthy idle) |

**Do not** POST on every tool call, file edit, or sentence. That is log spam, not status.

Lifecycle events (must update board immediately):

- claim / start work → `running` + task `in_progress`
- block → `blocked` + named `blocker`
- need Hari → `waiting_human` + decision/question in `task` or task card
- scoped gate passed → keep running or move task; attach `proof` path/URL
- finish → `done` / task `done` **only with `proof`**
- fail → `needs_attention` + blocker

Heartbeats (while `running`):

```json
{"id":"atlas","state":"running","task":"same task title — still working: <one short clause>"}
```

If no lifecycle event **and** no heartbeat for **15 minutes** while `running`, the board marks **STALE** (lease expired). Treat as `needs_attention` until the agent renews or Dumie clears.

---

## 2. State machines (strict)

### Agent states
`idle → running → {blocked | waiting_human | needs_attention} → running → done → idle`

- `done` on an agent means “this assignment finished,” then usually return to `idle`.
- `passed_scoped_gate` is **not** a separate UI state; encode as progress text + `proof` on the task. Final `done` needs final proof.

### Task columns
`backlog → todo → in_progress → {blocked | waiting_human} → done`

- `blocked` is a first-class column in the ledger; UI may show it under In Progress with a **blocked** tag.
- Moving to `done` **requires** `proof` (PR URL, commit SHA, log path, test output path). No proof → reject / stay in review.

---

## 3. Idempotency & conflict

- Every write should include agent `id` + optional `event_id` (UUID). Duplicate `event_id` = no-op.
- `status.json` is the single source of truth; `events.jsonl` is the append-only audit log.
- Last-write-wins on agent/task fields, but **never** clear a `blocker` or `proof` with an empty heartbeat.
- Heartbeats must not change `column` or clear blockers.

---

## 4. What to put in a status line

Good: `"HITL #6 — waiting Actions enable"`  
Bad: `"reading coordinator.py line 40… still thinking…"`

Fields:

- `task` — one line, human-scannable
- `blocker` — required if blocked/waiting_human
- `proof` — required to mark done

---

## 5. API (local server)

```bash
# Lifecycle or heartbeat
curl -sS -X POST http://127.0.0.1:8765/api/agents/update \
  -H 'Content-Type: application/json' \
  -d '{"id":"iris","state":"blocked","task":"scout","blocker":"gh auth","event_id":"…"}'

# Create task
curl -sS -X POST http://127.0.0.1:8765/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"…","column":"todo","assignee":"atlas"}'

# Move task (done needs proof)
curl -sS -X PATCH http://127.0.0.1:8765/api/tasks/TASK_ID \
  -H 'Content-Type: application/json' \
  -d '{"column":"done","proof":"https://github.com/…/pull/6"}'
```

Without server: edit `data/status.json` + append one JSON line to `data/events.jsonl`.

---

## 6. Cadence cheat sheet (pin this)

| Situation | Update? |
|-----------|---------|
| Started work | Yes — immediate |
| Still working, <5 min since last beat | No |
| Still working, ≥5–15 min | Yes — heartbeat only |
| Blocked / need human | Yes — immediate |
| Unblocked / resumed | Yes — immediate |
| Finished with proof | Yes — immediate |
| Idle | No |
| Cosmetic progress | No |

Default heartbeat while running: **10 minutes**.  
Lease / stale threshold: **15 minutes**.

---

## 7. Dumie / crew contract

- Dumie mirrors the board when Hari asks “where are we?”
- Specialists update **themselves** on lifecycle; Dumie may correct the board if someone goes silent past lease.
- Fan-out to many agents still does **not** mean 50 board writes — one write per agent per transition.
