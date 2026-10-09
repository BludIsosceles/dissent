#!/usr/bin/env python3
"""--wayback (v0.9): before accusing, ask archive.org whether the quote was ever there.

The six wrong accusations left in the v0.8 KPMG benchmark were three 404s and
three pages rewritten after they were cited. These tests pin the contract:

  * a hit in an archived copy is ARCHIVED_ONLY: flagged, never a pass, never NOT_VERBATIM
  * a miss in every sampled copy leaves the accusation standing, with the evidence noted
  * an archive we could not ask, or could not read, changes nothing
  * only citations that would be ACCUSED are sent to the archive
  * without --wayback, JSON output is v0.8's, key for key

No network: the CDX lookup and the snapshot fetch are replaced with fakes.
"""
import pathlib, sys, dataclasses
root = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

QUOTE = "Group net income rose to a record level in the first nine months"
PAGE_WITH = f"<html><body><article>{'<p>Filler about banking results and outlook. </p>' * 30}<p>{QUOTE}.</p></article></body></html>"
PAGE_WITHOUT = f"<html><body><article>{'<p>Filler about banking results and outlook. </p>' * 31}</article></body></html>"

run = failed = 0
calls = []

def case(name, fn):
    global run, failed
    run += 1
    saved = (D.wayback_snapshots, D.fetch_page, D._cdx, D.time.sleep, D.check_citation)
    calls.clear(); D._ARCHIVE_CACHE.clear()
    try:
        fn(); print(f"  PASS  {name}")
    except AssertionError as e:
        failed += 1; print(f"  FAIL  {name}: {e}")
    except Exception as e:  # noqa: BLE001
        failed += 1; print(f"  FAIL  {name}: unexpected {type(e).__name__}: {e}")
    finally:
        D.wayback_snapshots, D.fetch_page, D._cdx, D.time.sleep, D.check_citation = saved

def cite(**kw):
    c = D.Citation("third_party_analysis", "https://bank.example/results", QUOTE, "claim", 1)
    for k, v in kw.items():
        setattr(c, k, v)
    return c

def archive(snaps, pages, final=None):
    """snaps: [(ts, orig)]; pages: ts -> html | None (unreadable) | Exception.
    final: ts -> URL the replay ended at (default: the snapshot itself)."""
    def cdx(url, timeout=30):
        calls.append(("cdx", url)); return sorted(snaps)
    def fetch(url, timeout=30, retries=2):
        calls.append(("get", url))
        ts = url.split("/web/")[1].split("id_")[0]
        html = pages.get(ts)
        if isinstance(html, Exception):
            raise html
        if html is None:
            return D.Fetched(503, err="HTTP 503", refused="server error")
        return D.Fetched(200, html.encode(), "text/html", "utf-8",
                         final_url=(final or {}).get(ts, url))
    D.wayback_snapshots, D.fetch_page = cdx, fetch

SNAPS = [("20190101000000", "https://bank.example/results"), ("20210101000000", "https://bank.example/results"),
         ("20230101000000", "https://bank.example/results"), ("20250101000000", "https://bank.example/results"),
         ("20260101000000", "https://bank.example/results")]

def rewritten_page_found_in_older_copy():
    archive(SNAPS, {ts: (PAGE_WITH if ts < "2022" else PAGE_WITHOUT) for ts, _ in SNAPS})
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "ARCHIVED_ONLY", c.verdict
    assert c.verdict in D.FLAGGED, "ARCHIVED_ONLY must be flagged"
    assert c.archived["timestamp"] < "2022", c.archived
    assert "no longer contains" in c.archive_note, c.archive_note

def gone_page_found_in_newest_copy():
    archive(SNAPS, {ts: PAGE_WITH for ts, _ in SNAPS})
    c = D.check_archive(cite(resolved=False, http_status=404))
    assert c.verdict == "ARCHIVED_ONLY", c.verdict
    assert c.archived["timestamp"] == "20260101000000", "newest copy should be tried first"
    assert "HTTP 404" in c.archive_note, c.archive_note

def absent_everywhere_keeps_accusation():
    archive(SNAPS, {ts: PAGE_WITHOUT for ts, _ in SNAPS})
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "NOT_VERBATIM", c.verdict
    assert c.archived is None and c.archive_note == "4 archived copies read, quote in none (5 known)", c.archive_note

