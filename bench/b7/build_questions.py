#!/usr/bin/env python3
"""Build bench/b7/questions.jsonl from questions_spec.py, verifying every quote and key fact.

usage: B7_CACHE=<dir> python3 -I bench/b7/build_questions.py --kafka <apache/kafka clone at the pinned sha>
       [--spec questions_spec_v2 --out bench/b7/questions_v2.jsonl --checks reports/b7/question_checks_v2.tsv]

Fetches (politely, cached; see asf.py) each cited KIP page, Jira issue and 4.3 docs page; reads
code from the clone. Fails if a quote is not in its source, a key fact is not in any cited source,
or a Jira source is not a Fixed issue created since 2022-01-01. Also writes
reports/b7/question_checks.tsv: per question, how many key facts occur anywhere in the repo
(git grep), split into non-docs files and docs/ markdown, to back the code-only labels.

Any --spec other than the default questions_spec is also linted: no question may contain "KIP-",
"KAFKA-" or "KIP <digit>", or a 4+ word run from a cited KIP title or Jira summary, and no key fact
may be a KIP number or Jira key (the lookup key must not be in the question or required in the answer).
"""
import argparse, importlib, json, os, re, subprocess, sys, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asf  # noqa: E402
from textify import text, norm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
SHA = open(os.path.join(HERE, "kafka_trunk.sha")).read().strip()
CONF = "https://cwiki.apache.org/confluence"
JIRA = "https://issues.apache.org/jira"
DOCS = "https://kafka.apache.org/43/"


def loose(s):
    return re.sub(r"\s+", "", norm(s))


_kip_titles = {}


def kip_titles():
    if not _kip_titles:
        r = asf.get_json(f"{CONF}/rest/api/content", {"spaceKey": "KAFKA", "title": "Kafka Improvement Proposals", "expand": "body.storage,version"})
        body = r["results"][0]["body"]["storage"]["value"]
        for m in re.finditer(r'ri:content-title="(KIP-(\d+)[^"]*)"|>(KIP-(\d+)[^<]*)<', body):
            title, num = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            _kip_titles.setdefault("KIP-" + num, __import__("html").unescape(title))
    return _kip_titles


def load(kind, ident, kafka):
    """Return (url, plain text, meta) for a source."""
    if kind == "kip":
        title = kip_titles()[ident]
        r = asf.get_json(f"{CONF}/rest/api/content", {"spaceKey": "KAFKA", "title": title, "expand": "body.storage"})
        page = r["results"][0]
        # the page title (which carries the KIP number) is part of what a reader sees
        return f"{CONF}/display/KAFKA/" + urllib.parse.quote_plus(title), title + "\n" + text(page["body"]["storage"]["value"]), {}
    if kind == "jira":
        f = asf.get_json(f"{JIRA}/rest/api/2/issue/{ident}", {"fields": "summary,description,fixVersions,created,resolution,issuetype"})["fields"]
        fv = ", ".join(v["name"] for v in f["fixVersions"])
        body = f"{ident}: {f['summary']}\n{text(f.get('description') or '')}\nFix Version/s: {fv}"
        meta = {"summary": f["summary"], "created": f["created"][:10], "resolution": (f.get("resolution") or {}).get("name"), "fixVersions": fv}
        return f"{JIRA}/browse/{ident}", body, meta
    if kind == "docs":
        url = f"{DOCS}{ident}/"
        h = asf.get(url, accept="text/html")
        m = re.search(r"(?s)<main\b.*?</main>", h) or re.search(r"(?s)<body\b.*?</body>", h)
        return url, text(m.group(0)), {}
    if kind == "code":
        src = subprocess.run(["git", "-C", kafka, "show", f"{SHA}:{ident}"], capture_output=True, text=True, check=True).stdout
        return f"https://github.com/apache/kafka/blob/{SHA}/{ident}", src, {}
    raise ValueError(kind)


ID_RE = re.compile(r"KIP-|KAFKA-|KIP \d")  # case-sensitive: kafka-configs.sh is fine


def words4(s):
    w = re.findall(r"[a-z0-9]+", s.lower())
    return {" ".join(w[i:i + 4]) for i in range(len(w) - 3)}


