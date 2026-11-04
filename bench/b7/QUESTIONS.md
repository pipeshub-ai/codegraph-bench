# B7 question set

`questions.jsonl` holds 90 questions about Apache Kafka. Each is asked twice: in phase A only the code is indexed, and in phase B the code plus Jira, the KIPs and the 4.3 docs site.

| type  | count | what it tests |
|-------|------:|---------------|
| kip   | 25 | which KIP introduced a config, class or metric that exists at the pinned commit, and why (motivation, rejected alternative) |
| jira  | 25 | Fixed bugs created since 2022-01-01 with a commit at the pinned commit: given the symptom, name the issue and fixVersion; or, given the issue, explain the bug |
| docs  | 20 | operational and upgrade prose from https://kafka.apache.org/43/ (upgrade, KRaft vs ZooKeeper, ELR, rebalance and transaction protocols, Java versions, SSL, ACLs, securing a running cluster) |
| multi | 10 | a KIP's or Jira issue's motivation, or a 4.3 upgrade note, combined with the default or constant in the code |
| code  | 10 | controls answerable from code alone (defaults, implementing classes, enum values); phase B should not score lower on these |

Snapshot: apache/kafka `9fbcd3f76cecd04015e5d893976d9849f407af30` (trunk, `4.5.0-SNAPSHOT`), docs `/43/`, Jira project KAFKA, Confluence space KAFKA. Sources were fetched on 2026-10-21.

## Fields

`id`, `type`, `question`, `answer`, `key_facts` (1–4 short strings), `sources` (list of `{kind, id_or_url, quote}`), `answerable_from_code_only` (`true`, `false` or `"partial"`).

The source kinds are `kip`, `jira`, `docs` and `code`. A `code` source links to the file on GitHub at the pinned commit.

## How it was built

1. `select_candidates.py` builds the candidate pools in `reports/b7/candidates_*.tsv`.
   - **Jira:** takes commits since 2022 whose subject starts with `KAFKA-N` and reads like a bug fix. It runs one Jira search per 100 keys, filtered to Bug, Fixed and created ≥ 2022-01-01. It keeps issues with one commit touching 1–2 main source files that still exist, one fixVersion, and a description of at least 200 characters. That leaves 130 candidates.
   - **KIP:** takes rows of the KIP index released in 3.x or 4.0–4.3, 276 in all.
2. I read the candidate KIP pages, Jira issues and docs pages and wrote the questions by hand in `questions_spec.py`.
   - **KIPs:** each one's config, class or metric was checked with `git grep` at the pinned commit. KIP-1196's proposed name is not in the code, and the multi question about it asks for exactly that difference.
   - **Jira:** 25 issues were picked from the pool.
   - **Code facts:** each was read from the clone.
3. `build_questions.py` re-fetches every cited source and writes `questions.jsonl`. It fails, and writes nothing, if:
   - a quote is missing from its source;
   - a key fact is missing from every source the question cites;
   - a cited Jira issue is not Fixed and created since 2022.

   It also writes `reports/b7/question_checks.tsv`, which shows how many key facts appear anywhere in the repo, in code files and in `docs/*.md`.

```
B7_CACHE=/path/to/cache python3 -I bench/b7/select_candidates.py --kafka <clone at the sha>
B7_CACHE=/path/to/cache python3 -I bench/b7/build_questions.py  --kafka <clone at the sha>
```

`asf.py` sends requests to the ASF servers one at a time, at least 1 s apart. It caches every response and logs every request in `$B7_CACHE/requests.log`. It refuses to go past 1,500 requests (`B7_BUDGET`). Building this set took 90 ASF requests:

- 1 for the docs index;
- 9 Jira searches;
- 1 for the KIP index;
- 35 KIP pages (2 missed on their title);
- 18 docs pages;
- 26 single Jira issues.

A rebuild with a warm cache makes none. The default cache is `bench/b7/.b7cache/`, which is gitignored.

## How the `answerable_from_code_only` labels were set

"Code only" means the repo at the pinned commit as PipesHub's code connector indexes it. `SUPPORTED_CODE_FILE_EXTENSIONS` includes `md` and `html`, so the code-only arm also sees `docs/*.md`. The labels mean:

- **true:** every key fact, and the reasoning the question asks for, is in the repo.
- **partial:** the config, class or default is in the repo, but the KIP or issue number, the motivation or the release is not.
- **false:** the repo has essentially nothing that answers the question.

`question_checks.tsv` gives the mechanical evidence behind each label.

| type  | true | partial | false |
|-------|-----:|--------:|------:|
| kip   | 4  | 19 | 2  |
| jira  | 0  | 4  | 21 |
| docs  | 17 | 3  | 0  |
| multi | 0  | 10 | 0  |
| code  | 10 | 0  | 0  |

## Caveats

