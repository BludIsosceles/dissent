#!/usr/bin/env python3
"""Build the held-out L3 items from WiCE (claim level, TEST split). Run with python3 -I.
Gold: supported -> SUPPORTS, partially_supported -> PARTIAL, not_supported -> DOES_NOT_SUPPORT.
Quote: supported/partial = the first non-empty annotated supporting-sentence set;
not_supported (no annotated support) = the 3 evidence sentences with the highest word overlap
with the claim (metadata lines excluded), in source order."""
import ast, json, re, sys
src, dst = sys.argv[1], sys.argv[2]
GOLD = {"supported": "SUPPORTS", "partially_supported": "PARTIAL", "not_supported": "DOES_NOT_SUPPORT"}
W = lambda s: set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "an", "of", "and", "in", "to", "was", "is", "for", "on", "by", "with", "as", "at"}
out = []
for line in open(src):
    r = json.loads(line)
    ev = r["evidence"] if isinstance(r["evidence"], list) else ast.literal_eval(r["evidence"])
    ss = r["supporting_sentences"] if isinstance(r["supporting_sentences"], list) else ast.literal_eval(r["supporting_sentences"])
    meta = r["meta"] if isinstance(r["meta"], dict) else ast.literal_eval(r["meta"])
    if r["label"] != "not_supported":
        idx = next(s for s in ss if s)
        how = "annotated"
    else:
        cw = W(r["claim"])
        cand = [(len(cw & W(t)), -i, i) for i, t in enumerate(ev) if not t.startswith("(meta data)")]
        idx = sorted(i for _, _, i in sorted(cand, reverse=True)[:3])
        how = "overlap-top3"
    out.append({"id": meta["id"], "claim": r["claim"], "quote": " ".join(ev[i].strip() for i in idx),
                "gold": GOLD[r["label"]], "quote_rule": how})
with open(dst, "w") as f:
    for o in out:
        f.write(json.dumps(o, ensure_ascii=False) + "\n")
print(len(out), "items")
