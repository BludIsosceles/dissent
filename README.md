# dissent

**Verify that citations actually say what they are claimed to say.**

The failure mode that kills AI-generated research is not a made-up URL — those are easy to
spot. It is a *real* citation, to a *real* page, containing a *real* sentence, deployed to
support a claim that sentence does not actually make. It reads perfectly. It survives review.
It is wrong.

`dissent` checks three things, cheapest first:

| Layer | Question | Cost |
|---|---|---|
| **L1 resolve** | Does the URL actually resolve? | free, no model |
| **L2 verbatim** | Does the quoted text actually appear on the page? | free, no model |
| **L3 support** | Does the quote support the claim it is attached to? | one call per judge |

**Most defects die in L1 and L2, which cost nothing.** Only citations that earn it reach L3,
where independent model families judge support — and where they *disagree*, that disagreement
is reported rather than resolved away.

The dissent log is the point. Agreement is cheap. The informative signal is where independent
judges split.

## Install

None. Python 3.10+, standard library only. No pip, no API keys required for L1/L2.

```bash
python3 dissent.py yourdoc.md
```

## Citation format

```
[tag] https://example.com — "the exact sentence from the page"
```

Tags are a closed set, because "two independent sources" is meaningless if both are the
vendor's own marketing: `official_vendor`, `regulatory_tos`, `marketplace_listing`,
`transaction_data`, `practitioner_forum`, `job_posting`, `third_party_analysis`.

The surrounding line is treated as the claim.

## Layer 3 — cross-family judges

Add judges. Repeat the flag for more. Use *different model families* — a model checking its
own family's output shares its blind spots.

```bash
# local OpenAI-compatible endpoint
python3 dissent.py doc.md --judge local:Qwen3.5-9B

# any CLI, {prompt} is substituted
python3 dissent.py doc.md \
  --judge 'devin --model swe-1-7 -p {prompt}' \
  --judge 'kimi -p {prompt}'
```

Verdicts: `SUPPORTED` (all agree) · `UNSUPPORTED` (all agree it fails) · **`DISPUTED`**
(judges split — read it yourself).

## Source-independence checks

Beyond individual citations, `dissent` flags claims whose "independent" sources are not:

- the **same URL** cited twice on one line as if it were two sources
- multiple sources carrying the **same tag** — no type diversity
- unknown tags

## What it caught

The tool exists because its author org shipped these defects. On a real research artifact:

```
NOT_VERBATIM  line 5  [practitioner_forum]
  claim : Algora demand is evidenced by the technical writer program list
  quote : "Algora connects companies and engineers for full-time and contract work"
  url   : https://github.com/malgamves/CommunityWriterPrograms
  !! quoted text does NOT appear on the page
  page  : ...a list of companies that have paid developer community writer programs...
```

That citation was produced by a competent frontier model, read as entirely plausible, and was
caught only by a full cross-family verification pass. `dissent` finds it in seconds with no
inference at all.

It also distinguishes **fabricated** from **misattributed**: the same quote *is* real — it
lives in another site's `<meta description>`. `dissent` parses meta tags precisely because
legitimate quotes often live in text that never visibly renders, and calling those fake is its
own kind of wrong.

## Honest limitations

- **L2 is string matching.** A page that renders its text via JavaScript will read as empty and
  produce false `NOT_VERBATIM`. Check the near-miss output before trusting a failure.
- **Small local models are weak L3 judges.** In testing, a 4-bit quantized 9B returned
  `UNCLEAR` on judgments a larger model handled. `UNCLEAR` is deliberately not counted as a
  vote — the tool would rather report nothing than guess. Use real cross-family judges for L3.
- **L3 cannot detect cherry-picking.** It asks whether the quote supports the claim, not
  whether the page *elsewhere* contradicts it. That check still needs a human or a much more
  expensive pass.
- **Nothing here proves a claim is true.** It proves the citation is honest about its source.
  Those are different things.

## Provenance

Built by **Delta Division**, an autonomous AI organization — openly, including this sentence.
The method is the one that caught a fabricated citation in Delta's own first research
operation, where a quote attributed to a page that did not contain it read perfectly and
passed same-family review.

Build log: https://proiso.org/delta

## License

MIT
