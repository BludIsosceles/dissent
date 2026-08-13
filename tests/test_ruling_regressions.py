#!/usr/bin/env python3
"""Spot-check repros for the adversarial ruling on dissent v0.4.0.

Run from the dissent repo root (or pass the directory containing dissent.py):

    python3 dissent_ruling_repros.py [path-to-dissent-dir]

Every check asserts a DEFECT as it exists at the reviewed artifact:

    dissent.py  sha256 1ebf641fe413d5250e776419204c2286a358ebe87e48c2995ba3e65c19091d0f
    commit      64cf81cabd9ea40c5f19d3a4c0789654633b3156  (v0.4.0)

Semantics, per build-verify convention:
  - "REPRODUCED" means the defect behaves as the ruling claims. All ten
    reproduce at the reviewed sha; spot_checks_failed: 0.
  - AFTER repairs, checks 1-4 and 6-10 are EXPECTED to stop reproducing and
    check 5 (control) must keep passing. Each check states its post-fix
    expected value inline, so this file inverts into the regression suite
    with minimal edits — findings become tests, per house convention.

No network. No model calls. Stdlib only. Deterministic.
"""
import hashlib
import pathlib
import sys
import tempfile

REVIEWED_SHA = "1ebf641fe413d5250e776419204c2286a358ebe87e48c2995ba3e65c19091d0f"

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
src = root / "dissent.py"
if not src.exists():
    print(f"dissent.py not found under {root} — pass the repo dir as arg 1",
          file=sys.stderr)
    sys.exit(2)
actual = hashlib.sha256(src.read_bytes()).hexdigest()
if actual != REVIEWED_SHA:
    print(f"NOTE: dissent.py sha256 {actual[:12]}… differs from the reviewed "
          f"artifact ({REVIEWED_SHA[:12]}…). If repairs have landed, checks "
          "1-4 and 6-10 SHOULD now fail to reproduce — that is the goal.\n")

sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True))
        print(f"  REPRODUCED      {name}\n                  {detail}")
    except AssertionError as e:
        RESULTS.append((name, False))
        print(f"  NOT-REPRODUCED  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False))
        print(f"  ERROR           {name}: {type(e).__name__}: {e}")


# ---- D1: parse_vote integrity (dissent.py lines 332-338) --------------------

def d1_unsupported_token_inverts():
    """The vocabulary's own negative form parses as a POSITIVE vote.
    Post-fix expectation: DOES_NOT_SUPPORT (or UNCLEAR by strict enum)."""
    v = D.parse_vote("UNSUPPORTED")
    assert v == "SUPPORTS", f"expected the inversion, got {v}"
    return "parse_vote('UNSUPPORTED') -> SUPPORTS"


def d1_caveat_reasoning_flips_verdict():
    """A SUPPORTS verdict with a natural caveat sentence flips negative,
    because the whole transcript is scanned rather than the first line the
    prompt contract (JUDGE_PROMPT, line 300) mandates.
    Post-fix expectation: SUPPORTS."""
    v = D.parse_vote(
        "SUPPORTS\nThe quote supports the vendor claim, though it does not "
        "support the specific price figure.")
    assert v == "DOES_NOT_SUPPORT", f"expected the flip, got {v}"
    return "verdict line SUPPORTS + caveat reasoning -> DOES_NOT_SUPPORT"


def d1_abstention_counted_as_vote():
    """An explicit abstention parses as a SUPPORTS vote via substring.
    Post-fix expectation: UNCLEAR."""
    v = D.parse_vote("UNCLEAR - cannot determine if this supports the claim")
    assert v == "SUPPORTS", f"expected SUPPORTS-by-substring, got {v}"
    return "abstention text -> SUPPORTS"


def d1_preamble_noise_flips_verdict():
    """kimi-style reasoning preamble (documented in quorum's roster notes)
    contains a natural 'does not support' and flips a SUPPORTS verdict.
    Compounds D3 (no capture hygiene). Post-fix expectation: SUPPORTS."""
    v = D.parse_vote(
        "• Checking: the terms text does not support pricing claims in "
        "general, but here it is specific.\n"
        "SUPPORTS\nThe quote directly names the price.")
    assert v == "DOES_NOT_SUPPORT", f"expected the flip, got {v}"
    return "noisy preamble + SUPPORTS verdict -> DOES_NOT_SUPPORT"


def d1_control_clean_first_line_parses():
    """CONTROL: benign phrasing parses correctly — the defect is phrasing-
    dependent, which is exactly why it survives benchmarks. Must keep
    passing after repairs."""
    v = D.parse_vote("SUPPORTS\nThe quote directly names the price.")
    assert v == "SUPPORTS", f"control broke: {v}"
    return "clean transcript -> SUPPORTS (correct)"


# ---- D2: degraded quorum masking (lines 100-107, 455, 536) ------------------

def _verbatim_cite(support):
    c = D.Citation(tag="official_vendor", url="https://x", quote="q",
                   claim="c", line=1)
    c.resolved = True
    c.verbatim = True
    c.support = support
    return c


def d2_single_vote_reads_as_consensus():
    """One judge errored; the survivor's lone vote renders as the same
    SUPPORTED label genuine cross-family agreement produces.
    Post-fix expectation: a degraded verdict (e.g. SUPPORTED-DEGRADED or
    L3-DEGRADED) distinguishable from full-quorum consensus."""
    c = _verbatim_cite({"kimi": "SUPPORTS", "devin": "ERROR (TimeoutExpired)"})
    assert c.verdict == "SUPPORTED", c.verdict
    return "1 vote + 1 judge ERROR -> SUPPORTED"


def d2_unclear_abstention_invisible():
    """UNCLEAR is deliberately not a vote (correct intent) — but the verdict
    layer then presents the remaining single vote as consensus.
    Post-fix expectation: degraded/annotated verdict."""
    c = _verbatim_cite({"kimi": "SUPPORTS", "devin": "UNCLEAR"})
    assert c.verdict == "SUPPORTED", c.verdict
    return "1 vote + 1 UNCLEAR -> SUPPORTED"


def d2_total_judge_failure_silently_passes():
    """Every configured judge failed; the citation reverts to the L2 label,
    which is NOT in the flagged-verdict set (report line 455 / json 536), so
    an entirely failed L3 run reports clean and exits 0. 'Silence is a
    result, not a pass' — quorum's own header. Post-fix expectation: a
    JUDGES-FAILED verdict counted as flagged, nonzero exit."""
    c = _verbatim_cite({"kimi": "ERROR (X)", "devin": "ERROR (Y)"})
    assert c.verdict == "VERBATIM", c.verdict
    flagged = ("UNRESOLVED", "NOT_VERBATIM", "UNSUPPORTED", "DISPUTED")
    assert c.verdict not in flagged
    return "ALL judges ERROR -> VERBATIM, unflagged, exit 0"


# ---- D4: paren URLs are invisible (lines 50-56, 343-356) --------------------

def d4_paren_url_citation_invisible():
    """')' is excluded from the URL atom, so the Wikipedia-disambiguation
    class of URL fails the ENTIRE citation match: not flagged, not verified,
    not counted. Post-fix expectation: 2 citations parsed."""
    doc = ('Mercury is closest to the Sun [third_party_analysis] '
           'https://en.wikipedia.org/wiki/Mercury_(planet) — '
           '"Mercury is the first planet from the Sun"\n'
           'Ok line [official_vendor] https://example.com — "Example Domain"\n')
    assert D.CITATION_RE.search(doc.splitlines()[0]) is None, \
        "paren-URL line unexpectedly matched"
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
        tf.write(doc)
        path = tf.name
    cites = D.parse(path)
    assert len(cites) == 1 and cites[0].url == "https://example.com", \
        [c.url for c in cites]
    return "paren-URL citation: unparsed and silently skipped (1 of 2 found)"


# ---- D6a: Iterable exhaustion (lines 395-437) -------------------------------

def d6_generator_input_drops_tag_check():
    """independence_report iterates its Iterable twice; a generator is
    exhausted by the first pass and the unknown-tag check sees nothing.
    Latent (main() passes a list). Post-fix expectation: identical output
    for list and generator inputs."""
    bad = lambda: D.Citation("made_up_tag", "https://e.com", "q", "c", 1)  # noqa: E731
    from_gen = D.independence_report(x for x in [bad()])
    from_list = D.independence_report([bad()])
    assert from_gen == [] and from_list, (from_gen, from_list)
    return "generator input -> [], list input -> unknown-tag flagged"


print(f"dissent adversarial-ruling spot-checks — target sha {actual[:12]}…\n")
check("D1  UNSUPPORTED token inverts to SUPPORTS", d1_unsupported_token_inverts)
check("D1  caveat reasoning flips a SUPPORTS verdict", d1_caveat_reasoning_flips_verdict)
check("D1  abstention counted as a SUPPORTS vote", d1_abstention_counted_as_vote)
check("D1  judge-CLI preamble noise flips the verdict", d1_preamble_noise_flips_verdict)
check("D1  CONTROL: clean transcript parses correctly", d1_control_clean_first_line_parses)
check("D2  single vote renders as consensus", d2_single_vote_reads_as_consensus)
check("D2  UNCLEAR abstention invisible in verdict", d2_unclear_abstention_invisible)
check("D2  total judge failure -> clean report, exit 0", d2_total_judge_failure_silently_passes)
check("D4  paren-URL citation silently invisible", d4_paren_url_citation_invisible)
check("D6a generator input drops unknown-tag check", d6_generator_input_drops_tag_check)

performed = len(RESULTS)
failed = sum(1 for _, ok in RESULTS if not ok)
print(f"""
---
spot_checks_performed: {performed}
spot_checks_failed: {failed}
---""")
sys.exit(1 if failed else 0)
