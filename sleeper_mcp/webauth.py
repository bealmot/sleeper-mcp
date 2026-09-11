"""A one-time local page for handing the server a token safely.

The token must never pass through the model (chat transcripts are logged),
the terminal (scrollback, shell history) or, ideally, the clipboard. So the
server — already a local process — briefly listens on 127.0.0.1 at a random,
single-use URL, and the token travels browser -> loopback -> this process ->
config file (0600). The page offers three ways in, best first:

  1. a one-line snippet run in the console on sleeper.com, which POSTs
     localStorage's token straight here — nothing copied or typed
  2. `copy(localStorage.token)` in that console, then paste into a masked
     field here
  3. the manual DevTools path, spelled out per browser

Whatever arrives is normalised (localStorage stores the token JSON-quoted and
Sleeper rejects it in that form), checked for shape, VERIFIED against Sleeper,
and only then saved. No response, log line or tool result ever contains it.

Why not MCP elicitation? The spec forbids using it for secrets: clients need
not mask the input, and the value can round-trip through the conversation.
A URL the user opens is the sanctioned pattern for sensitive flows.
"""

from __future__ import annotations

import hmac
import html
import http.server
import json
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from . import config

ORIGIN = "https://sleeper.com"
TTL = 300


def normalise(raw: str) -> str:
    """localStorage hands back the token JSON-quoted; humans add whitespace."""
    tok = (raw or "").strip()
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        inner = tok[1:-1]
        try:
            tok = json.loads(tok) if tok[0] == '"' else inner
        except ValueError:
            tok = inner
    return tok.strip()


@dataclass
class Session:
    nonce: str
    enable_writes: bool
    verify: object                      # (token) -> (ok, message)
    ttl: float = TTL
    created: float = field(default_factory=time.monotonic)
    port: int = 0
    result: str | None = None           # set once, never contains the token
    done: threading.Event = field(default_factory=threading.Event)
    _server: object = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/setup/{self.nonce}"

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > self.ttl

    def matches(self, nonce: str) -> bool:
        return hmac.compare_digest(nonce or "", self.nonce)

    def accept(self, raw: str) -> tuple[int, dict]:
        """Validate, verify and save. Returns (http status, json body)."""
        if self.done.is_set():
            return 410, {"ok": False, "message": "this setup link was already used"}
        if self.expired:
            return 410, {"ok": False, "message": "this setup link has expired"}
        tok = normalise(raw)
        problems = config.diagnose_token(tok)
        if problems:
            return 400, {"ok": False, "message": "not saved — the token "
                                                  + "; ".join(problems)}
        ok, msg = self.verify(tok)
        if not ok:
            return 400, {"ok": False, "message": f"not saved — {msg}"}
        updates = {"token": tok}
        if self.enable_writes:
            updates["enable_writes"] = "1"
        config.save(updates)
        # Make the running server use it immediately, no restart needed.
        from . import client
        client.TOKEN = tok
        if self.enable_writes:
            client.WRITES_ENABLED = True
        self.result = (f"token verified and saved for {msg}; writes "
                       f"{'enabled' if self.enable_writes else 'unchanged'}")
        self.done.set()
        return 200, {"ok": True, "message": self.result}


