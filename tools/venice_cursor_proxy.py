#!/usr/bin/env python3
"""
OpenAI-compatible local proxy so Venice works with Cursor Agent.

Venice returns {"error":"list object has no element 0"} when the chat history
contains OpenAI ``tool`` role messages (Cursor Agent always sends these).
This proxy rewrites tool turns into plain user/assistant text and strips
``tools`` / ``tool_choice`` before forwarding to Venice.

Cursor settings:
  Override OpenAI Base URL: http://127.0.0.1:8791/v1
  OpenAI API Key: your Venice inference key (or set env VENICE_API_KEY)

Run:
  python tools/venice_cursor_proxy.py
  # or: .\\Start-VeniceCursorProxy.ps1
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

VENICE_BASE = os.environ.get("VENICE_API_BASE", "https://api.venice.ai/api/v1").rstrip("/")
VENICE_DEFAULT_MODEL = os.environ.get("VENICE_DEFAULT_MODEL", "venice-uncensored").strip()
# Used when Cursor Agent attaches images but selected model is text-only.
VENICE_VISION_MODEL = os.environ.get("VENICE_VISION_MODEL", "qwen-3-6-plus").strip()
VENICE_VISION_MODELS: frozenset[str] = frozenset(
    m.strip()
    for m in os.environ.get(
        "VENICE_VISION_MODELS",
        "qwen-3-6-plus,qwen-3-7-plus,qwen-3-7-max,gemini-3-1-pro-preview",
    ).split(",")
    if m.strip()
)
# Cursor custom model ids → Venice API ids (optional remap).
VENICE_MODEL_ALIASES: dict[str, str] = {
    "venice-uncensored": "venice-uncensored",
    "venice-uncensored-1-2": "venice-uncensored-1-2",
    "qwen-3-6-plus": "qwen-3-6-plus",
    "olafangensan-glm-4.7-flash-heretic": "olafangensan-glm-4.7-flash-heretic",
}
HOST = os.environ.get("VENICE_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("VENICE_PROXY_PORT", "8791"))


def _content_has_images(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = str(part.get("type") or "")
        if ptype in ("image_url", "input_image", "image"):
            return True
    return False


def _messages_have_images(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        if isinstance(msg, dict) and _content_has_images(msg.get("content")):
            return True
    return False


def _content_to_str(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, dict) and part.get("type") in (
                "image_url",
                "input_image",
                "image",
            ):
                parts.append("[image omitted — model has no vision]")
        return "\n".join(p for p in parts if p)
    return str(content)


def _content_for_model(content: Any, *, vision: bool) -> str | list[Any]:
    """Plain string for text-only models; preserve multimodal parts for vision models."""
    if vision:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: list[Any] = []
            for part in content:
                if isinstance(part, dict):
                    out.append(part)
            if out:
                return out
        return _content_to_str(content)
    return _content_to_str(content)


def sanitize_messages(
    messages: list[dict[str, Any]], *, vision: bool
) -> list[dict[str, Any]]:
    """Convert tool-call history into plain text Venice accepts."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user")
        if role == "tool":
            tid = msg.get("tool_call_id", "")
            out.append(
                {
                    "role": "user",
                    "content": f"[tool result {tid}]: {_content_to_str(msg.get('content'))}",
                }
            )
            continue
        if role == "assistant" and msg.get("tool_calls"):
            parts: list[str] = []
            text = _content_to_str(msg.get("content"))
            if text:
                parts.append(text)
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = fn.get("name", "tool")
                args = fn.get("arguments", "")
                parts.append(f"[assistant tool call {name}: {args}]")
            out.append(
                {
                    "role": "assistant",
                    "content": "\n".join(parts) if parts else "(tool call)",
                }
            )
            continue
        out.append(
            {
                "role": role,
                "content": _content_for_model(msg.get("content"), vision=vision),
            }
        )
    return out


def resolve_model(body: dict[str, Any]) -> tuple[str, bool]:
    """Return (venice_model_id, needs_vision)."""
    raw = str(body.get("model") or VENICE_DEFAULT_MODEL)
    model = VENICE_MODEL_ALIASES.get(raw, raw)
    raw_messages = list(body.get("messages") or [])
    needs_vision = _messages_have_images(raw_messages)
    if needs_vision and model not in VENICE_VISION_MODELS:
        sys.stderr.write(
            f"  → images in request; upgrading {model} → {VENICE_VISION_MODEL}\n"
        )
        model = VENICE_VISION_MODEL
    return model, model in VENICE_VISION_MODELS


def sanitize_body(body: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
    """Return (sanitized body, had_tool_roles, vision)."""
    clean = dict(body)
    raw_messages = list(clean.get("messages") or [])
    had_tool = any(
        isinstance(m, dict)
        and (m.get("role") == "tool" or (m.get("role") == "assistant" and m.get("tool_calls")))
        for m in raw_messages
    )
    model, vision = resolve_model(clean)
    clean["model"] = model
    clean["messages"] = sanitize_messages(raw_messages, vision=vision)
    for key in (
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "functions",
        "function_call",
    ):
        clean.pop(key, None)
    return clean, had_tool, vision


class VeniceProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def _api_key(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (os.environ.get("VENICE_API_KEY") or "").strip()

    def _forward_headers(self, key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
        }

    def _pipe_response(self, resp: Any) -> None:
        self.send_response(resp.status)
        for header, value in resp.headers.items():
            lower = header.lower()
            if lower in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(header, value)
        self.end_headers()
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/models", "/models"):
            self.send_error(404)
            return
        key = self._api_key()
        if not key:
            self.send_error(401, "Missing Venice API key")
            return
        req = urllib.request.Request(
            f"{VENICE_BASE}/models",
            headers={"Authorization": f"Bearer {key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self._pipe_response(resp)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path not in ("/v1/chat/completions", "/chat/completions"):
            self.send_error(404)
            return
        key = self._api_key()
        if not key:
            self.send_error(401, "Missing Venice API key")
            return
        try:
            body = json.loads(self._read_body() or b"{}")
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON body")
            return
        if not isinstance(body, dict):
            self.send_error(400, "Expected JSON object")
            return
        sanitized, had_tool, vision = sanitize_body(body)
        if not sanitized.get("model"):
            sanitized["model"] = VENICE_DEFAULT_MODEL
        model = sanitized.get("model", "?")
        nmsg = len(sanitized.get("messages") or [])
        sys.stderr.write(
            f"  → model={model} messages={nmsg} vision={'yes' if vision else 'no'}"
            f" tool_history={'yes→rewritten' if had_tool else 'no'}\n"
        )
        payload = json.dumps(sanitized).encode("utf-8")
        req = urllib.request.Request(
            f"{VENICE_BASE}/chat/completions",
            data=payload,
            headers=self._forward_headers(key),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                self._pipe_response(resp)
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(e.read())


def main() -> int:
    if not (os.environ.get("VENICE_API_KEY") or "").strip():
        print(
            "WARN: VENICE_API_KEY is not set; Cursor must send Authorization: Bearer …",
            file=sys.stderr,
        )
    server = ThreadingHTTPServer((HOST, PORT), VeniceProxyHandler)
    print(f"Venice Cursor proxy listening on http://{HOST}:{PORT}/v1", file=sys.stderr)
    print(f"Forwarding to {VENICE_BASE}", file=sys.stderr)
    print(f"Default model (when client omits): {VENICE_DEFAULT_MODEL}", file=sys.stderr)
    print(f"Vision fallback model: {VENICE_VISION_MODEL}", file=sys.stderr)
    print(
        "Cursor → Override OpenAI Base URL: "
        f"http://{HOST}:{PORT}/v1",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
