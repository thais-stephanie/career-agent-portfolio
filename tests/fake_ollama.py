"""A fake Ollama on loopback, for tests of the local reading end to end.

A real HTTP server on 127.0.0.1 with the three endpoints the product uses
(`/api/tags`, `/api/ps`, a STREAMED `/api/chat`), so the real transport, its
deadline and its cancellation are exercised rather than replaced. It never
touches the network beyond the loopback interface and needs no model.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Behaviour:
    models: list[str] = field(default_factory=lambda: ["qwen3:4b"])
    loaded: list[str] = field(default_factory=list)
    #: The quote the answer cites; must be text the posting contains.
    quote: str = ""
    #: Seconds before the response headers (a cold model load: real Ollama
    #: sends nothing at all until the model is in memory).
    header_delay: float = 0.0
    #: Seconds before the first chunk (a model load / prompt read).
    first_delay: float = 0.0
    #: Chunks of the answer and the pause between them.
    chunks: int = 8
    chunk_delay: float = 0.0
    done_reason: str = "stop"
    #: Filled in by the server: requests seen, and whether a client left early.
    chats: list[dict[str, Any]] = field(default_factory=list)
    disconnects: int = 0


class FakeOllama:
    def __init__(self) -> None:
        self.behaviour = Behaviour()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # quiet
                pass

            def _json(self, status: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                b = fake.behaviour
                if self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m} for m in b.models]})
                elif self.path == "/api/ps":
                    self._json(200, {"models": [{"name": m} for m in b.loaded]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                b = fake.behaviour
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                b.chats.append(body)
                answer = json.dumps(
                    {
                        "summary": "A synthetic reading of the posting.",
                        "technologies": [{"text": "A tool the posting names", "quote": b.quote}],
                        "strengths": [],
                        "gaps": [],
                        "risk_flags": [],
                        "recommended_action": "READ_IN_FULL",
                        "confidence": "MEDIUM",
                    }
                )
                if b.done_reason == "length":
                    answer = answer[: len(answer) // 2]
                size = max(1, len(answer) // max(1, b.chunks))
                pieces = [answer[i : i + size] for i in range(0, len(answer), size)]
                try:
                    time.sleep(b.header_delay)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    time.sleep(b.first_delay)
                    for piece in pieces:
                        line = {"message": {"role": "assistant", "content": piece}, "done": False}
                        self.wfile.write((json.dumps(line) + "\n").encode())
                        self.wfile.flush()
                        time.sleep(b.chunk_delay)
                    done = {
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": b.done_reason,
                        "eval_count": len(pieces),
                    }
                    self.wfile.write((json.dumps(done) + "\n").encode())
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    b.disconnects += 1

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_address[1])
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FakeOllama:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