def unreadable_snapshots_are_not_counted():
    archive(SNAPS[:2], {})
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "NOT_VERBATIM" and c.archive_note == "no archived copy could be read (2 known; 2 unreadable)", \
        c.archive_note
    assert "absent" not in c.archive_note and "quote in none" not in c.archive_note

def archive_unreachable_changes_nothing():
    def boom(url, timeout=30):
        raise OSError("network down")
    D.wayback_snapshots = boom
    c = D.check_archive(cite(resolved=False, http_status=410))
    assert c.verdict == "UNRESOLVED" and "not consulted" in c.archive_note, (c.verdict, c.archive_note)

def no_snapshots():
    archive([], {})
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "NOT_VERBATIM" and c.archive_note == "no archived copy exists"

def only_accused_citations_consulted():
    archive(SNAPS, {ts: PAGE_WITH for ts, _ in SNAPS})
    for c in (cite(resolved=True, verbatim=True),                       # verified
              cite(resolved=True, unreadable="js app shell"),           # abstained
              cite(resolved=True, verbatim=False, unreadable="pdf"),    # abstained, inconsistent fields
              cite(blocked="HTTP 403", unreadable="HTTP 403"),          # refused
              cite(resolved=False, http_status=None),                   # DNS failure, not a 404
              cite(resolved=True, verbatim=False,
                   url="https://web.archive.org/web/2020/https://bank.example/results")):
        before = c.verdict
        D.check_archive(c)
        assert c.verdict == before and c.archive_note is None, f"{before} consulted the archive"
    assert not calls, f"archive was called: {calls}"

def snapshot_exception_does_not_abort():
    archive(SNAPS, {"20260101000000": OSError("archive reset"), "20190101000000": PAGE_WITH})
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "ARCHIVED_ONLY", f"{c.verdict}: {c.archive_note}"
    archive(SNAPS[:1], {"20190101000000": RuntimeError("boom")}); D._ARCHIVE_CACHE.clear()
    c = D.check_archive(cite(resolved=True, verbatim=False))
    assert c.verdict == "NOT_VERBATIM" and "1 unreadable" in c.archive_note, c.archive_note

def redirect_to_another_page_is_not_evidence():
    other = "https://web.archive.org/web/20200101000000id_/https://bank.example/some-other-page"
    same = "https://web.archive.org/web/20200102000000id_/http://www.bank.example/results/"
    archive(SNAPS[:1], {"20190101000000": PAGE_WITH}, final={"20190101000000": other})
    c = D.check_archive(cite(resolved=False, http_status=404))
    assert c.verdict == "UNRESOLVED" and "1 redirected to another page" in c.archive_note, c.archive_note
    D._ARCHIVE_CACHE.clear()
    archive(SNAPS[:1], {"20190101000000": PAGE_WITH}, final={"20190101000000": same})
    c = D.check_archive(cite(resolved=False, http_status=404))
    assert c.verdict == "ARCHIVED_ONLY", f"same page (scheme/www/slash differ) refused: {c.archive_note}"

def fragment_not_sent():
    archive(SNAPS, {ts: PAGE_WITH for ts, _ in SNAPS})
    D.check_archive(cite(resolved=True, verbatim=False, url="https://bank.example/results#q3"))
    assert ("cdx", "https://bank.example/results") in calls, calls

def sampling_order():
    s = [(f"20{i:02d}0101000000", "u") for i in range(10)]
    got = [ts[:4] for ts, _ in D.sample_snapshots(s)]
    assert got[:2] == ["2009", "2000"], f"newest then oldest expected, got {got}"
    assert len(got) == D.WAYBACK_SAMPLE and len(set(got)) == len(got), got
    assert [t for t, _ in D.sample_snapshots(s[:3])] == [t for t, _ in s[:3]][::-1]
    for n in range(1, 60):
        got = D.sample_snapshots([(f"{i:014d}", "u") for i in range(n)])
        assert len(got) == min(n, D.WAYBACK_SAMPLE) and len(set(got)) == len(got), (n, got)

def json_unchanged_without_wayback():
    c = cite(resolved=True, verbatim=False)
    keys = set(D.citation_json(c))
    assert "archived" not in keys and "archive_note" not in keys, keys
    v08 = {f.name for f in dataclasses.fields(D.Citation)} - {"archived", "archive_note",
           "declared_meta", "page_meta", "meta_issues", "meta_unchecked"} | {"verdict"}
    assert keys == v08, keys ^ v08

