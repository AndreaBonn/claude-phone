"""Stand-in for the `claude` binary: speaks stream-json, driven by FAKE_SCENARIO."""

import json
import os
import signal
import sys
import time
import uuid


def emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def result(session_id: str, text: str, is_error: bool = False, turns: int = 1) -> None:
    subtype = "error_during_execution" if is_error else "success"
    emit(
        {
            "type": "result",
            "subtype": subtype,
            "is_error": is_error,
            "result": text,
            "session_id": session_id,
            "num_turns": turns,
            "total_cost_usd": 0.01,
        }
    )


def main() -> None:
    scenario = os.environ.get("FAKE_SCENARIO", "echo")
    record = os.environ.get("FAKE_RECORD")
    if record:
        with open(record, "a") as handle:
            handle.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
    resume = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else None
    session_id = resume or str(uuid.uuid4())
    if scenario == "stubborn":
        # Ignores SIGTERM: only SIGKILL stops it.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    for line in sys.stdin:
        text = json.loads(line)["message"]["content"][0]["text"]
        if scenario == "resumefail" and resume:
            result(session_id, "", is_error=True, turns=0)
            sys.stderr.write("API Error: 529 overloaded\n")
            sys.exit(1)
        if scenario == "notfound" and resume:
            result(session_id, "", is_error=True, turns=0)
            sys.stderr.write(f"No conversation found with session ID: {resume}\n")
            sys.exit(1)
        emit({"type": "system", "subtype": "init", "session_id": session_id})
        if scenario == "autherror":
            message = "Failed to authenticate. API Error: 401 OAuth access token has expired."
            emit({"type": "assistant", "message": {"content": [{"type": "text", "text": message}]}})
            emit(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": message,
                    "session_id": session_id,
                    "num_turns": 1,
                }
            )
            continue
        if scenario == "crash":
            sys.stderr.write("boom: simulated crash\n")
            sys.exit(3)
        if scenario == "noisy":
            # One huge stderr line with no newline, bigger than a pipe buffer.
            sys.stderr.write("x" * 300_000)
            sys.stderr.flush()
        if scenario in ("hang", "stubborn"):
            time.sleep(60)
        emit(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "working"},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Read",
                            "input": {"file_path": "a.py"},
                        },
                    ]
                },
            }
        )
        emit(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": f"echo: {text}"}]},
            }
        )
        result(session_id, f"echo: {text}")
        if scenario == "oneshot":
            # Exits while idle between turns, like a crash or an external kill.
            sys.exit(0)


if __name__ == "__main__":
    main()
