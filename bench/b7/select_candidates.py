#!/usr/bin/env python3
"""Candidate pools the hand-written questions in questions_spec.py were picked from.

usage: B7_CACHE=<dir> python3 -I bench/b7/select_candidates.py --kafka <clone at the pinned sha>
writes reports/b7/candidates_jira.tsv and reports/b7/candidates_kip.tsv

Jira: commits since 2022-01-01 whose subject starts with KAFKA-N and reads like a bug fix
(not test/doc/flaky/bump), then a Jira search per 100 keys for Bug / Fixed / created >= 2022-01-01;
kept: one commit per issue touching 1-2 main .java/.scala files that still exist, one fixVersion,
description >= 200 chars.
KIP: rows of the "Kafka Improvement Proposals" index whose release column names 3.x or 4.0-4.3
(including "Adopted, targeting 3.6"-style entries; config/class presence was then checked by git grep).
"""
import argparse, html, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asf  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "reports", "b7")
SKIP = re.compile(r"flaky|test|typo|doc|bump|upgrade .* to|checkstyle|javadoc", re.I)
BUGGY = re.compile(r"fix|bug|should|incorrect|wrong|NPE|leak|race|not |avoid|prevent|handle", re.I)


def git(kafka, *a):
    return subprocess.run(["git", "-C", kafka, *a], capture_output=True, text=True, check=True).stdout


def jira(kafka):
    commits = {}
    for line in git(kafka, "log", "--since=2022-01-01", "-E", "--grep=^KAFKA-[0-9]+", "--format=%H%x09%s").splitlines():
        h, s = line.split("\t", 1)
        m = re.match(r"(KAFKA-\d+)", s)
        if m:
            commits.setdefault(m.group(1), []).append((h, s))
    keys = sorted({k for k, cs in commits.items() for _, s in cs if BUGGY.search(s) and not SKIP.search(s)})
    issues = []
    for i in range(0, len(keys), 100):
        jql = "key in (%s) AND created >= 2022-01-01 AND resolution = Fixed AND issuetype = Bug" % ",".join(keys[i:i + 100])
        issues += asf.get_json("https://issues.apache.org/jira/rest/api/2/search", {
            "jql": jql, "maxResults": 100,
            "fields": "summary,created,resolution,fixVersions,description,components,issuetype,resolutiondate"})["issues"]
    rows = []
    for it in issues:
        f, k = it["fields"], it["key"]
        if len(f.get("description") or "") < 200 or len(commits[k]) != 1 or len(f["fixVersions"]) != 1:
            continue
        files = git(kafka, "show", "--name-only", "--format=", commits[k][0][0]).split()
        main = [p for p in files if "/main/" in p and p.endswith((".java", ".scala"))]
        if 1 <= len(main) <= 2 and all(os.path.exists(os.path.join(kafka, p)) for p in main):
            rows.append([k, f["fixVersions"][0]["name"], f["created"][:10], f["summary"], " ".join(main)])
    return len(keys), rows


def kips():
    r = asf.get_json("https://cwiki.apache.org/confluence/rest/api/content",
                     {"spaceKey": "KAFKA", "title": "Kafka Improvement Proposals", "expand": "body.storage,version"})
    body = r["results"][0]["body"]["storage"]["value"]
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 2:
            continue
        m = re.search(r'ri:content-title="(KIP-\d+[^"]*)"', tds[0]) or re.search(r">(KIP-\d+[^<]*)<", tds[0])
        rel = re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", tds[1]))).strip()
        if m and re.search(r"(?<![\d.])(3\.\d|4\.[0-3])(?![\d])", rel) and not re.match(r"(TBD|Accepted)", rel):
            rows.append([html.unescape(m.group(1)).strip(), rel])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kafka", required=True)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    n, jrows = jira(args.kafka)
    with open(os.path.join(OUT, "candidates_jira.tsv"), "w") as f:
        f.write("key\tfixVersion\tcreated\tsummary\tmain_files\n")
        f.writelines("\t".join(r) + "\n" for r in jrows)
    krows = kips()
    with open(os.path.join(OUT, "candidates_kip.tsv"), "w") as f:
        f.write("kip\trelease\n")
        f.writelines("\t".join(r) + "\n" for r in krows)
    print(f"jira: {n} bug-like keys -> {len(jrows)} candidates; kip: {len(krows)} released 3.x-4.3; requests so far {asf.requests_made()}")


if __name__ == "__main__":
    main()
