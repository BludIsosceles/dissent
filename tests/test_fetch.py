#!/usr/bin/env python3
"""L1/L2 regression tests for the KPMG-audit fetch defects (op-008 W1).

Run from the repo root (or pass the directory containing dissent.py):

    python3 dissent/tests/test_fetch.py dissent

Every case below is a way v0.7 falsely accused an honest citation, or a
guard that keeps the fix from swinging into never accusing at all:

  * bot blocks (403/429) and a UA-dependent 404 reported as UNRESOLVED
  * gzip/deflate bodies compared as compressed bytes -> NOT_VERBATIM
  * "captcha" inside a <script> -> abstained on a readable page
  * a JS-rendered page slipping the 400-char readability floor
  * PDFs never read

No network: a local http.server on 127.0.0.1 serves every page, and the one
"connection refused" case uses a port nothing listens on. Stdlib only.
"""
import gzip
import hashlib
import http.server
import pathlib
import socket
import sys
import threading
import zlib

# Default to the repo root that holds dissent.py, derived from THIS file --
# not a cwd-relative "dissent", which only resolved when the suite happened to
# be run from the parent of the repo. A clone running `python3 tests/x.py`
# got ModuleNotFoundError.
root = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

D.time.sleep = lambda s: None     # retry back-off is irrelevant against localhost

QUOTE = "The quick brown fox verifies every citation it is handed"
ARTICLE = ("<html><head><title>Fox news</title><meta name='description' content='A page'></head><body>"
           "<nav>Home | About | Contact</nav><article><h1>The fox</h1>"
           + "<p>Filler paragraph about foxes and hounds, long enough to be a real article. </p>" * 30
           + f"<p>{QUOTE}.</p></article></body></html>")


# ------------------------------------------------------------------ PDF builder

def pdf_escape(s: bytes) -> bytes:
    return s.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(content: bytes, extra_objs: dict[int, bytes] | None = None,
              font_obj: bytes | None = None, encrypt: bytes | None = None,
              crypt=None) -> bytes:
    """A small but structurally real PDF: catalog, page tree, one page, a
    Flate-compressed content stream, a font, and an xref table."""
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << /F1 5 0 R /F2 7 0 R >> >> >>",
        # Resources deliberately inherited from the Pages node.
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        5: font_obj or b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    objs.update(extra_objs or {})
    streams = {4: zlib.compress(content)}
    for k, v in list(objs.items()):
        if isinstance(v, tuple):          # (dict, raw stream) pairs from extra_objs
            streams[k] = v[1]
            objs[k] = v[0]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(set(objs) | set(streams)):
        offsets[num] = len(out)
        if num in streams:
            data = streams[num]
            if crypt:
                data = crypt(num, data)
            head = objs.get(num, b"<< /Filter /FlateDecode >>")
            head = head[:-2].rstrip() + b" /Length %d >>" % len(data)
            out += b"%d 0 obj\n%s\nstream\n%s\nendstream\nendobj\n" % (num, head, data)
        else:
            out += b"%d 0 obj\n%s\nendobj\n" % (num, objs[num])
    xref = len(out)
    n = max(offsets) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % n
    for i in range(1, n):
        out += b"%010d 00000 n \n" % offsets.get(i, 0)
    trailer = b"<< /Size %d /Root 1 0 R" % n
    if encrypt:
        trailer += encrypt
    out += trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


# Simple font: literal strings with escapes, a TJ array with kerning and a
# word-gap, an octal escape, and a hex string.
SIMPLE_CONTENT = (
    b"BT /F1 12 Tf 72 700 Td (The quick brown fox) Tj ( verifies every) ' "
    b"[(cita) 30 (tion) -250 (it)] TJ ( is handed \\(sic\\) and caf\\351 ) Tj "
    b"<726570656174656420696e20686578> Tj ET"
)
# "\351" is é in WinAnsi; the hex string reads "repeated in hex".

