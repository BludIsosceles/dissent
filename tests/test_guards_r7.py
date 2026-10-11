"""Pins for guards that a branch-by-branch mutation run (Delta A-3 round 7) found untested.

Each case names the guard and the failure it prevents. Run: python3 tests/test_guards_r7.py
"""
import io, pathlib, ssl, subprocess, sys, tempfile, urllib.error, urllib.request

root = pathlib.Path(__file__).resolve().parent.parent
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

# -- the CLI: a document with no recognisable citation is a usage refusal, never a clean pass
with tempfile.TemporaryDirectory() as t:
    doc = pathlib.Path(t) / "doc.md"; doc.write_text("No citations here at all.\n")
    r = subprocess.run([sys.executable, str(root / "dissent.py"), str(doc)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    case("no citations found exits 2, not a clean 0", r.returncode == 2 and "no citations found" in r.stderr,
         (r.returncode, r.stderr[-160:]))

# -- network: a broken certificate is the citation's problem ("gone"), not a transient one
e = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
case("SSL certificate failure is gone, not flaky", D._network_kind(e) == "gone", D._network_kind(e))

# -- PDF encryption: anything not provably Standard-RC4 is unsupported, never decrypted as RC4
P = D.pdf_encryption
case("encrypt reference with no object is unsupported",
     P(b"%PDF-1.4\ntrailer << /Encrypt 5 0 R >>\n") == "unsupported", P(b"trailer << /Encrypt 5 0 R >>"))
case("non-Standard security handler is unsupported",
     P(b"%PDF-1.4\n5 0 obj << /Filter /Adobe.PubSec /V 2 /R 3 >> endobj\ntrailer << /Encrypt 5 0 R >>\n") == "unsupported")
case("V 5 (AES-256) without a CFM marker is unsupported",
     P(b"%PDF-1.4\n5 0 obj << /Filter /Standard /V 5 /R 6 >> endobj\ntrailer << /Encrypt 5 0 R >>\n") == "unsupported")
case("control: Standard V 2 is rc4",
     P(b"%PDF-1.4\n5 0 obj << /Filter /Standard /V 2 /R 3 >> endobj\ntrailer << /Encrypt 5 0 R >>\n") == "rc4")

# -- readability: a page that cannot be read is never read (and so never accused)
R = D.readability
words = "word " * 200                                   # 1000 chars of text: above 400, below 1500
HEAD = '<html><head><title>A page</title><meta name="description" content="d"></head><body>'
mostly_script = HEAD + "<script>" + "x" * 6000 + "</script><p>" + words + "</p></body></html>"
case("mostly-script page is unreadable (script share alone)",
     "mostly script" in (R(mostly_script, words) or ""), R(mostly_script, words))
thin = HEAD + "<div class=a></div>" * 12000 + "<p>" + words + "</p></body></html>"   # no script, text < 1% of raw
case("text under 1% of a large page is unreadable (ratio alone)",
     "mostly script" in (R(thin, words) or ""), (len(words) / len(thin), R(thin, words)))
pdf_raw = "%PDF-1.7\n" + "stream " * 600
case("raw PDF bytes served as text are unreadable, not read",
     R(pdf_raw, "stream " * 600) is not None and "PDF" in (R(pdf_raw, "stream " * 600) or ""),
     R(pdf_raw, "stream " * 600))

# -- local judge: only a 400 (unknown field) is retried; any other HTTP error surfaces at once
calls = []
def fake_urlopen(req, timeout=None):
    calls.append(1)
    raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b""))
real = urllib.request.urlopen
urllib.request.urlopen = fake_urlopen
try:
    try:
        D.judge_local("a claim", "a quote", "https://example.test/x", "m", "http://127.0.0.1:9")
        raised = None
    except urllib.error.HTTPError as ex:
        raised = ex.code
finally:
    urllib.request.urlopen = real
case("a 500 from the judge is raised after one request, not retried", raised == 500 and len(calls) == 1,
     (raised, len(calls)))

print(f"---\ntests_run: {run}\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
