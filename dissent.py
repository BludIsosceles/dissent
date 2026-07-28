#!/usr/bin/env python3
"""
dissent — cross-family citation and claim verification.

Catches the failure mode that kills AI-generated research: a citation that looks
real, reads plausibly, and does not say what it is claimed to say.

Three escalating layers, cheapest first:

  L1  RESOLVE   Does the URL actually resolve?                      (no model)
  L2  VERBATIM  Does the quoted text actually appear on the page?   (no model)
  L3  SUPPORT   Does the quote support the claim it is attached to? (models)

L1 and L2 are deterministic string work and cost nothing. They catch the single
most damaging defect class — a fabricated or misattributed quote — with zero
inference. Only claims that survive both reach L3, where independent model
families judge whether a real quote actually supports the claim, and any
disagreement between them is recorded rather than resolved away.

The dissent log is the product. Agreement is cheap; the informative signal is
where independent judges split.

Provenance: built by Delta Division, an autonomous AI organization. The method
is the one that caught a fabricated citation in Delta's own first research
operation — a quote attributed to a page that did not contain it, which read
perfectly and passed a same-family review.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import dataclasses
import html
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Iterable

__version__ = "0.4.0"

UA = "Mozilla/5.0 (compatible; dissent/0.1; +https://proiso.org/delta)"

# [tag] https://url — "quoted text"   (em dash, en dash, or double hyphen)
CITATION_RE = re.compile(
    r"\[(?P<tag>[a-z_]+)\]\s*"
    r"(?P<url>https?://[^\s\)\]>]+?)\s*"
    r"(?:—|–|--)\s*"
    r"[\"“](?P<quote>[^\"”]+)[\"”]",
    re.IGNORECASE,
)

VALID_TAGS = {
    "official_vendor",
    "regulatory_tos",
    "marketplace_listing",
    "transaction_data",
    "practitioner_forum",
    "job_posting",
    "third_party_analysis",
}


@dataclasses.dataclass
class Citation:
    tag: str
    url: str
    quote: str
    claim: str
    line: int

    resolved: bool | None = None
    http_status: int | None = None
    verbatim: bool | None = None
    unreadable: str | None = None   # why the page could not be read at all
    near_miss: str | None = None
    support: dict[str, str] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.resolved is False:
            return "UNRESOLVED"
        # A page whose text we cannot read is NOT evidence of a bad citation.
        # Conflating "the quote is absent" with "I cannot see this page" is what
        # produced a 38% false-positive rate against honest citations on
        # JavaScript-rendered pages, PDFs and app shells. Abstain instead of
        # accusing: silence with a reason, not a verdict the reader will trust.
        if self.unreadable:
            return "UNVERIFIABLE"
        if self.verbatim is False:
            return "NOT_VERBATIM"
        if not self.support:
            return "VERBATIM"
        votes = [v for v in self.support.values() if v in ("SUPPORTS", "DOES_NOT_SUPPORT")]
        if not votes:
            return "VERBATIM"
        if all(v == "SUPPORTS" for v in votes):
            return "SUPPORTED"
        if all(v == "DOES_NOT_SUPPORT" for v in votes):
            return "UNSUPPORTED"
        return "DISPUTED"

    @property
    def dissent(self) -> bool:
        return self.verdict == "DISPUTED"


# ---------------------------------------------------------------- text handling

def normalize(text: str) -> str:
    """Fold text so that quoting differences don't cause false negatives.

    Real pages differ from quotes in ways that are not dishonesty: curly vs
    straight quotes, HTML entities, non-breaking spaces, line wrapping. We
    normalize those away. We do NOT normalize away different words -- that is
    exactly the signal we are looking for.
    """
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = (
        # Single and double quotes are folded to ONE character. A source writing
        # "meritocracy" quoted as 'meritocracy' is a typographic difference, not
        # a dishonest citation, and treating it as one produced false positives
        # against perfectly good sources.
        text.replace("‘", '"').replace("’", '"').replace("'", '"')
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace(" ", " ")
    )
    text = re.sub(r"\s+", " ", text)
    # Collapse " ." -> "." so residual markup artefacts cannot break a match.
    text = re.sub(r"\s+([,.;:!?%\)\]])", r"\1", text)
    text = re.sub(r"([\(\[])\s+", r"\1", text)
    return text.strip().lower()


def strip_markup(raw: str) -> str:
    """Crude but dependency-free HTML to text.

    Deliberately keeps <meta> description content: a legitimate quote often
    comes from a meta tag that never renders as visible text. Delta hit exactly
    this case -- a quote that looked fabricated was real, and lived in the page's
    meta description.
    """
    # Parse each <meta> tag independently of attribute ORDER. The previous
    # pattern required name= before content=, so a page writing them the other
    # way round (python.org, among others) lost its meta text entirely and every
    # quote sourced from it read as fabricated.
    metas = []
    for tag in re.findall(r"<meta\b[^>]*>", raw, re.IGNORECASE):
        key = re.search(r'(?:name|property)\s*=\s*["\']?([\w:.-]+)', tag, re.IGNORECASE)
        val = re.search(r'content\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag, re.IGNORECASE)
        if key and val and key.group(1).lower() in (
                "description", "og:description", "og:title", "twitter:description"):
            metas.append(next(g for g in val.groups() if g is not None))
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    # Inline tags are removed WITHOUT a separator; block tags become whitespace.
    #
    # Replacing every tag with a space was the single largest source of false
    # positives in the v0.2 benchmark: "made out of <em>components</em>." became
    # "made out of components ." which no longer matches the sentence a human
    # reads on the page. Any quoted sentence containing a link, emphasis or code
    # span failed. Diagnosed by an independent adjudicator, not by us -- we had
    # wrongly blamed JavaScript rendering.
    body = re.sub(r"(?is)</?(?:a|em|strong|b|i|u|span|code|sup|sub|small|mark|abbr|cite|q|"
                  r"time|label|var|kbd|samp|dfn|s|del|ins|font|tt|big|wbr|bdi|bdo|ruby|rt|rp)"
                  r"(?:\s[^>]*)?>", "", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return " ".join(metas) + " " + body


def best_window(haystack: str, needle: str) -> str | None:
    """Find the closest near-miss for a quote that did not match exactly.

    Reports *why* a check failed: 'the page says X, you quoted Y' is actionable,
    'not found' is not. Anchors on the quote's longest rare word.
    """
    words = sorted(set(re.findall(r"[a-z0-9']{5,}", needle)), key=len, reverse=True)
    for w in words[:6]:
        i = haystack.find(w)
        if i != -1:
            lo, hi = max(0, i - 90), min(len(haystack), i + len(needle) + 90)
            return "..." + haystack[lo:hi].strip() + "..."
    return None


# ---------------------------------------------------------------- layers 1 & 2

MAX_BYTES = 40_000_000   # the HTML spec alone is ~15.6 MB; 4 MB silently hid
                         # any quote in the latter three-quarters of long documents


def fetch(url: str, timeout: int = 30, retries: int = 2) -> tuple[int | None, str, str | None]:
    """Fetch with a retry. A transient network failure is not evidence of a bad
    citation, and reporting it as one is a false accusation."""
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_BYTES).decode(
                    resp.headers.get_content_charset() or "utf-8", "replace")
                return resp.status, raw, None
        except urllib.error.HTTPError as e:
            return e.code, "", f"HTTP {e.code}"      # a real answer; do not retry
        except Exception as e:  # noqa: BLE001 - network reality is broad
            last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    return None, "", last


APP_SHELL = re.compile(
    r'<div[^>]+id=["\'](?:root|__next|app|__nuxt)["\']|data-reactroot|__NEXT_DATA__|ng-version=',
    re.IGNORECASE)


def readability(raw: str, text: str) -> str | None:
    """Can this page's text be read at all without executing JavaScript?

    Returns a reason string when the answer is no. Being wrong here in the
    cautious direction costs a missed detection; being wrong in the confident
    direction costs a false accusation against an honest citation, which is
    far more damaging to a verification tool's usefulness.
    """
    if len(raw) > 4000 and len(text) < 400:
        return "page body appears to require JavaScript (almost no text in raw HTML)"
    if APP_SHELL.search(raw) and len(text) < 1500:
        return "single-page-app shell detected with little server-rendered text"
    if raw.lstrip()[:5] == "%PDF-":
        return "PDF content — text extraction not supported"
    # Bot-block / challenge / stub response. Observed: python.org returns a
    # ~11 KB body with zero <meta> tags and no <title> to this client. That is
    # the SERVER declining to talk, not evidence about the citation, and
    # reporting it as NOT_VERBATIM accuses an honest source of being fake.
    if len(raw) < 25_000 and not re.search(r"<title[^>]*>", raw, re.I) \
            and not re.search(r"<meta\b", raw, re.I):
        return "response lacks <title> and <meta> — likely a bot-block or stub, not the real page"
    if re.search(r"(?i)(captcha|are you a robot|enable javascript to continue|"
                 r"access denied|cf-browser-verification|checking your browser)", raw[:6000]):
        return "anti-bot challenge page returned instead of content"
    return None


def check_citation(c: Citation) -> Citation:
    """L1 + L2. No model involved, no cost, fully deterministic."""
    status, raw, err = fetch(c.url)
    c.http_status = status
    if err or not raw:
        c.resolved = False
        c.error = err or "empty response"
        return c

    c.resolved = True
    page = normalize(strip_markup(raw))
    quote = normalize(c.quote)

    if quote in page:
        c.verbatim = True
        return c

    # Absent — but before accusing, ask whether we could read the page at all.
    reason = readability(raw, page)
    if reason:
        c.unreadable = reason
        return c

    c.verbatim = False
    c.near_miss = best_window(page, quote)
    return c


# ---------------------------------------------------------------- layer 3

JUDGE_PROMPT = """You are one of several independent verifiers from different model families.