# Type0 font with a 2-byte ToUnicode CMap mapping glyph ids to "Delta GmbH".
CID_TEXT = "Delta GmbH"
CMAP = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        b"1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        b"2 beginbfchar <0003> <0020> <0010> <0044> endbfchar\n"
        b"1 beginbfrange <0020> <0027> [<0065> <006C> <0074> <0061> <0047> <006D> <0062> <0048>] endbfrange\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end")
CID_GLYPHS = [0x10, 0x20, 0x21, 0x22, 0x23, 0x03, 0x24, 0x25, 0x26, 0x27]   # D e l t a _ G m b H
CID_CONTENT = b"BT /F2 12 Tf 72 650 Td <" + b"".join(b"%04X" % g for g in CID_GLYPHS) + b"> Tj ET"
CID_OBJS = {
    7: b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Sub /Encoding /Identity-H "
       b"/DescendantFonts [8 0 R] /ToUnicode 9 0 R >>",
    8: b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ABCDEF+Sub >>",
    9: (b"<< /Filter /FlateDecode >>", zlib.compress(CMAP)),
}


def plain_pdf() -> bytes:
    return build_pdf(SIMPLE_CONTENT + b"\n" + CID_CONTENT, CID_OBJS)


# Independent RC4 + key derivation for the encrypted fixture (not dissent's).
PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def rc4(key, data):
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for b in data:
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out.append(b ^ s[(s[i] + s[j]) % 256])
    return bytes(out)


def encrypted_pdf() -> bytes:
    O = bytes(range(32))
    P = -1324
    file_id = bytes.fromhex("FD0EFBA80FB8BB41BE00332EBFB3DAE5")
    key = hashlib.md5(PAD + O + (P & 0xFFFFFFFF).to_bytes(4, "little") + file_id).digest()[:16]
    for _ in range(50):
        key = hashlib.md5(key).digest()[:16]

    def crypt(num, data):
        k = hashlib.md5(key + num.to_bytes(3, "little") + b"\0\0").digest()[:16]
        return rc4(k, data)
    enc_obj = (b"<< /Filter /Standard /V 2 /R 3 /Length 128 /P %d /O <%s> /U <%s> >>"
               % (P, O.hex().encode(), bytes(32).hex().encode()))
    return build_pdf(SIMPLE_CONTENT, {20: enc_obj}, encrypt=b" /Encrypt 20 0 R /ID [<%s> <%s>]"
                     % (file_id.hex().encode(), file_id.hex().encode()), crypt=crypt)


# ------------------------------------------------------------------ server

def page(body: str) -> bytes:
    return body.encode("utf-8")


JS_SHELL = ("<html><head><title>Careers</title><meta name='description' content='Join us'></head><body>"
            "<div id='main'><header>Doctolib careers</header>"
            + "<p>We are hiring engineers who care about healthcare and good software.</p>" * 6
            + "</div>"
            + "".join(f"<script>window.__chunk{i}=" + "'x'" * 1500 + ";</script>" for i in range(10))
            + "</body></html>")
TEXT_LIGHT = ("<html><head><title>Notice</title><meta name='description' content='Office closed'></head>"
              "<body><nav>" + "<a href='/x'>Link</a> " * 60 + "</nav>"
              "<p>The office will be closed on Monday for the public holiday. "
              "Normal service resumes Tuesday morning at nine.</p>"
              "<footer>" + "<a href='/y'>Footer link</a> " * 40 + "</footer></body></html>")
CAPTCHA_IN_SCRIPT = (ARTICLE.replace(
    "<head>", "<head><script>var recaptchaKey='abc'; function loadCaptcha(){/* captcha for the contact form */}</script>"))
CHALLENGE = ("<html><head><title>Just a moment...</title><meta name='robots' content='noindex'></head>"
             "<body><h1>Verify you are human</h1><p>Checking your browser before accessing the site.</p>"
             "<script src='/cdn-cgi/challenge-platform/h/b/orchestrate/jsch/v1'></script></body></html>")
