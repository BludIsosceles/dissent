# A-5 — dissent L3 on a held-out set: pre-registration

Committed before any judge is run. Any change after results are seen gets a new
pre-registration on a fresh split (WiCE dev), never a re-run on this one.

## Why
dissent's README: *"the L3 prompt's run 2 was tuned after reading run 1 on this same
benchmark. A held-out set is owed before any L3 number here should be treated as predictive."*
This is that set.

## Data (held out: never used to build or tune dissent)
- **WiCE** (Kamoi et al., 2023): Wikipedia claims paired with their cited sources, with human
  labels. Repo `ryokamoi/wice` @ `ddeb6c18`; **claim-level TEST split**, all **358** items, no
  sampling (sha256 of the source file starts `4c91b9e9590cfcd8`).
- Gold: supported → SUPPORTS (111); partially_supported → PARTIAL (215); not_supported →
  DOES_NOT_SUPPORT (32).
- **Quote rule (`prep.py`):**
  - supported and partial: the first non-empty annotated supporting-sentence set;
  - not_supported, which has no annotated support: the 3 source sentences with the highest
    word overlap with the claim, in source order, excluding metadata lines.

  The rule depends on the label, so median quote length differs by class (269 / 205 / 394
  chars) and is reported. `items.jsonl` sha256 starts `57d46fd783c898ed`.
- SOURCE shown to judges: "cited source for the Wikipedia article: <title>". WiCE gives no URL.

## Instrument
- dissent **v0.9.0** (`9d58b50`), L3 exactly as shipped: `JUDGE_PROMPT`, `fence()`,
  `judge_cli()`, `parse_vote()`, `Citation.verdict`. No prompt change.
- Judges (two families, both outside the author's family), through Lumen, run via
  `tools/xfam-read.py`-style identity checks:
  - **gpt-5.6-luna** (openai) via OpenRouter;
  - **glm-5.3** (glm) via Z.AI.
  `--max-tokens 32768`.
- A transport failure is retried once. Anything still failing is recorded as ERROR and counted
  (dissent's verdict logic makes such a citation DEGRADED or JUDGES-FAILED). Nothing is
  dropped.

## Metrics (all reported, with Wilson 95% intervals)
1. **Per judge:** 3×3 confusion against gold, accuracy, macro-F1; UNCLEAR and ERROR counted.
2. **Per judge, binary "flag"** (vote ≠ SUPPORTS) against gold ≠ SUPPORTS: precision, recall,
   specificity.
3. **False accusation:** a DOES_NOT_SUPPORT vote on a gold-SUPPORTS item (per judge).
4. **Miss:** a SUPPORTS vote on a gold-DOES_NOT_SUPPORT item (per judge).
5. **Combined verdict** (both judges, dissent v0.9 logic): distribution per gold class;
   DISPUTED rate; UNSUPPORTED (hard flag) on gold-SUPPORTS; SUPPORTED on gold-DOES_NOT_SUPPORT.
6. **Judge agreement:** Cohen's kappa over the three votes.
7. **The withdrawn claim, tested:** on gold-SUPPORTS and gold-PARTIAL items, how often each
   judge hard-flags (DOES_NOT_SUPPORT) rather than PARTIAL. This is the over-flagging the
   withdrawn v0.8 claim was about.

## Caveats stated now
- WiCE's labels judge the claim against the **whole source**. dissent's L3 judges it against the
  **quote**. For supported and partial items the quote is the annotated support, so the two
  coincide. For not_supported items, a quote from a non-supporting source cannot support.
- WiCE's PARTIAL is any unsupported part; dissent's PARTIAL is a *peripheral* unsupported part
  (its prompt says DOES_NOT_SUPPORT when the central assertion is unsupported). Some gold-PARTIAL
  items are therefore correctly answered DOES_NOT_SUPPORT by dissent's own definition. The
  3-class accuracy understates dissent for this reason. The binary flag metrics (2-4) do not
  depend on it, so **metrics 2-4 are primary.**
- Only 32 not_supported items: the miss-rate interval will be wide, and is reported as such.

## What will be published
Whatever comes out goes into dissent's README "Measured performance" as the held-out result,
beside the in-sample KPMG numbers, with these caveats. Nothing is filtered by direction.
