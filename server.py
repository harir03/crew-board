#!/usr/bin/env python3
"""Crew board control plane — status.json truth, sparse heartbeats, lease/stale."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PUBLIC = ROOT / "public"
STATUS = DATA / "status.json"
EVENTS = DATA / "events.jsonl"
SEEN = DATA / "seen_event_ids.json"
PORT = int(os.environ.get("PORT", "8765"))

# Production knobs (seconds)
HEARTBEAT_HINT_SEC = int(os.environ.get("CREW_HEARTBEAT_SEC", "600"))  # 10m
LEASE_STALE_SEC = int(os.environ.get("CREW_LEASE_SEC", "900"))  # 15m

ALLOWED_AGENT = {"idle", "running", "needs_attention", "blocked", "waiting_human", "done"}
ALLOWED_TASK = {"backlog", "todo", "in_progress", "blocked", "waiting_human", "done"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_status() -> dict:
    return json.loads(STATUS.read_text())


def save_status(doc: dict) -> None:
    doc["updated_at"] = now()
    doc.setdefault("control_plane", {})
    doc["control_plane"].update(
        {
            "heartbeat_sec": HEARTBEAT_HINT_SEC,
            "lease_stale_sec": LEASE_STALE_SEC,
            "rule": "lifecycle immediate; heartbeat 5-15m while running; idle silent",
        }
    )
    STATUS.write_text(json.dumps(doc, indent=2) + "\n")


def append_event(event: dict) -> None:
    event = {"ts": now(), **event}
    with EVENTS.open("a") as f:
        f.write(json.dumps(event) + "\n")


def load_seen() -> set[str]:
    if not SEEN.exists():
        return set()
    try:
        return set(json.loads(SEEN.read_text()))
    except Exception:
        return set()


def save_seen(seen: set[str]) -> None:
    # keep last 5000
    items = list(seen)[-5000:]
    SEEN.write_text(json.dumps(items))


def annotate_stale(doc: dict) -> dict:
    """Mark agents whose lease expired (running/waiting without fresh update)."""
    now_ts = datetime.now(timezone.utc).timestamp()
    for a in doc.get("agents", []):
        if a.get("state") not in {"running", "waiting_human", "blocked", "needs_attention"}:
            a.pop("lease_stale", None)
            continue
        try:
            updated = datetime.fromisoformat(a.get("updated_at", "").replace("Z", "+00:00")).timestamp()
        except Exception:
            a["lease_stale"] = True
            continue
        a["lease_stale"] = (now_ts - updated) > LEASE_STALE_SEC
    return doc


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[crew-board] {self.address_string()} {fmt % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, body: dict | list) -> None:
        raw = json.dumps(body, indent=2).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode())

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/STATUS.html"):
            html = (PUBLIC / "index.html").read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if path == "/api/status":
            self._json(200, annotate_stale(load_status()))
            return
        if path == "/api/events":
            lines = EVENTS.read_text().strip().splitlines() if EVENTS.exists() else []
            events = [json.loads(l) for l in lines[-100:]]
            self._json(200, events)
            return
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "heartbeat_sec": HEARTBEAT_HINT_SEC,
                    "lease_stale_sec": LEASE_STALE_SEC,
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._read_json()
        doc = load_status()
        seen = load_seen()

        event_id = body.get("event_id")
        if event_id:
            if event_id in seen:
                self._json(200, {"ok": True, "deduped": True, "event_id": event_id})
                return
            seen.add(event_id)
            save_seen(seen)

        if path == "/api/agents/update":
            agent_id = body.get("id")
            agent = next((a for a in doc["agents"] if a["id"] == agent_id), None)
            if not agent:
                self._json(404, {"error": "unknown agent"})
                return
            kind = body.get("kind") or "lifecycle"
            if "state" in body:
                if body["state"] not in ALLOWED_AGENT:
                    self._json(400, {"error": "bad state", "allowed": sorted(ALLOWED_AGENT)})
                    return
                agent["state"] = body["state"]
            # Heartbeats must not clear blocker/proof
            if kind == "heartbeat":
                if body.get("task"):
                    agent["task"] = body["task"]
                agent["updated_at"] = now()
                agent["last_heartbeat_at"] = agent["updated_at"]
            else:
                for key in ("task", "blocker", "proof"):
                    if key in body:
                        agent[key] = body[key]
                if agent.get("state") in {"blocked", "waiting_human"} and not agent.get("blocker"):
                    self._json(400, {"error": "blocker required for blocked/waiting_human"})
                    return
                agent["updated_at"] = now()
            save_status(doc)
            append_event({"type": "agent_update", "kind": kind, "agent": agent_id, "patch": body, "event_id": event_id})
            self._json(200, agent)
            return

        if path == "/api/tasks":
            task = {
                "id": body.get("id") or f"t-{int(datetime.now().timestamp())}",
                "title": body.get("title") or "untitled",
                "column": body.get("column") or "todo",
                "assignee": body.get("assignee"),
                "blocker": body.get("blocker"),
                "link": body.get("link"),
                "proof": body.get("proof"),
                "updated_at": now(),
            }
            if task["column"] not in ALLOWED_TASK:
                self._json(400, {"error": "bad column", "allowed": sorted(ALLOWED_TASK)})
                return
            if task["column"] == "done" and not task.get("proof"):
                self._json(400, {"error": "proof required to create task as done"})
                return
            doc.setdefault("tasks", []).append(task)
            save_status(doc)
            append_event({"type": "task_create", "task": task, "event_id": event_id})
            self._json(201, task)
            return

        if path == "/api/meta":
            for key in ("goal", "next_action", "not_proven", "project"):
                if key in body:
                    doc[key] = body[key]
            save_status(doc)
            append_event({"type": "meta_update", "patch": body, "event_id": event_id})
            self._json(200, {"ok": True, "updated_at": doc["updated_at"]})
            return

        self._json(404, {"error": "not found"})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/api/tasks/"):
            self._json(404, {"error": "not found"})
            return
        task_id = path[len("/api/tasks/") :]
        body = self._read_json()
        doc = load_status()
        seen = load_seen()
        event_id = body.get("event_id")
        if event_id:
            if event_id in seen:
                self._json(200, {"ok": True, "deduped": True})
                return
            seen.add(event_id)
            save_seen(seen)

        task = next((t for t in doc.get("tasks", []) if t["id"] == task_id), None)
        if not task:
            self._json(404, {"error": "unknown task"})
            return
        if "column" in body:
            if body["column"] not in ALLOWED_TASK:
                self._json(400, {"error": "bad column", "allowed": sorted(ALLOWED_TASK)})
                return
            if body["column"] == "done":
                proof = body.get("proof", task.get("proof"))
                if not proof:
                    self._json(400, {"error": "proof required to mark done"})
                    return
                task["proof"] = proof
            task["column"] = body["column"]
        for key in ("title", "assignee", "blocker", "link", "proof"):
            if key in body:
                task[key] = body[key]
        if task.get("column") in {"blocked", "waiting_human"} and not task.get("blocker"):
            self._json(400, {"error": "blocker required for blocked/waiting_human tasks"})
            return
        task["updated_at"] = now()
        save_status(doc)
        append_event({"type": "task_update", "task_id": task_id, "patch": body, "event_id": event_id})
        self._json(200, task)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if not STATUS.exists():
        raise SystemExit(f"missing {STATUS}")
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"crew-board on http://127.0.0.1:{PORT} (heartbeat={HEARTBEAT_HINT_SEC}s lease={LEASE_STALE_SEC}s)")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