def lint(qid, q, titles):
    """Errors if the question hands over the lookup key: an id, or a 4+ word run of a cited title."""
    errs = [f"{qid}: question contains an id: {m.group(0)!r}" for m in ID_RE.finditer(q["q"])]
    errs += [f"{qid}: key fact is an id: {f!r}" for f in q["facts"] if ID_RE.search(f)]
    for t in titles:
        for run in sorted(words4(q["q"]) & words4(re.sub(r"^KIP-\d+:?\s*", "", t))):
            errs.append(f"{qid}: question repeats title words {run!r} from {t!r}")
    return errs


def repo_hits(kafka, fact):
    """(files outside docs/, files under docs/) at the pinned commit containing fact verbatim."""
    # working tree == SHA (asserted in main) and is much faster than grepping the commit's tree
    out = subprocess.run(["git", "-C", kafka, "grep", "-l", "-F", "-e", fact], capture_output=True, text=True).stdout.split("\n")
    out = [p for p in out if p]
    docs = [p for p in out if p.startswith("docs/")]
    return len(out) - len(docs), len(docs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka", required=True)
    ap.add_argument("--out", default=os.path.join(HERE, "questions.jsonl"))
    ap.add_argument("--report", "--checks", default=os.path.join(ROOT, "reports", "b7", "question_checks.tsv"))
    ap.add_argument("--spec", default="questions_spec", help="spec module in bench/b7 (non-default specs are linted)")
    args = ap.parse_args()
    spec = importlib.import_module(args.spec)
    prefix = getattr(spec, "ID_PREFIX", "b7")
    linted = args.spec != "questions_spec"
    head = subprocess.run(["git", "-C", args.kafka, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == SHA, f"clone is at {head}, expected {SHA}"

    errors, rows, report = [], [], []
    cache = {}
    for qtype, items in spec.SECTIONS:
        for i, q in enumerate(items, 1):
            qid = f"{prefix}-{qtype}-{i:02d}"
            sources, texts, titles = [], [], []
            for kind, ident, quote in q["src"]:
                if (kind, ident) not in cache:
                    cache[kind, ident] = load(kind, ident, args.kafka)
                url, body, meta = cache[kind, ident]
                texts.append(body)
                if kind == "kip":
                    titles.append(kip_titles()[ident])
                elif kind == "jira":
                    titles.append(meta["summary"])
                if loose(quote) not in loose(body):
                    errors.append(f"{qid}: quote not found in {kind} {ident}: {quote[:80]!r}")
                if kind == "jira" and (meta["created"] < "2022-01-01" or meta["resolution"] != "Fixed"):
                    errors.append(f"{qid}: {ident} created {meta['created']} resolution {meta['resolution']}")
                if kind != "code":  # undo tag-boundary spaces (" ," / "( x )") left by HTML-to-text; the check above ignores whitespace
                    quote = re.sub(r"\(\s+", "(", re.sub(r"\s+([,.;:)])", r"\1", quote))
                sources.append({"kind": kind, "id_or_url": url, "quote": quote})
            if linted:
                errors += lint(qid, q, titles)
            if not 1 <= len(q["facts"]) <= 4:
                errors.append(f"{qid}: {len(q['facts'])} key facts")
            alltext = loose(" ".join(texts))
            for fact in q["facts"]:
                if loose(fact) not in alltext:
                    errors.append(f"{qid}: key fact {fact!r} not in cited sources")
            hits = [repo_hits(args.kafka, f) for f in q["facts"]]
            report.append([qid, qtype, str(q["code_only"]).lower(),
                           f"{sum(1 for c, d in hits if c)}/{len(hits)}",
                           f"{sum(1 for c, d in hits if c or d)}/{len(hits)}",
                           "; ".join(f"{f}={c}+{d}docs" for f, (c, d) in zip(q["facts"], hits))])
            rows.append({"id": qid, "type": qtype, "question": q["q"], "answer": q["a"], "key_facts": q["facts"],
                         "sources": sources, "answerable_from_code_only": q["code_only"]})
            if "from_id" in q:
                rows[-1]["from_id"] = q["from_id"]

    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(f"{len(errors)} check(s) failed; nothing written")
    with open(args.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as f:
        f.write("id\ttype\tcode_only_label\tfacts_in_code_files\tfacts_in_repo_incl_docs\tper_fact_hits(code+docs)\n")
        for r in report:
            f.write("\t".join(r) + "\n")
    counts = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    print(f"wrote {len(rows)} questions {counts}; ASF requests logged so far: {asf.requests_made()}")


if __name__ == "__main__":
    main()
