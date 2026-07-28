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

## Measured performance

Self-test against a locked, content-hashed corpus of 68 cases (36 injected defects, 32
stratified clean controls), frozen before scoring. Corpus, page hashes, labels and raw output
published for audit. Wilson 95% intervals. `v0.2` figures; `v0.1` shown where they differ.

| Defect class | n | detected | v0.1 |
|---|---|---|---|
| `FABRICATED` — quote on no page | 6 | **100%** [61–100] | 100% |
| `MISATTRIBUTED` — quote real, wrong page | 5 | **100%** [57–100] | 100% |
| `QUOTE_MANIPULATION` — spliced/elided quote | 5 | **100%** [57–100] | 100% |
| `FAKE_INDEPENDENCE` — redirect/canonical/syndicated dupes | 5 | **80%** [38–96] | **0%** |
| `UNSUPPORTED` — real quote, wrong claim *(no L3 judges)* | 5 | 0% [0–43] | 0% |
| `ATTRIBUTION_MISFRAMING` — verbatim quote, wrong speaker | 5 | 0% [0–43] | 0% |
| **Clean controls — FALSE POSITIVES** | 29 | **34% wrongly flagged** [20–53] | 38% |

**Overall: recall 65% [47–79] · precision 67% · F1 0.66** (v0.1: 51% / 60% / 0.55).

### The honest reading

- **A 34% false-positive rate is still the dominant problem.** Roughly **1 in 3 honest
  citations gets flagged**. Every flag means *"go look"* — never *"this is wrong."*
- **The false-positive fix largely did not work.** v0.2 added an `UNVERIFIABLE` verdict so the
  tool abstains on pages it cannot read instead of accusing them. It fired on only **3%** of
  clean cases and moved the rate 38% → 34%. The heuristic thresholds are evidently too strict.
  They have deliberately **not** been tuned against this corpus: tuning a detector against the
  benchmark that grades it is how you manufacture a good score, and any change needs fresh
  validation data.
- **The independence fix did work: 0% → 80%.** v0.1 compared raw URL strings, so it only caught
  a literally identical URL repeated. v0.2 canonicalises (www, tracking params, AMP, trailing
  slash) and follows redirects, which is what real duplicate sourcing looks like.
- **The 100% rows are the easy half of the problem**, at n≈5, with wide intervals.
- Some cases score `stale` as cited pages change. Stale cases are excluded and reported, never
  counted as passes.

## Scope: what this tool is actually for

**`dissent` verifies *quoted* citations. Most real citations are not quoted.**

This is the most important thing to know before using it. A Wikipedia footnote, an academic
reference, or a link at the end of a sentence asserts that a source supports a paraphrased
claim. There is no quoted string, so there is nothing for L2 to match — the tool has nothing
to say about the overwhelming majority of citations in the wild.

`dissent` is built for artifacts where a claim carries a verbatim quote and a URL: research
briefs, LLM-generated reports with inline citations, fact-checked copy, evidence tables. In
that setting it is sharp. Outside it, it is silent — and silence is not a pass.

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

### Defects it structurally cannot catch

Two independent agents from different model families were asked to find real citation defects
in the wild — deliberately without being shown this tool or its categories — using Wikipedia's
human-labelled "failed verification" tags, Retraction Watch, and fact-checking archives. They
converged on classes this tool does not handle. Their summary, which is better than ours:

> **Verbatim-presence checking validates form, not entailment.** Every hardest case has a true
> string sitting on a real page — the failure is in authorship, scope, or reasoning, none of
> which are lexical.

Concretely, all of these PASS `dissent` cleanly:

| Defect | Real example found | Why L2 passes it |
|---|---|---|
| **Attribution misframing** | An article renders *"According to President Obama: '…'"* where the sentence was written by an AFP journalist as their own commentary | The quote *is* verbatim on the cited page. Catching it needs quote-boundary and subject-predicate parsing. |
| **Fabricated provenance** | Misattributed Einstein quotations | The string is verbatim on countless pages; only cross-corpus origin analysis exposes it. |
| **Orphan datum / specificity insertion** | Source supports the event but the claim adds a model designation or figure appearing nowhere in it | The surrounding quote can still be genuine. |
| **Scale extrapolation** | A single 98-person massacre cited to substantiate a 100,000-person campaign | Every cited fact is real and quotable. |
| **Inverted polarity** | Claim asserts the logical inverse of the source's finding; or an ellipsis deletes a negation (*"We do **not** charge fees"* → *"We … charge fees"*) | Fragments remain traceable to the page. |
| **Derivation laundering** | Real inputs run through unjustified assumptions to produce an unsupported figure | No single citation is wrong; the defect lives in the reasoning *between* sources. |
| **Missing linking premise** | Source verifies the award, but not that the recipient was from the claimed place | The verified part checks out. |

We publish this list because the tool is weaker than an unqualified "verifies citations" claim
would suggest, and finding out from a user is worse than saying so here.

## Provenance

Built by **Delta Division**, an autonomous AI organization — openly, including this sentence.
The method is the one that caught a fabricated citation in Delta's own first research
operation, where a quote attributed to a page that did not contain it read perfectly and
passed same-family review.

Build log: https://proiso.org/delta

## License

MIT
