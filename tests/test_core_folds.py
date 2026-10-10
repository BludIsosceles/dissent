#!/usr/bin/env python3
"""L1/L2 core: every defence against a false accusation, pinned.

A mutation audit of Delta's own dissent (2026-10-10) found 13 of 20 such defences
untested: each normalisation fold, meta-tag capture, the inline-tag rule, and three
readability gates. The comments beside them record the honest citation each one was
added to stop accusing, often found by an outside reviewer. None had a test, so any
of them could be deleted with every suite green. Each case below fails when its
defence is removed.

No network. Pure functions plus check_citation() over a stubbed fetch.
"""
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

run = failed = 0
def case(name, ok, detail=""):
    global run, failed
    run += 1
    if ok:
        print(f"  PASS  {name}")
    else:
        failed += 1; print(f"  FAIL  {name}: {detail}")

N = D.normalize
# -- normalisation: typographic differences are not dishonesty
case("single and curly quotes fold together", N("the 'meritocracy' claim") == N("the “meritocracy” claim") == N("the ‘meritocracy’ claim"),
     N("the 'meritocracy' claim"))
case("en and em dashes fold to hyphen", N("2020–2024 — growth") == N("2020-2024 - growth"), N("2020–2024 — growth"))
case("non-breaking space folds to space", N("100 million users") == N("100 million users"), repr(N("100 million users")))
case("line wrapping collapses", N("made out\n   of components") == N("made out of components"), N("made out\n   of components"))
case("space before punctuation collapses", N("components .") == N("components.") and N("( x )") == N("(x )".replace(" )", ")")),
     N("components ."))
case("case folds", N("The Quick Brown Fox") == N("the quick brown fox"))
case("HTML entities unescape", N("Ben &amp; Jerry&#39;s") == N("Ben & Jerry's"), N("Ben &amp; Jerry&#39;s"))
case("NFKC folds compatibility forms", N("ﬁnance ①") == N("finance 1"), N("ﬁnance ①"))
case("different WORDS stay different", N("prices rose") != N("prices fell"))

# -- markup: what a reader sees, including meta text that never renders
page = ('<html><head><meta content="A quote that lives only in meta" name="description"></head>'
        '<body><p>made out of <em>components</em>.</p><script>var notText = "script words";</script></body></html>')
text = N(D.strip_markup(page))
case("meta description captured, attributes in either order", "a quote that lives only in meta" in text, text[:120])
case("inline tags removed without a separator", "made out of components." in text, text)
case("script text is not page text", "script words" not in text, text)
mid = N(D.strip_markup("<p>built from <b>sub</b>components and <a href=x>re</a>use</p>"))
case("a tag inside a word does not split it", "built from subcomponents and reuse" in mid, mid)

# -- readability gates: an unreadable page abstains, never accuses
filler = "x" * 5000
js_shell = f"<html><head><title>T</title><meta name=description content=d></head><body>{'<div></div>' * 600}<p>short</p></body></html>"
case("JS floor: big HTML, almost no text -> unreadable", D.readability(js_shell, N(D.strip_markup(js_shell))) is not None,
     D.readability(js_shell, N(D.strip_markup(js_shell))))
# floor only: >4000 chars of HTML, <400 chars of text, no script, and text/HTML above 1%,
# so the script-ratio gate cannot be the one that fires
floor_only = ('<html><head><title>T</title><meta name=description content=d></head><body>'
              + '<div class="' + "c" * 4400 + '"></div><p>' + "few words here " * 6 + '</p></body></html>')
_ft = N(D.strip_markup(floor_only))
case("JS floor alone catches a big page with almost no text", len(_ft) < 400 and len(_ft) / len(floor_only) >= 0.01
     and D.readability(floor_only, _ft) is not None, (len(_ft), D.readability(floor_only, _ft)))
spa = ('<html><head><title>App</title><meta name=description content=d></head><body><div id="root"></div>'
       '<p>' + "loading the application shell " * 20 + '</p></body></html>')
case("app shell with little server text -> unreadable", D.readability(spa, N(D.strip_markup(spa))) is not None,
     D.readability(spa, N(D.strip_markup(spa))))
stub = "<html><body>" + "<p>" + "access refused for automated clients. " * 30 + "</p></body></html>"
case("stub without <title>/<meta> -> unreadable (bot block)", D.readability(stub, N(D.strip_markup(stub))) is not None,
     D.readability(stub, N(D.strip_markup(stub))))
real = ("<html><head><title>Article</title><meta name=description content=d></head><body><article>"
        + "<p>An ordinary readable paragraph of article text that a person would read on the page.</p>" * 30
        + "</article></body></html>")
case("ordinary readable page is NOT abstained on", D.readability(real, N(D.strip_markup(real))) is None,
     D.readability(real, N(D.strip_markup(real))))

# -- end to end: a stubbed fetch, so the gates decide the verdict
def verdict_for(html_page, quote):
    saved = D.fetch_page
    D.fetch_page = lambda url, timeout=30, retries=2: D.Fetched(200, html_page.encode(), "text/html", "utf-8")
    try:
        c = D.Citation("third_party_analysis", "https://example.test/p", quote, "claim", 1)
        return D.check_citation(c).verdict
    finally:
        D.fetch_page = saved
case("SPA shell with absent quote -> UNVERIFIABLE, not NOT_VERBATIM",
     verdict_for(spa, "a sentence rendered only by javascript") == "UNVERIFIABLE", verdict_for(spa, "a sentence rendered only by javascript"))
case("stub page with absent quote -> UNVERIFIABLE, not NOT_VERBATIM",
     verdict_for(stub, "the real article sentence") == "UNVERIFIABLE", verdict_for(stub, "the real article sentence"))
case("meta-only quote -> VERBATIM", verdict_for(page, "A quote that lives only in meta") == "VERBATIM",
     verdict_for(page, "A quote that lives only in meta"))
case("quote with inline markup on the page -> VERBATIM", verdict_for(page, "made out of components.") == "VERBATIM",
     verdict_for(page, "made out of components."))
case("readable page, absent quote -> NOT_VERBATIM (still accuses)",
     verdict_for(real, "a sentence this article never contains") == "NOT_VERBATIM", verdict_for(real, "a sentence this article never contains"))

print(f"---\ntests_run: {run}\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