def _page(s: Session) -> str:
    base = f"http://127.0.0.1:{s.port}"
    post = f"{base}/token/{s.nonce}"
    snippet = (f'fetch("{post}",{{method:"POST",headers:{{"content-type":'
               f'"text/plain"}},body:localStorage.getItem("token")}})'
               f'.then(r=>r.json()).then(j=>console.log("sleeper-mcp: "+j.message))'
               f'.catch(()=>console.log("sleeper-mcp: blocked — run '
               f'copy(localStorage.token) and paste it into the setup page"))')
    e = html.escape
    return f"""<!doctype html><meta charset="utf-8"><title>sleeper-mcp setup</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:46em;margin:3em auto;padding:0 1em;color:#222}}
code,pre{{background:#f3f3f3;padding:.15em .35em;border-radius:4px}}pre{{padding:.8em;overflow:auto;white-space:pre-wrap;word-break:break-all}}
h2{{margin-top:2em;font-size:1.1em}}input{{width:100%;padding:.5em;font:inherit}}button{{padding:.5em 1em;font:inherit}}
.ok{{color:#176b2a}}.bad{{color:#a11}}small{{color:#666}}</style>
<h1>sleeper-mcp — add your Sleeper token</h1>
<p>This page is served by the sleeper-mcp process on your own machine, at a
one-time address that expires in 5 minutes. The token goes from your browser
to that process and into <code>{e(str(config.config_path()))}</code> (mode 0600).
It is never shown, logged, or sent anywhere but Sleeper (once, to verify it).</p>

<h2>Option 1 — one line, nothing to copy <small>(recommended)</small></h2>
<p>In the tab where you are logged in to <b>sleeper.com</b>, open the developer
console (<kbd>F12</kbd> or <kbd>Ctrl</kbd>/<kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>J</kbd>,
then the <b>Console</b> tab), paste this, press Enter:</p>
<pre id="snip">{e(snippet)}</pre>
<p><button onclick="navigator.clipboard.writeText(document.getElementById('snip').textContent)">copy snippet</button>
<small>Browsers warn about pasting into the console for good reason — read it: it
sends <code>localStorage.token</code> to <code>127.0.0.1</code>, nowhere else.</small></p>

<h2>Option 2 — copy from the console, paste here</h2>
<p>In that same console run <code>copy(localStorage.token)</code>, then paste below.
Quotes around it are fine; they are removed.</p>
<form method="post" action="{e(post)}" onsubmit="return send(event)">
<input type="password" name="token" placeholder="paste token" autocomplete="off" spellcheck="false">
<p><button type="submit">verify &amp; save</button> <span id="out"></span></p></form>

<h2>Option 3 — find it by hand</h2>
<ul>
<li><b>Chrome / Edge / Brave:</b> F12 → <b>Application</b> → Local Storage → <code>https://sleeper.com</code> → key <code>token</code></li>
<li><b>Firefox:</b> F12 → <b>Storage</b> → Local Storage → <code>https://sleeper.com</code> → <code>token</code></li>
<li><b>Safari:</b> enable the Develop menu in Settings → Advanced, then Develop → Show Web Inspector → <b>Storage</b></li>
</ul>
<p>Copy the value and use Option 2. The token is account-scoped and lasts about
a year; to revoke it, log out of Sleeper everywhere.</p>
<script>
async function send(ev){{ev.preventDefault();const out=document.getElementById('out');
const tok=ev.target.token.value;ev.target.token.value='';
try{{const r=await fetch({json.dumps(post)},{{method:'POST',headers:{{'content-type':'text/plain'}},body:tok}});
const j=await r.json();out.className=j.ok?'ok':'bad';out.textContent=j.message;
if(j.ok){{try{{await navigator.clipboard.writeText('');}}catch(_){{}}}}}}
catch(e){{out.className='bad';out.textContent='could not reach the local server — has the link expired?';}}return false;}}
</script>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    session: Session

    def log_message(self, *_):            # never log paths or bodies
        pass

    def _origin_ok(self) -> bool:
        """CORS headers do NOT gate this endpoint — so check the origin.

        Access-Control-Allow-Origin controls whether a PAGE MAY READ A
        RESPONSE. It does not stop the request being delivered and executed:
        a POST with content-type text/plain is a CORS "simple request" and is
        sent with no preflight at all. The nonce is what genuinely protects
        this listener, and it does; this is the cheap second lock, and it
        makes the code mean what its ORIGIN constant implies.

        A missing Origin is allowed — curl and a plain form post have none,
        while a cross-site request always carries one.
        """
        got = self.headers.get("Origin")
        return got in (None, "", ORIGIN, f"http://127.0.0.1:{self.session.port}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "300")
        self.send_header("Vary", "Origin")

    def _send(self, status: int, body: bytes, ctype: str, cors=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if cors:
            self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> tuple[str, str]:
        parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
        return (parts[0], parts[1]) if len(parts) == 2 else ("", "")

    def do_GET(self):
        kind, nonce = self._route()
        s = self.session
        if kind == "setup" and s.matches(nonce) and not s.expired and not s.done.is_set():
            return self._send(200, _page(s).encode(), "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")

    def do_OPTIONS(self):
        kind, nonce = self._route()
        if kind == "token" and self.session.matches(nonce) and self._origin_ok():
            return self._send(204, b"", "text/plain", cors=True)
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        kind, nonce = self._route()
        s = self.session
        if kind != "token" or not s.matches(nonce) or not self._origin_ok():
            return self._send(404, b"not found", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(min(n, 65536)).decode("utf-8", "replace")
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype == "application/x-www-form-urlencoded":
            raw = urllib.parse.parse_qs(raw).get("token", [""])[0]
        elif ctype == "application/json":
            try:
                raw = str(json.loads(raw).get("token", ""))
            except (ValueError, AttributeError):
                raw = ""
        status, body = s.accept(raw)
        self._send(status, json.dumps(body).encode(), "application/json", cors=True)
        if s.done.is_set():
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def start(enable_writes: bool = False, verify=None, ttl: float = TTL) -> Session:
    """Begin a session on an ephemeral loopback port. Returns immediately."""
    if verify is None:
        from .setup import verify_token
        verify = verify_token
    s = Session(nonce=secrets.token_urlsafe(24), enable_writes=enable_writes,
                verify=verify, ttl=ttl)
    handler = type("H", (_Handler,), {"session": s})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    s.port, s._server = srv.server_address[1], srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def reaper():
        s.done.wait(ttl)
        srv.shutdown()
        if not s.done.is_set():
            s.result = "setup link expired unused"
            s.done.set()
    threading.Thread(target=reaper, daemon=True).start()
    return s