- **The docs questions barely separate the phases.** The 4.3 site is built from the repo's `docs/` markdown, and trunk carries the same prose, including the 4.3.1 notes. If phase A indexes `.md` files, the docs questions mostly test retrieval, not the extra source. To make them separate the phases, exclude `docs/` from the phase-A code index, or score docs questions separately.
- **Some KIP numbers are in the repo.** `docs/getting-started/upgrade.md` links KIP-884, 923, 1033, 1074, 1106, 1188 and 1230 to their configs. Phase A can therefore name those KIPs, though not their rejected alternatives. Two Jira keys are named in main code comments: KAFKA-14831 in `TransactionManager` and KAFKA-13743 in `ReplicationControlManager`. KAFKA-15836 is named in a test. Those questions are labelled `partial`.
- **Key facts are verbatim, not normalised.** Some appear as code expressions, such as `2 * 60 * 1000L`, `1024 * 1024 * 1024` and `300000`. Some are short and match too easily as substrings, such as `5`, `2`, `50`, `false`, `-1` and `ISR`. A grader should normalise numbers and units, and should not rely on substring matching alone for these.
- **The snapshot versions differ.** Trunk is 4.5.0-SNAPSHOT and the docs are 4.3. The questions avoid facts that differ between the two, except multi-10, which tests the KIP-1196 rename on purpose.
- **Fix versions and KIP pages can change after the snapshot.** Each Jira question uses an issue with exactly one fixVersion, so "first release with the fix" is that version. A later backport could add versions. KIP pages are wikis and can be edited. Re-running `build_questions.py` with an empty cache detects any drift.
- **Quotes are checked loosely.** Each quote is compared with the source's text with all whitespace ignored. Jira wiki markup (`{{ }}`, `{code}`, `[text|url]`) is stripped first, and the page title or issue key is prepended. The published quotes have the spacing artifacts from HTML-to-text conversion removed.

## v2: questions a real user would ask

`questions_v2.jsonl` (built from `questions_spec_v2.py`) re-asks all 90 questions. v1 mostly asked "Which KIP introduced…" or "name the Jira issue", and often put the KIP number, Jira key or title words in the question. Real users never ask "which KIP". The lookup key was in the question, so retrieval was trivial. 18 questions named an id outright, and 51 of 90 required one as a key fact.

v2 keeps the same sources, types and counts. Each question has a `from_id` that points to the v1 question it replaces. Ids look like `b7v2-kip-01`.

Rules:

1. Each question describes a real situation or need: "how do I…", "why does…", "we upgraded and now see X — known bug, cause, fixed in which release?", "should we change Y, what's the trade-off?". Jira questions describe the reporter's symptom, not the issue title.
2. A question never contains a KIP number, a Jira key, a page title, or a 4+ word run from the cited KIP title or Jira summary. It may name a config, class or component the user would already know, but not the term the question asks for.
3. Key facts are what the user needs in order to act: config, metric or class names, defaults, the fixing or introducing release, the root cause, the workaround. There are no KIP or Jira ids among them. Each question has 1–4 facts, and each fact is verified in a cited source.
4. `code` questions are controls that can be answered from code alone. `multi` questions need a design, issue or doc rationale plus a fact from the code. `docs` questions are about 4.3 docs-site content.
5. `answerable_from_code_only` is re-labelled for the new wording. The code-only arm does **not** index `docs/`, so a fact that appears only in `docs/*.md` counts as missing.

`build_questions.py` takes `--spec`, `--out` and `--checks` (an alias of `--report`). Without them it builds v1 exactly as before. Any spec other than the default is also linted, and the build fails, writing nothing, if:

- a question contains `KIP-`, `KAFKA-` or `KIP <digit>` (case-sensitive, so `kafka-configs.sh` is allowed);
- a question shares a 4-word run with a cited KIP title or Jira summary (words are lower-cased alphanumerics);
- a key fact is an id.

The v1 spec fails this lint on 67 questions. v2 passes it.

```
B7_CACHE=/path/to/cache python3 -I bench/b7/build_questions.py --kafka <clone at the sha, with docs/> \
  --spec questions_spec_v2 --out bench/b7/questions_v2.jsonl --checks reports/b7/question_checks_v2.tsv
```

With a cold cache the build made 69 ASF requests, and v1 and v2 share them. Every quote and key fact was verified. Per-fact repo hits are in `reports/b7/question_checks_v2.tsv`.

| type  | count | true | partial | false |
|-------|------:|-----:|--------:|------:|
| kip   | 25 | 21 | 4  | 0  |
| jira  | 25 | 0  | 4  | 21 |
| docs  | 20 | 7  | 7  | 6  |
| multi | 10 | 0  | 10 | 0  |
| code  | 10 | 10 | 0  | 0  |

Why the labels moved from v1:

- **kip:** most are now `true`. Without the KIP number, the config, metric or class the user needs is usually defined, with its doc string, in the code. The four `partial` ones ask for a motivation or a behaviour that only the KIP explains (kip-02, 03, 22, 25).
- **docs:** v1 marked 17 as `true`. That assumed the code-only arm sees `docs/`, and it no longer does. Release notes, the 5.0 timeline, the SSL migration steps and "not encrypted" are now `false`.
- **jira:** still mostly `false`. The fix release is not in the code.

Caveats specific to v2:

- Some defaults are still 1–2 character facts: `-1` in kip-02, and `50`, `2` and `5` in multi-02, multi-07, multi-09, code-06 and code-09. `grade.py` leaves those to the judge.
- jira-13 has a single key fact, the fix version.
