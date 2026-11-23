"""Structured JSON output and error classification for agent callers."""
import json

if __package__:
    from .failures import classify, redact
else:
    from failures import classify, redact

SCHEMA_VERSION = 1


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def classify_error(error, phase=None):
    failure = classify(error, phase=phase)
    return {
        "category": failure.category,
        "label": failure.label,
        "message": redact(error) or failure.message,
        "next_step": redact(failure.next_step),
        "retryable": bool(failure.retryable),
    }


def error_payload(command, error, *, phase=None, exit_code=1, task_id=None, extra=None):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "exit_code": exit_code,
        "error": classify_error(error, phase=phase),
    }
    if task_id is not None:
        payload["task_id"] = task_id
    if extra:
        payload.update(extra)
    return payload