LATIN1 = ("<html><head><meta charset='iso-8859-1'><title>Caf\xe9</title></head><body>"
          + "<p>Le caf\xe9 est ouvert tous les jours de la semaine, sauf le lundi.</p>" * 5
          + "</body></html>").encode("latin-1")

ROUTES = {}


def route(path, status=200, body=b"", headers=None, browser=None):
    ROUTES[path] = (status, body, headers or {}, browser)


route("/article", body=page(ARTICLE))
route("/forbidden", 403, b"<html>Forbidden</html>")
route("/ratelimited", 429, b"slow down")
route("/unavailable", 503, b"maintenance")
route("/gone", 404, b"not found")
route("/ua404", 404, b"not found", browser=(200, page(ARTICLE), {}))
route("/ua403-then-404", 403, b"no", browser=(404, b"not found", {}))
route("/gzip", body=gzip.compress(page(ARTICLE)), headers={"Content-Encoding": "gzip"})
route("/gzip-noheader", body=gzip.compress(page(ARTICLE)))
route("/deflate", body=zlib.compress(page(ARTICLE)), headers={"Content-Encoding": "deflate"})
route("/deflate-raw", body=zlib.compress(page(ARTICLE))[2:-4], headers={"Content-Encoding": "deflate"})
route("/brotli", body=b"\x1b\x00\xf8\xff", headers={"Content-Encoding": "br"})
route("/captcha-script", body=page(CAPTCHA_IN_SCRIPT))
route("/challenge", body=page(CHALLENGE))
route("/challenge-ua", body=page(CHALLENGE), browser=(200, page(ARTICLE), {}))
route("/jsshell", body=page(JS_SHELL))
route("/textlight", body=page(TEXT_LIGHT))
route("/latin1", body=LATIN1, headers={"Content-Type": "text/html"})
route("/empty", body=b"")
route("/doc.pdf", body=plain_pdf(), headers={"Content-Type": "application/pdf"})
route("/octet", body=plain_pdf(), headers={"Content-Type": "application/octet-stream"})
route("/enc.pdf", body=encrypted_pdf(), headers={"Content-Type": "application/pdf"})
AES_PDF = (b"%PDF-1.6\n1 0 obj << /Filter /Standard /V 4 /R 4 /CF << /StdCF << /CFM /AESV2 >> >> >> "
           b"endobj\ntrailer << /Encrypt 1 0 R >>\n%%EOF")
