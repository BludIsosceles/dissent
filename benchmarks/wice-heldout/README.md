# Held-out L3 benchmark: WiCE test split

Everything needed to check dissent v0.9.0's held-out L3 result (see the main README,
"Held-out L3 measurement"):

- `PREREG.md`: the design, committed before any judge ran.
- `prep.py`: builds `items.jsonl` from WiCE's claim-level test split.
- `items.jsonl`: 358 claim/quote/gold items.
- `transcripts.jsonl`: every judge response (716), with the parsed vote.
- `score.py`, `RESULTS.md`: the arithmetic. Run `python3 benchmarks/wice-heldout/score.py`
  from the repo root to reproduce the results.

**Data licence.** The items derive from **WiCE** (Ryo Kamoi, Tanya Goyal, Juan Diego Rodriguez,
Greg Durrett, "WiCE: Real-World Entailment for Claims in Wikipedia", EMNLP 2023;
github.com/ryokamoi/wice @ ddeb6c18). WiCE is built from Wikipedia (CC BY-SA) and
Common Crawl archives. `items.jsonl` is redistributed under **CC BY-SA 4.0** with that
attribution, and the Common Crawl terms of use apply. The MIT licence of dissent's code does
not cover these items.
