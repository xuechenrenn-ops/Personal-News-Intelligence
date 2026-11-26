"""Delivery state for two reviewed messages. The experiment supplies no sender."""
import fcntl
import hashlib
import json
import os
from pathlib import Path


def _persist(path, state):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def deliver_once(state_path, batch_id, messages, send, *, approved=False):
    """Checkpoint before calling a sender. A lost reply always requires reconciliation."""
    if not approved:
        return {"status": "preview"}
    if tuple(messages) != ("professional", "extended"):
        raise ValueError("Exactly two ordered messages are required")
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode()).hexdigest()
    with path.with_suffix(path.suffix + ".lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        batch = state.setdefault(batch_id, {"fingerprint": fingerprint, "status": {}})
        if batch["fingerprint"] != fingerprint:
            raise ValueError("Previously attempted batch has different content")
        status = batch["status"]
        for lane, message in messages.items():
            if status.get(lane):
                continue
            status[lane] = "submission_unknown"
            _persist(path, state)
            try:
                accepted = send(lane, message)
            except (OSError, RuntimeError, ValueError):
                accepted = False
            if accepted is True:
                status[lane] = "accepted"
                _persist(path, state)
        return dict(status)
