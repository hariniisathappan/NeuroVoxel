"""Tiny background-job runner. One heavy PyRadiomics job at a time keeps memory predictable on a laptop."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

import db

_LIVE: dict[tuple[str, str], dict[str, Any]] = {}
_LOCK = threading.Lock()
_HEAVY = threading.Lock()
_CANCEL: set[tuple[str, str]] = set()


def cancel(token: str, kind: str) -> None:
    _CANCEL.add((token, kind))


def cancelled(token: str, kind: str) -> bool:
    return (token, kind) in _CANCEL


def status(token: str, kind: str) -> dict[str, Any]:
    live = _LIVE.get((token, kind))
    if live:
        return {k: live[k] for k in ("state", "progress", "message", "error")}
    saved = db.get_job(token, kind)
    return saved or {"state": "idle", "progress": 0.0, "message": "", "error": None}


def start(token: str, kind: str, worker: Callable[[Callable[[float, str], None]], None], on_done: Callable[[], None] | None = None,
          on_error: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Run `worker(progress)` in a thread. Returns the current job; if one is already running it is returned instead."""
    with _LOCK:
        existing = _LIVE.get((token, kind))
        if existing and existing["state"] == "running":
            return existing
        _CANCEL.discard((token, kind))
        job = {"state": "running", "progress": 0.0, "message": "Queued", "error": None}
        _LIVE[(token, kind)] = job
        db.upsert_job(token, kind, state="running", progress=0.0, message="Queued", error=None, started_at=db.now(), finished_at=None)

    def progress(value: float, message: str) -> None:
        job["progress"], job["message"] = round(min(max(value, 0.0), 1.0), 3), message

    def run() -> None:
        try:
            progress(0.0, "Waiting for the extraction engine")
            with _HEAVY:
                worker(progress)
            if on_done:                     # results are saved before the job is reported as done
                on_done()
            job.update(state="done", progress=1.0, message="Complete")
            db.upsert_job(token, kind, state="done", progress=1.0, message="Complete", error=None, finished_at=db.now())
        except Exception as error:  # noqa: BLE001 - surfaced to the UI
            message = str(error) or error.__class__.__name__
            job.update(state="error", error=message)
            db.upsert_job(token, kind, state="error", message="Failed", error=message, finished_at=db.now())
            if on_error:
                on_error(message)
        finally:
            time.sleep(0.2)
            _LIVE.pop((token, kind), None)

    threading.Thread(target=run, daemon=True, name=f"nv-{kind}-{token[:8]}").start()
    return job