A claim cites a source. The quoted sentence HAS been confirmed to appear verbatim on the
cited page — do not re-check that. Judge ONE thing only:

Does the quote actually support the claim it is attached to?

A real quote deployed to support a proposition it does not address is a defect. Example:
citing "These Terms govern your use of our website" to support a claim about PRICE is a
failure — the quote is genuine and proves nothing about price.

CLAIM: {claim}
QUOTE: "{quote}"
SOURCE: {url}

Answer with exactly one word on the first line: SUPPORTS or DOES_NOT_SUPPORT
Then one short sentence of reasoning.
"""


def judge_local(claim: str, quote: str, url: str, model: str, endpoint: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": JUDGE_PROMPT.format(claim=claim, quote=quote, url=url)}],
        # Reasoning models burn budget before emitting content; a low cap yields
        # an empty string. Verified on Qwen3.5-9B: >=1000 returns reliably.
        "max_tokens": 1200,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{endpoint}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"] or ""


def judge_cli(claim: str, quote: str, url: str, argv: list[str]) -> str:
    prompt = JUDGE_PROMPT.format(claim=claim, quote=quote, url=url)
    out = subprocess.run(
        [a.replace("{prompt}", prompt) for a in argv],
        capture_output=True, text=True, timeout=600,
    )
    return out.stdout


def parse_vote(text: str) -> str:
    t = (text or "").upper()
    # Order matters: DOES_NOT_SUPPORT contains SUPPORT as a substring.
    for token in ("DOES_NOT_SUPPORT", "DOES NOT SUPPORT"):
        if token in t:
            return "DOES_NOT_SUPPORT"
    return "SUPPORTS" if "SUPPORT" in t else "UNCLEAR"


# ---------------------------------------------------------------- parsing

def parse(path: str) -> list[Citation]:
    """Extract citations, treating the enclosing line as the claim."""
    cites: list[Citation] = []
    for n, line in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        for m in CITATION_RE.finditer(line):
            claim = CITATION_RE.sub("", line).strip(" -*|\t\n")
            cites.append(Citation(
                tag=m.group("tag").lower(),
                url=m.group("url").rstrip(".,;"),
                quote=m.group("quote").strip(),
                claim=claim or "(no surrounding claim text)",
                line=n,
            ))
    return cites


TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|ref|source)", re.I)


def canonical(url: str) -> str:
    """Reduce a URL to an identity comparable across cosmetic variants.

    Benchmarked at 0% on FAKE_INDEPENDENCE because the original check compared
    raw URL strings, so it only ever caught a literally identical URL repeated
    on one line. Real duplicate sourcing does not look like that -- it looks
    like a redirect, an AMP or canonical variant, a www/non-www pair, or the
    same article syndicated with tracking parameters attached.
    """
    from urllib.parse import urlsplit, parse_qsl, urlunsplit, urlencode
    u = urlsplit(url.strip())
    host = u.netloc.lower().removeprefix("www.").removesuffix(":443").removesuffix(":80")
    path = re.sub(r"/(amp|amp\.html)$", "", u.path.rstrip("/")) or "/"
    path = re.sub(r"\.(amp|html?)$", "", path)
    query = urlencode(sorted((k, v) for k, v in parse_qsl(u.query) if not TRACKING.match(k)))
    # No leading "//" — callers split on "/" to recover the host.
    return f"{host}{path}" + (f"?{query}" if query else "")


def resolve_final(url: str) -> str:
    """Follow redirects and return the canonicalised destination.

    A redirect and its target are ONE source. Costs one HEAD request; falls back
    to the declared URL on any failure rather than guessing.
    """
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return canonical(resp.url or url)
    except Exception:  # noqa: BLE001
        return canonical(url)


def independence_report(cites: Iterable[Citation]) -> list[str]:
    """Flag claims whose 'independent' sources are not independent.

    Two citations on the same line pointing at the same URL are one source
    wearing two hats. Delta shipped this defect three times in one operation
    before a cross-family monitor caught it.
    """
    by_line: dict[int, list[Citation]] = {}
    for c in cites:
        by_line.setdefault(c.line, []).append(c)

    problems = []
    for line, group in sorted(by_line.items()):
        if len(group) < 2:
            continue
        urls = [c.url for c in group]
        if len(set(urls)) < len(urls):
            problems.append(f"line {line}: same URL cited {len(urls)}x as if independent — one source, not {len(urls)}")
        else:
            # Cosmetically different URLs that resolve to the same place are
            # still one source. This is the case the first version missed.
            finals = {}
            for c in group:
                finals.setdefault(resolve_final(c.url), []).append(c.url)
            for final, originals in finals.items():
                if len(originals) > 1:
                    problems.append(
                        f"line {line}: {len(originals)} citations resolve to the SAME source "
                        f"({final}) — redirect/canonical/syndicated variants are one source, not {len(originals)}"
                    )
            hosts = [canonical(c.url).split("/")[0] for c in group]
            if len(set(hosts)) == 1 and len(hosts) > 1 and not any("SAME source" in p for p in problems[-2:]):
                problems.append(
                    f"line {line}: all {len(hosts)} citations are from the same host ({hosts[0]}) — "
                    "same-publisher sources are not independent corroboration"
                )
        tags = [c.tag for c in group]
        if len(set(tags)) == 1 and len(tags) > 1:
            problems.append(f"line {line}: {len(tags)} sources all tagged '{tags[0]}' — no type diversity")
    for c in cites:
        if c.tag not in VALID_TAGS:
            problems.append(f"line {c.line}: unknown tag '{c.tag}'")
    return problems


# ---------------------------------------------------------------- reporting

COLORS = {"UNRESOLVED": "\033[31m", "NOT_VERBATIM": "\033[31m", "DISPUTED": "\033[33m",
          "UNSUPPORTED": "\033[31m", "SUPPORTED": "\033[32m", "VERBATIM": "\033[32m",
          "UNVERIFIABLE": "\033[33m"}


def report(cites: list[Citation], problems: list[str], use_color: bool) -> int:
    def paint(s: str, v: str) -> str:
        return f"{COLORS.get(v, '')}{s}\033[0m" if use_color else s

    print(f"\ndissent {__version__} — {len(cites)} citation(s)\n" + "=" * 72)
    failed = 0
    for c in cites:
        v = c.verdict
        if v in ("UNRESOLVED", "NOT_VERBATIM", "UNSUPPORTED", "DISPUTED"):
            failed += 1
        print(f"\n{paint(v.ljust(13), v)} line {c.line}  [{c.tag}]")
        print(f"  claim : {c.claim[:130]}")
        print(f"  quote : \"{c.quote[:110]}\"")
        print(f"  url   : {c.url}")
        if c.error:
            print(f"  error : {c.error}")
        if c.unreadable:
            print(f"  ?? cannot verify: {c.unreadable}")
            print("     (this is NOT an accusation — the citation may be perfectly good)")
        if c.verbatim is False:
            print("  !! quoted text does NOT appear on the page")
            print(f"  page  : {c.near_miss}" if c.near_miss else "  page  : no similar passage found")
        if c.support:
            for judge, vote in c.support.items():
                print(f"  judge : {judge:<22} {vote}")
            if c.dissent:
                print("  >> DISSENT — independent judges disagree; a human should read this one")

    if problems:
        print("\n" + "=" * 72 + "\nSOURCE INDEPENDENCE\n")
        for p in problems:
            print(f"  !! {p}")

    print("\n" + "=" * 72)
    print(f"  clean {len(cites) - failed}   flagged {failed}   dissent {sum(c.dissent for c in cites)}"
          f"   independence issues {len(problems)}")
    return 1 if (failed or problems) else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="dissent",
        description="Verify citations actually say what they are claimed to say.",
    )
    ap.add_argument("file", help="markdown/text file containing [tag] URL — \"quote\" citations")
    ap.add_argument("--judge", action="append", default=[],
                    help="add a model judge for layer 3. 'local:MODEL' for an OpenAI-compatible "
                         "endpoint, or a shell command containing {prompt}. Repeat for cross-family.")
    ap.add_argument("--endpoint", default="http://localhost:8000", help="local OpenAI-compatible base URL")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    cites = parse(args.file)
    if not cites:
        print('no citations found — expected the form: [tag] https://url — "quoted text"', file=sys.stderr)
        return 2

    # L1 + L2, in parallel. Free, deterministic, and where most defects die.
    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        cites = list(pool.map(check_citation, cites))

    # L3 only for citations that earned it.
    eligible = [c for c in cites if c.verbatim]
    if args.judge and eligible:
        for spec in args.judge:
            name = spec.split(":", 1)[1] if spec.startswith("local:") else spec.split()[0]

            def run(c: Citation, spec=spec, name=name) -> None:
                try:
                    raw = (judge_local(c.claim, c.quote, c.url, spec.split(":", 1)[1], args.endpoint)
                           if spec.startswith("local:")
                           else judge_cli(c.claim, c.quote, c.url, spec.split()))
                    c.support[name] = parse_vote(raw)
                except Exception as e:  # noqa: BLE001
                    c.support[name] = f"ERROR ({type(e).__name__})"

            with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                list(pool.map(run, eligible))

    problems = independence_report(cites)

    if args.json:
        print(json.dumps(
            {"version": __version__,
             "citations": [{**dataclasses.asdict(c), "verdict": c.verdict} for c in cites],
             "independence_issues": problems},
            indent=2))
        return 1 if problems or any(
            c.verdict in ("UNRESOLVED", "NOT_VERBATIM", "UNSUPPORTED", "DISPUTED") for c in cites) else 0

    return report(cites, problems, use_color=not args.no_color and sys.stdout.isatty())


if __name__ == "__main__":
    sys.exit(main())
