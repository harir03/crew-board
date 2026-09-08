# crew-board

Production-style **control plane** for Hari’s Grok Bot crew: Mission Control layout, D colors, sparse updates.

## Design rules (master)

1. **Lifecycle immediate** — start / block / wait / done (with proof)
2. **Heartbeat 5–15 min** while `running` (default **10 min**) — not every tool call
3. **Idle = silence**
4. **Lease stale at 15 min** without renewal
5. **Done requires proof**
6. **Blocked / waiting_human requires blocker**
7. **Idempotent `event_id`** on writes

See [PROTOCOL.md](./PROTOCOL.md).

## Run

```bash
python3 server.py
# http://127.0.0.1:8765
```

Env knobs: `CREW_HEARTBEAT_SEC` (default 600), `CREW_LEASE_SEC` (default 900), `PORT` (default 8765).
