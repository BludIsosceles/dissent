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
import unicodedata
import urllib.error
import urllib.request
from typing import Iterable

__version__ = "0.1.0"

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
    near_miss: str | None = None
    support: dict[str, str] = dataclasses.field(default_factory=dict)
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.resolved is False:
            return "UNRESOLVED"
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
        text.replace("‘", "'").replace("’", "'")
        .replace("“", '"').replace("”", '"')
        .replace("–", "-").replace("—", "-")
        .replace(" ", " ")
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_markup(raw: str) -> str:
    """Crude but dependency-free HTML to text.

    Deliberately keeps <meta> description content: a legitimate quote often
    comes from a meta tag that never renders as visible text. Delta hit exactly
    this case -- a quote that looked fabricated was real, and lived in the page's
    meta description.
    """
    metas = re.findall(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|og:title)["\']'
        r'[^>]+content=["\']([^"\']*)["\']',
        raw,
        re.IGNORECASE,
    )
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
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

def fetch(url: str, timeout: int = 25) -> tuple[int | None, str, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
            return resp.status, raw, None
    except urllib.error.HTTPError as e:
        return e.code, "", f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 - network reality is broad
        return None, "", f"{type(e).__name__}: {e}"


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
    else:
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
        tags = [c.tag for c in group]
        if len(set(tags)) == 1 and len(tags) > 1:
            problems.append(f"line {line}: {len(tags)} sources all tagged '{tags[0]}' — no type diversity")
    for c in cites:
        if c.tag not in VALID_TAGS:
            problems.append(f"line {c.line}: unknown tag '{c.tag}'")
    return problems


# ---------------------------------------------------------------- reporting

COLORS = {"UNRESOLVED": "\033[31m", "NOT_VERBATIM": "\033[31m", "DISPUTED": "\033[33m",
          "UNSUPPORTED": "\033[31m", "SUPPORTED": "\033[32m", "VERBATIM": "\033[32m"}


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
