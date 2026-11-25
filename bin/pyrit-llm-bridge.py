#!/usr/bin/env python3
"""Local OpenAI-compatible HTTP bridge for PyRIT backed by documented `agy` and `claude` CLIs.

Binds strictly to loopback (127.0.0.1:8787). Uses the user's authenticated monthly
Antigravity (`agy -p`) and Claude Code (`claude -p`) CLIs via their official print modes.

Supported models:
  - gemini-neon       -> agy --model gemini-4-neon-low
  - gemini-neon-high  -> agy --model gemini-4-neon-high
  - gemini-flash      -> agy --model gemini-3.8-flash-low
  - claude-sonnet     -> claude --model sonnet -p
  - claude-haiku      -> claude --model haiku -p
  - claude-opus       -> claude --model opus -p
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import time
import uuid
from typing import Any

HOME = os.path.expanduser("~")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787

AGY_MODELS: dict[str, str] = {
    "gemini-neon": "gemini-4-neon-low",
    "gemini-4-neon-low": "gemini-4-neon-low",
    "gemini-neon-high": "gemini-4-neon-high",
    "gemini-4-neon-high": "gemini-4-neon-high",
    "gemini-flash": "gemini-3.8-flash-low",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini": "gemini-4-neon-low",
}

CLAUDE_MODELS: dict[str, str] = {
    "claude": "sonnet",
    "claude-sonnet": "sonnet",
    "claude-haiku": "haiku",
    "claude-opus": "opus",
    "sonnet": "sonnet",
    "haiku": "haiku",
    "opus": "opus",
}

PUBLIC_MODEL_LIST = [
    "gemini-neon",
    "gemini-neon-high",
    "gemini-flash",
    "claude-sonnet",
    "claude-haiku",
    "claude-opus",
]


def find_bin(name: str, fallback: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    if os.path.isfile(fallback):
        return fallback
    return name


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return str(content or "")


def format_messages(messages: list[dict[str, Any]], response_format: Any = None) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) flattened cleanly for print-mode CLIs."""
    sys_parts: list[str] = []
    convo_parts: list[str] = []

    for msg in messages:
        role = str(msg.get("role", "user")).strip().lower()
        text = extract_text(msg.get("content", "")).strip()
        if not text:
            continue
        if role in ("system", "developer"):
            sys_parts.append(text)
        elif role == "assistant":
            convo_parts.append(f"Assistant:\n{text}")
        else:
            convo_parts.append(f"User:\n{text}")

    if isinstance(response_format, dict):
        rf_type = response_format.get("type", "")
        if rf_type in ("json_object", "json_schema"):
            sys_parts.append(
                "IMPORTANT: Reply with valid JSON ONLY. No markdown code fences, no preamble, no trailing text."
            )

    system_text = "\n\n".join(sys_parts).strip()

    # If there is only one user message and no multi-turn history, send raw text cleanly.
    if len(convo_parts) == 1 and convo_parts[0].startswith("User:\n"):
        user_text = convo_parts[0][len("User:\n"):]
    else:
        user_text = "\n\n".join(convo_parts).strip()

    return system_text, user_text


def call_agy(model_slug: str, system_text: str, user_text: str, timeout: int = 110) -> str:
    agy = find_bin(
        "agy",
        os.path.join(HOME, ".local/share/mise/installs/antigravity-cli/latest/agy"),
    )
    prompt = f"{system_text}\n\n---\n\n{user_text}" if system_text else user_text
    cmd = [
        agy,
        "--model",
        model_slug,
        "--disable-slash-commands",
        "--print-timeout",
        f"{max(timeout - 10, 30)}s",
        "-p",
        prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        err = (proc.stderr or "").strip() or f"agy exited with code {proc.returncode}"
        raise RuntimeError(err)
    return out


def call_claude(model_alias: str, system_text: str, user_text: str, timeout: int = 110) -> str:
    claude = find_bin(
        "claude",
        os.path.join(HOME, ".local/share/mise/installs/claude/latest/claude"),
    )
    cmd = [
        claude,
        "-p",
        "--no-session-persistence",
        "--tools",
        "",
        "--output-format",
        "text",
        "--model",
        model_alias,
    ]
    if system_text:
        cmd.extend(["--system-prompt", system_text])
    cmd.append(user_text or "Hello")

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        err = (proc.stderr or "").strip() or f"claude exited with code {proc.returncode}"
        raise RuntimeError(err)
    return out


def dispatch_completion(model: str, messages: list[dict[str, Any]], response_format: Any = None) -> tuple[str, str]:
    key = (model or "gemini-neon").strip().lower()
    system_text, user_text = format_messages(messages, response_format=response_format)

    if key in CLAUDE_MODELS or "claude" in key:
        alias = CLAUDE_MODELS.get(key)
        if not alias:
            if "haiku" in key:
                alias = "haiku"
            elif "opus" in key:
                alias = "opus"
            else:
                alias = "sonnet"
        reply = call_claude(alias, system_text, user_text)
        return f"claude-{alias}", reply

    slug = AGY_MODELS.get(key, "gemini-4-neon-low")
    reply = call_agy(slug, system_text, user_text)
    return key if key in AGY_MODELS else "gemini-neon", reply


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"[pyrit-bridge] {self.address_string()} - {format % args}\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/health", "/v1/health"):
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "pyrit-llm-bridge",
                    "models": PUBLIC_MODEL_LIST,
                },
            )
            return
        if path in ("/v1/models", "/models"):
            now = int(time.time())
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {"id": m, "object": "model", "created": now, "owned_by": "local-cli-bridge"}
                        for m in PUBLIC_MODEL_LIST
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": f"Not found: {path}", "type": "not_found"}})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": {"message": f"Unsupported endpoint: {path}", "type": "not_found"}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length > 0 else b"{}"
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": {"message": f"Invalid JSON: {e}", "type": "invalid_request_error"}})
            return

        model = str(req.get("model") or "gemini-neon")
        messages = req.get("messages") or []
        response_format = req.get("response_format")

        if not isinstance(messages, list) or not messages:
            self._send_json(400, {"error": {"message": "messages must be a non-empty list", "type": "invalid_request_error"}})
            return

        try:
            resolved_model, reply = dispatch_completion(model, messages, response_format=response_format)
        except subprocess.TimeoutExpired:
            self._send_json(504, {"error": {"message": f"CLI backend timed out for model {model}", "type": "timeout"}})
            return
        except Exception as e:
            self._send_json(502, {"error": {"message": str(e), "type": "bridge_error"}})
            return

        now = int(time.time())
        prompt_tokens = sum(len(extract_text(m.get("content", "")).split()) for m in messages)
        completion_tokens = len(reply.split())

        self._send_json(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
                "object": "chat.completion",
                "created": now,
                "model": resolved_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Local OpenAI-compatible CLI bridge for PyRIT")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("Refusing non-loopback bind for local CLI bridge.", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print(f"pyrit-llm-bridge listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
