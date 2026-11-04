"""B7 test proxy: lets PipesHub's Jira Data Center connector read Apache's public Jira anonymously.

The connector checks its login with GET /rest/api/2/myself before it will enable, and Apache's Jira
answers 401 there to anonymous callers while serving every other read anonymously. This answers that
one call with a fixed anonymous user and forwards everything else, read-only and without credentials,
to issues.apache.org, at most a few requests a second. Test infrastructure only; nothing in PipesHub
changes. Standard library only, so it runs in a plain python image.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://issues.apache.org/jira"
MIN_INTERVAL = 0.35  # seconds between upstream requests
_lock = threading.Lock()
_last = [0.0]
ME = {"self": f"{UPSTREAM}/rest/api/2/user?username=anonymous", "key": "anonymous", "name": "anonymous",
      "emailAddress": "anonymous@example.invalid", "displayName": "Anonymous (B7 proxy)", "active": True,
      "timeZone": "UTC", "locale": "en_US", "groups": {"size": 0, "items": []}}


class Proxy(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self, method: str) -> None:
        path = self.path[len("/jira"):] if self.path.startswith("/jira") else self.path
        if path.split("?")[0].rstrip("/") == "/rest/api/2/myself":
            self._send(200, json.dumps(ME).encode())
            return
        if method != "GET":  # read-only: the connector only reads
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not path.startswith("/rest/api/2/search"):
                self._send(405, b'{"errorMessages":["B7 proxy is read-only"]}')
                return
        else:
            body = None
        with _lock:
            wait = MIN_INTERVAL - (time.monotonic() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.monotonic()
        req = urllib.request.Request(UPSTREAM + path, data=body, method=method,
                                     headers={"Accept": self.headers.get("Accept", "application/json"),
                                              "Content-Type": self.headers.get("Content-Type", "application/json"),
                                              "User-Agent": "pipeshub-b7-benchmark (read-only, rate-limited)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                self._send(r.status, r.read(), r.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except Exception as e:  # upstream unreachable: a 502 the connector retries
            self._send(502, json.dumps({"errorMessages": [f"B7 proxy: {type(e).__name__}"]}).encode())

    def do_GET(self) -> None:
        self._forward("GET")

    def do_POST(self) -> None:
        self._forward("POST")

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Proxy).serve_forever()
