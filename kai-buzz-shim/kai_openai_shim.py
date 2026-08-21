#!/usr/bin/env python3
"""
OpenAI-compatible shim so KAI + advisors can be created as NATIVE Buzz Agents.
KAI-984 comms spike (24e49013). Productionized into the kai-system stack as the
durable `kai-buzz-shim` service 2026-08-15 (KAI-1029) after the host nohup version
was retired by a cleanup sweep (122ac4ec, 2026-08-03) and left Leo's native agents
with no backend — every advisor DM silently unanswered from Aug 4 onward.

Exposes /v1/models and /v1/chat/completions. Config is env-driven so it runs the
same in-container (compose service names) or host-side (localhost). Models:
  kai | sky | roads | beats | coach -> the real council orchestrator (per-model channel)
  ember* -> local inference (litellm qwen-mid -> the Mac mini)

Buzz "New Agent -> Buzz Agent -> OpenAI-compatible provider" points here:
  base_url = http://<worker>:4001/v1   model = kai | sky | roads | beats | coach
Everything stays on Leo's tailnet.
"""
import os, json, base64, urllib.request, time, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000/v1/chat/completions")
LITELLM_KEY = open(os.environ.get("LITELLM_KEY_FILE", "/home/leo/kai-system/secrets/litellm_master_key.txt")).read().strip()
COUNCIL_URL = os.environ.get("BUZZ_COUNCIL_URL", "http://localhost:3001/council/message")
WEB_USER = os.environ.get("BUZZ_WEB_USER", "kai")
WEB_PW = open(os.environ.get("KAI_WEB_PW_FILE", "/home/leo/kai-system/secrets/kai_web_password.txt")).read().strip()
API_KEY = "buzz-eval"  # the key Buzz Agent config uses (any-string; tailnet-gated anyway)

MODELS = ["kai", "sky", "roads", "beats", "coach"]  # public advisors only; Ember+Doc off Buzz until AR-5.4 egress gate


def _last_user(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            return m.get("content", "")
    return (messages or [{}])[-1].get("content", "")


def call_council(text, channel="kai"):
    body = json.dumps({"channel": channel, "message": text, "user_id": "leo",
                       "trigger_source": "webhook:buzz-agent"}).encode()
    basic = base64.b64encode(f"{WEB_USER}:{WEB_PW}".encode()).decode()
    req = urllib.request.Request(COUNCIL_URL, data=body, method="POST",
        headers={"Authorization": f"Basic {basic}", "Content-Type": "application/json"})
    # KAI-1182: was 180s. A council STALL let request threads live up to 3 min each and
    # pile up, a contributing factor in the 2026-08-21 shim wedge (2.5h connection-refused
    # outage). 90s caps thread lifetime without severing legitimate slow-but-valid replies
    # (the advisor_dm_probe's own round-trip bound is 90s). Autoheal (buzz_shim_watchdog)
    # is the primary durability fix; this is defense-in-depth against the pileup trigger.
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read()).get("reply", "(no reply)")


def call_ember(messages):
    body = json.dumps({"model": "qwen-mid", "messages": messages,
                       "max_tokens": 600, "temperature": 0.6}).encode()
    req = urllib.request.Request(LITELLM_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def completion(model, reply):
    return {"id": f"chatcmpl-{int(time.time())}", "object": "chat.completion",
            "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


_CHAN_RE = re.compile(r"#([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _buzz_shell_tool(tools):
    """The buzz-dev-mcp shell tool buzz-agent must call to publish (name ends __shell)."""
    for t in (tools or []):
        fn = t.get("function", t)
        name = fn.get("name", "")
        if name == "shell" or name.endswith("__shell"):
            return name
    return None


def _buzz_channel(messages):
    """The current channel/DM uuid from the [Context] block."""
    for m in reversed(messages or []):
        mt = _CHAN_RE.search(str(m.get("content", "")))
        if mt:
            return mt.group(1)
    return None


def completion_toolcall(model, tool_name, command):
    now = int(time.time())
    return {"id": f"chatcmpl-{now}", "object": "chat.completion", "created": now, "model": model,
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None,
                    "tool_calls": [{"id": f"call_{now}", "type": "function",
                        "function": {"name": tool_name, "arguments": json.dumps({"command": command})}}]}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        print(f"[{time.strftime('%H:%M:%S')}]", self.address_string(), *a, flush=True)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "kai"} for m in MODELS]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        model = (body.get("model") or "kai").lower()
        # KAI-1154: MODELS is the SOLE authority for both the /v1/models listing
        # AND dispatch. Reject anything not listed (ember/doc) so the
        # "off Buzz until AR-5.4" gate is enforced, not merely advertised.
        if model not in MODELS:
            return self._send(404, {"error": {"message": f"model {model!r} not available",
                "type": "invalid_request_error", "code": "model_not_found"}})
        messages = body.get("messages", [])
        stream = bool(body.get("stream"))
        # buzz-agent loops: after it runs our publish tool it re-asks with the tool
        # result. The reply is already posted -> end the turn, do NOT re-call council.
        if messages and messages[-1].get("role") == "tool":
            print(f"[{time.strftime('%H:%M:%S')}] tool-result -> turn done", flush=True)
            return self._send(200, completion(model, ""))
        post_tool = _buzz_shell_tool(body.get("tools"))
        channel = _buzz_channel(messages)
        print(f"[{time.strftime('%H:%M:%S')}] chat model={model} channel={channel} tool={post_tool}", flush=True)
        try:
            reply = call_ember(messages) if model.startswith("ember") else call_council(_last_user(messages), channel=model)
        except Exception as e:
            return self._send(502, {"error": {"message": f"backend error: {e}"}})
        # A Buzz agent (has the shell tool + a channel): publish via a tool-call so
        # buzz-agent runs `buzz messages send` and the reply actually appears.
        if post_tool and channel:
            b64 = base64.b64encode(reply.encode("utf-8")).decode()
            cmd = f"echo {b64} | base64 -d | buzz messages send --channel {channel} --content -"
            return self._send(200, completion_toolcall(model, post_tool, cmd))
        # Direct (non-Buzz) API use: return plain text.
        if stream:
            return self._send_stream(model, reply)
        self._send(200, completion(model, reply))

    def _send_stream(self, model, reply):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        cid = f"chatcmpl-{int(time.time())}"
        base = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model}
        def chunk(delta, finish=None):
            d = dict(base); d["choices"] = [{"index": 0, "delta": delta, "finish_reason": finish}]
            self.wfile.write(f"data: {json.dumps(d)}\n\n".encode()); self.wfile.flush()
        chunk({"role": "assistant"})
        chunk({"content": reply})
        chunk({}, finish="stop")
        self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()


if __name__ == "__main__":
    print("KAI OpenAI shim on 0.0.0.0:4001  models:", MODELS,
          "council:", COUNCIL_URL, flush=True)
    ThreadingHTTPServer(("0.0.0.0", 4001), H).serve_forever()
