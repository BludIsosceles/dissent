#!/usr/bin/env python3
"""Tests for dissent's citation-metadata check (op-008, W2).

    python3 dissent/tests/test_metadata.py [path-to-dissent-dir]

Stdlib only. No network: the one end-to-end check serves pages from an
in-process http.server on 127.0.0.1.
"""
import contextlib
import http.server
import io
import json
import pathlib
import sys
import tempfile
import threading
import time

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent.parent).resolve()
sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True))
        print(f"  PASS  {name}")
    except AssertionError as e:
        RESULTS.append((name, False))
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False))
        print(f"  ERROR {name}: {type(e).__name__}: {e}")


def parse_text(text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tf:
        tf.write(text)
    return D.parse(tf.name)


def codes(issues):
    return sorted(i.split(":", 1)[0] for i in issues)


# ------------------------------------------------------------------ parsing

V07_DOC = (
    'Mercury is closest [third_party_analysis] https://en.wikipedia.org/wiki/Mercury_(planet) — '
    '"Mercury is the first planet from the Sun"\n'
    'Ok line [official_vendor] https://example.com — "Example Domain"\n'
    'Two cites [official_vendor] https://a.example/x -- "alpha" and [job_posting] https://b.example/y – "beta" tail\n'
    'Brace prose [official_vendor] https://example.com — "Example Domain" {not metadata} rest\n'
    'Unknown key [official_vendor] https://example.com — "Example Domain" {colour="red"} rest\n'
)
# What v0.7 (commit 82d11d0) parsed from V07_DOC, recorded by running it there.
V07_EXPECTED = [
    ("third_party_analysis", "https://en.wikipedia.org/wiki/Mercury_(planet)",
     "Mercury is the first planet from the Sun", "Mercury is closest", 1),
    ("official_vendor", "https://example.com", "Example Domain", "Ok line", 2),
    ("official_vendor", "https://a.example/x", "alpha", "Two cites  and  tail", 3),
    ("job_posting", "https://b.example/y", "beta", "Two cites  and  tail", 3),
    ("official_vendor", "https://example.com", "Example Domain", "Brace prose  {not metadata} rest", 4),
    ("official_vendor", "https://example.com", "Example Domain", 'Unknown key  {colour="red"} rest', 5),
]


def t_v07_lines_parse_unchanged():
    got = [(c.tag, c.url, c.quote, c.claim, c.line) for c in parse_text(V07_DOC)]
    assert got == V07_EXPECTED, got
    assert all(c.declared_meta == {} for c in parse_text(V07_DOC))


def t_suffix_parsed():
    cs = parse_text('Claim text [third_party_analysis] https://ex.com/a — "the quote" '
                    '{title="A Title, with comma", date="2025-07", publisher="Ex Co", author="Jane Doe"} after\n')
    assert len(cs) == 1, cs
    c = cs[0]
    assert c.quote == "the quote", c.quote
    assert c.url == "https://ex.com/a", c.url
    assert c.declared_meta == {"title": "A Title, with comma", "date": "2025-07",
                               "publisher": "Ex Co", "author": "Jane Doe"}, c.declared_meta
    assert c.claim == "Claim text  after", repr(c.claim)


def t_suffix_escapes_and_case():
    cs = parse_text('x [official_vendor] https://ex.com — "q"{TITLE="The \\"Big\\" One" ,date="2024"}\n')
    assert cs[0].declared_meta == {"title": 'The "Big" One', "date": "2024"}, cs[0].declared_meta


def t_suffix_empty_value_dropped():
    cs = parse_text('x [official_vendor] https://ex.com — "q" {title=""}\n')
    assert cs[0].declared_meta == {}, cs[0].declared_meta


def t_regex_linear_on_adversarial_input():
    # Unterminated suffixes and long runs that a backtracking pattern would chew on.
    evil = ('[official_vendor] https://ex.com — "q" {' + 'title="' + 'a\\' * 20000
            + ' ' + ', date="x"' * 5000)
    t = time.perf_counter()
    list(D.CITATION_RE.finditer(evil))
    list(D.CITATION_RE.finditer("[a] https://" + "(" * 30000 + " — " + '"q"'))
    assert time.perf_counter() - t < 2.0, time.perf_counter() - t


# ------------------------------------------------------------------ extraction

SAMPLE = """<!doctype html><html><head>
<title>Škoda's voice assistant Laura now enhanced with ChatGPT | Škoda Storyboard</title>
<meta content="Laura gets ChatGPT" property="og:title">
<meta property="og:site_name" content="Škoda Storyboard">
<meta name="author" content="Kamila Biddle">
<meta property="article:published_time" content="2024-06-26T09:00:00+02:00">
<meta property="article:modified_time" content="2025-01-02T09:00:00+02:00">
<meta name="twitter:title" content='Twitter &amp; Title'>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"NewsArticle","headline":"Voice assistant Laura gets ChatGPT","datePublished":"2024-06-26",
  "author":{"@type":"Person","name":"K. Biddle"},"publisher":{"@type":"Organization","name":"Škoda Auto"}},
 {"@type":"WebSite","name":"Storyboard"},
 {"@type":"BreadcrumbList","name":"crumbs"}]}</script>
</head><body><h1>Škoda's <em>voice</em> assistant Laura</h1><time datetime="2024-06-26">26 June</time></body></html>"""


def t_extract_html():
    m = D.page_metadata(SAMPLE, "https://www.skoda-storyboard.com/en/press-releases/x/")
    titles = dict((s, v) for s, v in m["titles"])
    assert titles["title"].startswith("Škoda's voice assistant Laura"), titles
    assert titles["og:title"] == "Laura gets ChatGPT", titles
    assert titles["twitter:title"] == "Twitter & Title", titles
    assert titles["jsonld:headline"] == "Voice assistant Laura gets ChatGPT", titles
    assert titles["h1"] == "Škoda's voice assistant Laura", titles
    assert "crumbs" not in titles.values()     # BreadcrumbList name is not a title
    dates = [s for s, _ in m["dates"]]
    assert {"article:published_time", "jsonld:datePublished", "time"} <= set(dates), dates
    assert ["article:modified_time", "2025-01-02T09:00:00+02:00"] in m["modified"], m["modified"]
    assert ["og:site_name", "Škoda Storyboard"] in m["site_names"], m["site_names"]
    assert ["jsonld:publisher", "Škoda Auto"] in m["site_names"], m["site_names"]
    assert ["jsonld:website", "Storyboard"] in m["site_names"], m["site_names"]
    assert ["author", "Kamila Biddle"] in m["authors"] and ["jsonld:author", "K. Biddle"] in m["authors"]
    assert m["host"] == "skoda-storyboard.com" and not m["challenge"], m


def t_extract_url_dates_and_archive_host():
    m = D.page_metadata("<html><title>x y</title></html>",
                        "https://web.archive.org/web/20250702041512id_/https://www.verbund.com/en/press/2025/7/16/qurrent")
    assert m["host"] == "verbund.com", m["host"]
    assert ["url-path", "2025-07"] in m["dates"], m["dates"]
    m = D.page_metadata("", "https://archive.is/20250729165352/https://www.forbes.com/sites/p/2025/07/29/x/")
    assert m["host"] == "forbes.com" and ["url-path", "2025-07"] in m["dates"], m
    m = D.page_metadata("", "https://www.munichre.com/newsroom/2025/Sun-Life.html")
    assert ["url-path", "2025"] in m["dates"], m["dates"]


def t_extract_pdf():
    pdf = "%PDF-1.7\n1 0 obj << /Title (Salesforce YouGov Report \\(ANZ\\)) /CreationDate (D:20250211) >> endobj"
    m = D.page_metadata(pdf, "https://x.example/f.pdf")
    assert m["kind"] == "pdf", m
    assert ["pdf:Title", "Salesforce YouGov Report (ANZ)"] in m["titles"], m["titles"]
    assert ["pdf:CreationDate", "D:20250211"] in m["dates"], m["dates"]


def t_extract_challenge_page_discarded():
    m = D.page_metadata("<html><head><title>Just a moment...</title>"
                        "<meta property='og:site_name' content='Cloudflare'></head></html>",
                        "https://example.org/2024/05/story")
    assert m["challenge"] and m["titles"] == [] and m["site_names"] == [], m
    assert ["url-path", "2024-05"] in m["dates"], m["dates"]


# ------------------------------------------------------------------ decisions

def meta(titles=(), dates=(), sites=(), authors=(), host="example.com", kind="html", challenge=False):
    return {"kind": kind, "titles": [list(t) for t in titles], "dates": [list(d) for d in dates],
            "modified": [], "site_names": [list(s) for s in sites], "authors": [list(a) for a in authors],
            "host": host, "challenge": challenge}


def t_title_match_tolerances():
    page = meta(titles=[("title", "ING Bank: Transforming Operations Through Agentic AI | Computer Weekly")])
    for declared in ("ING Bank: Transforming Operations Through Agentic AI",       # site suffix
                     "ing bank transforming operations through agentic ai",        # case, punctuation
                     "ING Bank — transforming operations through agentic AI!",
                     "ING Bank"):                                                  # subtitle truncated
        assert D.check_metadata({"title": declared}, page) == [], declared
    page = meta(titles=[("og:title", "Four ways to shop: REWE takes hybrid and cashless shopping to a new level")])
    assert D.check_metadata({"title": "Four Ways to Shop"}, page) == []
    assert D.check_metadata({"title": "REWE takes hybrid & cashless shopping to a new level"}, page) == []


def t_title_mismatch_reports_page_title():
    page = meta(titles=[("title", "Generative AI in e-commerce: Otto Group introduces new innovation projects")])
    got = D.check_metadata({"title": "Generative AI and E-commerce Evolution"}, page)
    assert codes(got) == ["TITLE_MISMATCH"], got
    assert "Otto Group introduces" in got[0], got


def t_title_unchecked_not_accused():
    for page in (meta(titles=[]),
                 meta(titles=[("title", "Home")]),
                 meta(titles=[("title", "Intellihub")], sites=[("og:site_name", "Intellihub")]),
                 meta(titles=[("pdf:Title", "Microsoft Word - final_v3.docx")], kind="pdf"),
                 meta(challenge=True)):
        got = D.check_metadata({"title": "Something Entirely Different Here"}, page)
        assert codes(got) == ["TITLE_UNCHECKED"], (page, got)
    # A PDF title may confirm a match.
    assert D.check_metadata({"title": "Annual Report 2024"},
                            meta(titles=[("pdf:Title", "Annual Report 2024")], kind="pdf")) == []


def t_date_decisions():
    page = meta(dates=[("article:published_time", "2024-11-06T10:00:00Z"), ("url-path", "2024-11")])
    assert D.check_metadata({"date": "November 2024"}, page) == []
    got = D.check_metadata({"date": "2025"}, page)
    assert codes(got) == ["DATE_MISMATCH"] and "2024" in got[0], got
    assert codes(D.check_metadata({"date": "2025"}, meta())) == ["DATE_UNCHECKED"]
    assert codes(D.check_metadata({"date": "recently"}, page)) == ["DATE_UNCHECKED"]
    # Any stated publication year is enough; modified dates are not publication dates.
    page = meta(dates=[("time", "2023-01-01"), ("jsonld:datePublished", "2025-02-01")])
    assert D.check_metadata({"date": "2025-02"}, page) == []


def t_publisher_decisions():
    assert D.check_metadata({"publisher": "Unite.ai"}, meta(host="unite.ai")) == []
    assert D.check_metadata({"publisher": "Otto Group Newsroom"}, meta(host="ottogroup.com")) == []
    assert D.check_metadata({"publisher": "SNCF Group"}, meta(host="groupe-sncf.com")) == []
    assert D.check_metadata({"publisher": "NHS MFT"}, meta(host="mft.nhs.uk")) == []
    assert D.check_metadata({"publisher": "Toyota USA Newsroom"}, meta(host="pressroom.toyota.com")) == []
    assert D.check_metadata({"publisher": "Fast Company Middle East"},
                            meta(host="fastcompanyme.com", sites=[("og:site_name", "Fast Company Middle East")])) == []
    got = D.check_metadata({"publisher": "MaaS Alliance"}, meta(host="jreast.co.jp"))
    assert codes(got) == ["PUBLISHER_MISMATCH"] and "jreast.co.jp" in got[0], got
    got = D.check_metadata({"publisher": "Forbes"}, meta(host="unite.ai", sites=[("og:site_name", "Unite.AI")]))
    assert codes(got) == ["PUBLISHER_MISMATCH"] and "Unite.AI" in got[0], got
    assert codes(D.check_metadata({"publisher": "X Corp"}, meta(host=None))) == ["PUBLISHER_UNCHECKED"]


def t_author_decisions():
    page = meta(host="unite.ai", authors=[("author", "Antoine Tardif")], sites=[("og:site_name", "Unite.AI")])
    got = D.check_metadata({"author": "Mercedes-Benz"}, page)
    assert codes(got) == ["AUTHOR_MISMATCH"] and "Antoine Tardif" in got[0], got
    assert D.check_metadata({"author": "Antoine Tardif"}, page) == []
    assert D.check_metadata({"author": "BBVA"}, meta(host="bbva.com", authors=[("author", "J. Smith")])) == []
    assert D.check_metadata({"author": "İşbank"}, meta(host="isbank.com.tr")) == []
    # No author on the page: cannot contradict.
    assert codes(D.check_metadata({"author": "Jane Doe"}, meta(host="nytimes.com"))) == ["AUTHOR_UNCHECKED"]


# ------------------------------------------------------------------ end to end

PAGE = (b"<html><head><title>Real Title of the Page | Example News</title>"
        b"<meta property='article:published_time' content='2024-03-01'></head>"
        b"<body><p>The quoted sentence lives here.</p></body></html>")


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


def t_end_to_end_hook_report_json_exit():
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}/news"
    try:
        doc = (f'plain [official_vendor] {base} — "The quoted sentence lives here."\n'
               f'good [official_vendor] {base} — "The quoted sentence lives here." '
               f'{{title="Real Title of the Page", date="March 2024"}}\n'
               f'bad [official_vendor] {base} — "The quoted sentence lives here." '
               f'{{title="An Invented Headline About AI", date="2025"}}\n')
        cs = [D.check_citation(c) for c in parse_text(doc)]
    finally:
        srv.shutdown()
    plain, good, bad = cs
    assert plain.verdict == good.verdict == bad.verdict == "VERBATIM", [c.verdict for c in cs]
    assert plain.page_meta is None and plain.meta_issues == [] and plain.meta_unchecked == []
    assert good.meta_issues == [], good.meta_issues
    assert codes(bad.meta_issues) == ["DATE_MISMATCH", "TITLE_MISMATCH"], bad.meta_issues

    # JSON: a no-metadata citation serialises with the v0.7 keys plus the two
    # fetch fields v0.8 added (blocked, fetch_note), and none of the metadata keys.
    v07_keys = {"tag", "url", "quote", "claim", "line", "resolved", "http_status", "verbatim",
                "unreadable", "near_miss", "support", "transcripts", "error", "verdict",
                "blocked", "fetch_note"}
    assert set(D.citation_json(plain)) == v07_keys, set(D.citation_json(plain)) ^ v07_keys
    j = D.citation_json(bad)
    assert j["meta_issues"] and j["page_meta"]["titles"][0][0] == "title", j
    json.dumps(j)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc_clean = D.report([plain, good], [], use_color=False)
    text = out.getvalue()
    assert rc_clean == 0 and "flagged 0" in text and "metadata issues 0" in text, text
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc_bad = D.report([plain, good, bad], [], use_color=False)
    text = out.getvalue()
    assert rc_bad == 1 and "flagged 1" in text and "META_MISMATCH" in text and "!! TITLE_MISMATCH" in text, text
    # No metadata anywhere: the summary line is v0.7's, unchanged.
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        D.report([plain], [], use_color=False)
    assert "metadata issues" not in out.getvalue() and "meta  :" not in out.getvalue()


print("dissent metadata tests\n")
for name, fn in list(globals().items()):
    if name.startswith("t_") and callable(fn):
        check(name[2:], fn)
failed = sum(1 for _, ok in RESULTS if not ok)
print(f"\n---\ntests_run: {len(RESULTS)}\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
