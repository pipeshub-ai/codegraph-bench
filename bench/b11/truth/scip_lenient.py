"""`scip print --json`, reduced to what scip_truth.py reads, for an index with invalid UTF-8 strings
(scip-typescript writes lone surrogates from string-literal types, which `scip print` rejects).

  python3 scip_lenient.py in.scip out.json
"""
import json
import sys


def varint(b, i):
    n = s = 0
    while True:
        x = b[i]
        i += 1
        n |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return n, i


def fields(b):
    i = 0
    while i < len(b):
        key, i = varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(b, i)
        elif wt == 2:
            n, i = varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {wt}")
        yield f, wt, v


def packed(v):
    out, i = [], 0
    while i < len(v):
        n, i = varint(v, i)
        out.append(n - (1 << 32) if n >= 1 << 31 else n)
    return out


def occurrence(b):
    o = {"range": []}
    for f, wt, v in fields(b):
        if f == 1:
            o["range"] += packed(v) if wt == 2 else [v]
        elif f == 2:
            o["symbol"] = v.decode("utf-8", "replace")
        elif f == 3:
            o["symbol_roles"] = v
    return o


def document(b):
    d = {"occurrences": []}
    for f, _, v in fields(b):
        if f == 1:
            d["relative_path"] = v.decode("utf-8", "replace")
        elif f == 2:
            d["occurrences"].append(occurrence(v))
    return d


if __name__ == "__main__":
    data = open(sys.argv[1], "rb").read()
    docs = [document(v) for f, _, v in fields(data) if f == 2]
    json.dump({"documents": docs}, open(sys.argv[2], "w"))
    print(len(docs), "documents")