route("/aes.pdf", body=AES_PDF, headers={"Content-Type": "application/pdf"})
route("/scanned.pdf", body=build_pdf(b"q 612 0 0 792 0 0 cm /Im0 Do Q"), headers={"Content-Type": "application/pdf"})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        status, body, headers, browser = ROUTES.get(self.path, (404, b"no route", {}, None))
        ua = self.headers.get("User-Agent", "")
        if browser and "dissent" not in ua:
            status, body, headers = browser
        ctype = headers.get("Content-Type", "text/html; charset=utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in headers.items():
            if k != "Content-Type":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

# A port with nothing listening, for connection refused.
_s = socket.socket()
_s.bind(("127.0.0.1", 0))
DEAD_PORT = _s.getsockname()[1]
_s.close()


# ------------------------------------------------------------------ harness

RESULTS = []


def cite(path, quote=QUOTE, base=None):
    c = D.Citation(tag="third_party_analysis", url=(base or BASE) + path, quote=quote, claim="c", line=1)
    return D.check_citation(c)


def check(name, fn):
    try:
        fn()
        RESULTS.append(True)
        print(f"  PASS  {name}")
    except AssertionError as e:
        RESULTS.append(False)
        print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append(False)
        print(f"  ERROR {name}: {type(e).__name__}: {e}")


def expect(c, verdict, *, reason=None):
    assert c.verdict == verdict, f"verdict {c.verdict} != {verdict} (unreadable={c.unreadable!r}, error={c.error!r})"
    if reason:
        assert c.unreadable and reason in c.unreadable, f"reason {c.unreadable!r} lacks {reason!r}"


# a. refusals abstain; real absence still accuses
check("control: plain article, quote present -> VERBATIM", lambda: expect(cite("/article"), "VERBATIM"))
check("control: plain article, quote absent -> NOT_VERBATIM",
      lambda: expect(cite("/article", "a sentence the page never says"), "NOT_VERBATIM"))
check("403 -> UNVERIFIABLE with refusal reason",
      lambda: expect(cite("/forbidden"), "UNVERIFIABLE", reason="server refused automated access (HTTP 403)"))
check("403 sets blocked, leaves resolved undetermined",
      lambda: (lambda c: (c.blocked is not None and c.resolved is None) or (_ for _ in ()).throw(
          AssertionError(f"blocked={c.blocked!r} resolved={c.resolved!r}")))(cite("/forbidden")))
check("429 -> UNVERIFIABLE", lambda: expect(cite("/ratelimited"), "UNVERIFIABLE", reason="HTTP 429"))
check("503 -> UNVERIFIABLE", lambda: expect(cite("/unavailable"), "UNVERIFIABLE", reason="HTTP 503"))
check("404 to both UAs -> UNRESOLVED", lambda: expect(cite("/gone"), "UNRESOLVED"))
check("404 to dissent UA, 200 to browser UA -> read the page (VERBATIM)",
      lambda: expect(cite("/ua404"), "VERBATIM"))
check("UA-dependent 404 is disclosed in fetch_note",
      lambda: (lambda c: ("browser" in (c.fetch_note or "")) or (_ for _ in ()).throw(
          AssertionError(repr(c.fetch_note))))(cite("/ua404")))
check("403 to dissent UA, 404 to browser -> UNRESOLVED (browser's answer wins)",
      lambda: expect(cite("/ua403-then-404"), "UNRESOLVED"))
check("connection refused -> UNRESOLVED",
      lambda: expect(cite("/x", base=f"http://127.0.0.1:{DEAD_PORT}"), "UNRESOLVED"))
check("200 with empty body -> UNVERIFIABLE, not UNRESOLVED", lambda: expect(cite("/empty"), "UNVERIFIABLE"))

# b. compressed bodies
check("gzip with Content-Encoding -> VERBATIM", lambda: expect(cite("/gzip"), "VERBATIM"))
check("gzip WITHOUT Content-Encoding (Wayback id_) -> VERBATIM", lambda: expect(cite("/gzip-noheader"), "VERBATIM"))
check("deflate (zlib-wrapped) -> VERBATIM", lambda: expect(cite("/deflate"), "VERBATIM"))
check("deflate (raw) -> VERBATIM", lambda: expect(cite("/deflate-raw"), "VERBATIM"))
check("gzip, quote absent -> still NOT_VERBATIM",
      lambda: expect(cite("/gzip", "a sentence the page never says"), "NOT_VERBATIM"))
check("brotli body we cannot decode -> UNVERIFIABLE (unreadable, not blocked)",
      lambda: (lambda c: (c.verdict == "UNVERIFIABLE" and c.blocked is None and "brotli" in c.unreadable)
               or (_ for _ in ()).throw(AssertionError(f"{c.verdict} {c.blocked!r} {c.unreadable!r}")))(cite("/brotli")))


def _accept_encoding():
    seen = {}

    class H(Handler):
        def do_GET(self):
            seen["ae"] = self.headers.get("Accept-Encoding", "")
            super().do_GET()
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    D.fetch(f"http://127.0.0.1:{s.server_address[1]}/article", timeout=5, retries=0)
    s.shutdown()
    assert "gzip" in seen["ae"] and "br" not in seen["ae"], f"Accept-Encoding {seen['ae']!r}"


check("Accept-Encoding offers gzip/deflate, never br", _accept_encoding)
check("fetch() keeps the v0.7 (status, text, err) shape and decodes gzip",
      lambda: (lambda r: (r[0] == 200 and QUOTE in r[1] and r[2] is None) or (_ for _ in ()).throw(
          AssertionError(repr(r)[:200])))(D.fetch(BASE + "/gzip", timeout=5, retries=0)))
check("fetch() reports a refusal as an error, not a page",
      lambda: (lambda r: (r[0] == 403 and r[1] == "" and r[2]) or (_ for _ in ()).throw(
          AssertionError(repr(r))))(D.fetch(BASE + "/forbidden", timeout=5, retries=0)))

# c. captcha in a script is not a challenge; a real challenge still is
check("'captcha' only inside <script>, quote present -> VERBATIM", lambda: expect(cite("/captcha-script"), "VERBATIM"))
check("'captcha' only inside <script>, quote absent -> NOT_VERBATIM (no false abstain)",
      lambda: expect(cite("/captcha-script", "a sentence the page never says"), "NOT_VERBATIM"))
check("real challenge page -> UNVERIFIABLE", lambda: expect(cite("/challenge"), "UNVERIFIABLE", reason="anti-bot"))
check("challenge to dissent UA, real page to browser -> VERBATIM", lambda: expect(cite("/challenge-ua"), "VERBATIM"))

# d. JS shell abstains; ordinary text-light page does not
check("JS shell (text > 400 chars, mostly script) -> UNVERIFIABLE",
      lambda: expect(cite("/jsshell"), "UNVERIFIABLE", reason="script"))
check("ordinary text-light page, quote absent -> NOT_VERBATIM",
      lambda: expect(cite("/textlight", "the office is open every day"), "NOT_VERBATIM"))
check("ordinary text-light page, quote present -> VERBATIM",
      lambda: expect(cite("/textlight", "The office will be closed on Monday for the public holiday"), "VERBATIM"))

# e. PDFs
check("PDF: Tj/'/TJ text found -> VERBATIM",
      lambda: expect(cite("/doc.pdf", "The quick brown fox verifies every citation it is handed"), "VERBATIM"))
check("PDF: escaped parens and octal escape decoded -> VERBATIM",
      lambda: expect(cite("/doc.pdf", "is handed (sic) and café"), "VERBATIM"))
check("PDF: hex string decoded -> VERBATIM", lambda: expect(cite("/doc.pdf", "repeated in hex"), "VERBATIM"))
check("PDF: Type0 font through ToUnicode CMap (bfchar + bfrange) -> VERBATIM",
      lambda: expect(cite("/doc.pdf", CID_TEXT), "VERBATIM"))
check("PDF served as application/octet-stream is still read",
      lambda: expect(cite("/octet", "repeated in hex"), "VERBATIM"))
check("PDF: quote absent -> UNVERIFIABLE, never NOT_VERBATIM",
      lambda: expect(cite("/doc.pdf", "a sentence the report never says"), "UNVERIFIABLE",
                     reason="extraction attempted"))
check("PDF: RC4-encrypted, empty user password -> decrypted, VERBATIM",
      lambda: expect(cite("/enc.pdf", "The quick brown fox verifies every citation"), "VERBATIM"))
check("PDF: AES-encrypted -> UNVERIFIABLE with encryption reason",
      lambda: expect(cite("/aes.pdf"), "UNVERIFIABLE", reason="encrypted"))
check("PDF: image-only (no text) -> UNVERIFIABLE", lambda: expect(cite("/scanned.pdf"), "UNVERIFIABLE"))

# f. charset
check("latin-1 page declared by <meta charset> -> VERBATIM",
      lambda: expect(cite("/latin1", "Le café est ouvert tous les jours"), "VERBATIM"))

# verdict/JSON contract
check("verdict names unchanged, new fields are additions only",
      lambda: (lambda keys: (all(k in keys for k in ("resolved", "http_status", "verbatim", "unreadable",
                                                     "near_miss", "support", "transcripts", "error"))
                             and {"blocked", "fetch_note"} <= keys) or (_ for _ in ()).throw(
          AssertionError(sorted(keys))))(set(D.dataclasses.asdict(cite("/forbidden")))))

srv.shutdown()
failed = RESULTS.count(False)
print(f"\n---\ntests_run: {len(RESULTS)}\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