def cdx_parse_dedupes_and_sorts():
    import io, json as J
    U = "https://u.example/"
    T = lambda y: f"{y}0101000000"
    rows = [["timestamp", "original", "digest"], [T(2024), U, "A"], [T(2020), U, "B"], [T(2025), U, "A"],
            [T(2021), U, ""], [T(2022), U, ""], ["2023", U, "C"], [T(2019), "ftp://x", "D"], "junk"]
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False
    saved = D.urllib.request.urlopen
    D.urllib.request.urlopen = lambda req, timeout=30: R(J.dumps(rows).encode())
    try:
        assert D._cdx("u", 30) == [(T(2020), U), (T(2021), U), (T(2022), U), (T(2024), U)], D._cdx("u", 30)
        rows = [[T(2020), U, "A"]]                       # no header row: nothing dropped
        assert D._cdx("u", 30) == [(T(2020), U)]
        rows = {"error": "x"}
        try:
            D._cdx("u", 30); raise AssertionError("non-list CDX body accepted")
        except ValueError:
            pass
    finally:
        D.urllib.request.urlopen = saved

def one_lookup_per_url_and_failures_cached():
    n = []
    def cdx(url, timeout):
        n.append(url)
        if "bad" in url:
            raise OSError("down")
        return [("2020", url)]
    D.time.sleep = lambda s: None
    D._cdx = cdx
    for _ in range(3):
        assert D.wayback_snapshots("https://a.example/") == [("2020", "https://a.example/")]
    for _ in range(3):
        try:
            D.wayback_snapshots("https://bad.example/"); raise AssertionError("failure not raised")
        except OSError:
            pass
    assert n.count("https://a.example/") == 1, f"repeat lookups: {n}"
    assert n.count("https://bad.example/") == 2, f"expected one retry then a cached failure: {n}"

def cli_wayback_json_end_to_end():
    import contextlib, io, json as J, tempfile
    archive(SNAPS, {ts: PAGE_WITH for ts, _ in SNAPS})
    D.check_citation = lambda c: (setattr(c, "resolved", False), setattr(c, "http_status", 404), c)[2]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tf:
        tf.write(f'claim [third_party_analysis] https://bank.example/results — "{QUOTE}"\n')
    argv = sys.argv
    try:
        for flag, want_verdict, want_exit in ((["--wayback"], "ARCHIVED_ONLY", 1), ([], "UNRESOLVED", 1)):
            D._ARCHIVE_CACHE.clear()
            sys.argv = ["dissent", tf.name, "--json"] + flag
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = D.main()
            c = J.loads(out.getvalue())["citations"][0]
            assert (c["verdict"], code) == (want_verdict, want_exit), (flag, c["verdict"], code)
            assert ("archived" in c) == bool(flag), f"archive keys present={'archived' in c} with {flag}"
            if flag:
                assert c["archived"]["timestamp"] == "20260101000000" and "HTTP 404" in c["archive_note"], c
    finally:
        sys.argv = argv
        pathlib.Path(tf.name).unlink()

print("dissent --wayback tests")
for n, f in [("rewritten page: quote found in an older copy", rewritten_page_found_in_older_copy),
             ("gone page: quote found in the newest copy", gone_page_found_in_newest_copy),
             ("absent from every copy: accusation stands", absent_everywhere_keeps_accusation),
             ("unreadable snapshots are not evidence", unreadable_snapshots_are_not_counted),
             ("archive unreachable: nothing changes", archive_unreachable_changes_nothing),
             ("no snapshots: nothing changes", no_snapshots),
             ("only accused citations are consulted", only_accused_citations_consulted),
             ("sampling: newest, oldest, then between", sampling_order),
             ("JSON without --wayback is v0.8's", json_unchanged_without_wayback),
             ("CDX rows deduped by digest, sorted", cdx_parse_dedupes_and_sorts),
             ("one CDX lookup per URL; failures cached", one_lookup_per_url_and_failures_cached),
             ("an exception on one copy never aborts", snapshot_exception_does_not_abort),
             ("replay redirected elsewhere is not evidence", redirect_to_another_page_is_not_evidence),
             ("fragment is not sent to the archive", fragment_not_sent),
             ("CLI: --wayback --json end to end", cli_wayback_json_end_to_end)]:
    case(n, f)
print(f"---\ntests_run: {run}\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
