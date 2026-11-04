"""Polite, cached fetcher for the public ASF sources B7 uses (Jira, Confluence, kafka.apache.org).

Sequential, >=1 s between network requests, every response cached on disk, every network
request appended to <cache>/requests.log so the total stays countable.
Cache dir: $B7_CACHE (default: ./.b7cache next to this file).
"""
import hashlib, json, os, time, urllib.parse, urllib.request

CACHE = os.environ.get("B7_CACHE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".b7cache")
BUDGET = int(os.environ.get("B7_BUDGET", "1500"))
UA = "pipeshub-codegraph-eval-b7/1.0 (benchmark question builder; low rate)"
_last = [0.0]


def _count():
    p = os.path.join(CACHE, "requests.log")
    return sum(1 for _ in open(p)) if os.path.exists(p) else 0


def get(url, params=None, accept="application/json"):
    """Return the response body (str) for url, from cache when present."""
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest())
    if os.path.exists(path):
        return open(path, encoding="utf-8").read()
    if _count() >= BUDGET:
        raise RuntimeError(f"request budget {BUDGET} exhausted; not fetching {url}")
    wait = 1.0 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 2:
                time.sleep(30)
                continue
            raise
        finally:
            _last[0] = time.time()
            with open(os.path.join(CACHE, "requests.log"), "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}\t{url}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return body


def get_json(url, params=None):
    return json.loads(get(url, params))


def requests_made():
    return _count()
