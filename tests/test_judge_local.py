#!/usr/bin/env python3
"""judge_local regression tests (op-008 cross-family run).

    python3 dissent/tests/test_judge_local.py dissent

The first cross-family run lost 12 of 35 Qwen3.8-27B votes: the reasoning model
spent the 1,200-token cap thinking, returned empty content, and the vote parsed
as UNCLEAR with nothing saying why. A fake OpenAI-compatible server on
127.0.0.1 replays that and the other shapes judge_local must handle. No
network, stdlib only.
"""
import http.server
import json
import pathlib
import sys
import threading

# Default to the repo root that holds dissent.py, derived from THIS file --
# not a cwd-relative "dissent", which only resolved when the suite happened to
# be run from the parent of the repo. A clone running `python3 tests/x.py`
# got ModuleNotFoundError.
root = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import dissent as D  # noqa: E402

SEEN: list[dict] = []
MODE = {"reply": None, "reject_kwargs": False}


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        if MODE["reject_kwargs"] and "chat_template_kwargs" in body:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"unknown field chat_template_kwargs"}')
            return
        out = json.dumps({"choices": [MODE["reply"]]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
EP = f"http://127.0.0.1:{srv.server_address[1]}"
failed = 0


def check(name, fn):
    global failed
    SEEN.clear()
    try:
        fn()
        print(f"  ok    {name}")
    except Exception as e:  # noqa: BLE001
        failed += 1
        print(f"  FAIL  {name}: {type(e).__name__}: {e}")


def call():
    return D.judge_local("claim", "quote", "https://x", "m", EP)


def asks_no_thinking_with_room():
    MODE.update(reply={"message": {"content": "SUPPORTS\nfine."}, "finish_reason": "stop"},
                reject_kwargs=False)
    assert call() == "SUPPORTS\nfine."
    assert SEEN[0]["chat_template_kwargs"] == {"enable_thinking": False}, SEEN[0]
    assert SEEN[0]["max_tokens"] >= 4000, SEEN[0]["max_tokens"]


def empty_content_is_an_error_not_unclear():
    MODE.update(reply={"message": {"content": "", "reasoning_content": "x" * 5000},
                       "finish_reason": "length"}, reject_kwargs=False)
    try:
        call()
    except RuntimeError as e:
        assert "no content" in str(e) and "finish_reason=length" in str(e) and "5000" in str(e), e
        return
    raise AssertionError("empty content returned instead of raising")


def null_content_is_an_error():
    MODE.update(reply={"message": {"content": None}, "finish_reason": "length"}, reject_kwargs=False)
    try:
        call()
    except RuntimeError:
        return
    raise AssertionError("null content returned instead of raising")


def inline_think_block_is_dropped():
    MODE.update(reply={"message": {"content": "<think>weighing it\nSUPPORTS maybe</think>\n"
                                              "DOES_NOT_SUPPORT\nno price."}}, reject_kwargs=False)
    out = call()
    assert D.parse_vote(out) == "DOES_NOT_SUPPORT", out


def server_rejecting_the_field_is_retried_without_it():
    MODE.update(reply={"message": {"content": "PARTIAL\ndate missing."}}, reject_kwargs=True)
    assert D.parse_vote(call()) == "PARTIAL"
    assert len(SEEN) == 2 and "chat_template_kwargs" not in SEEN[1], SEEN


print("dissent judge_local tests\n")
for f in (asks_no_thinking_with_room, empty_content_is_an_error_not_unclear, null_content_is_an_error,
          inline_think_block_is_dropped, server_rejecting_the_field_is_retried_without_it):
    check(f.__name__, f)
print(f"\n---\ntests_run: 5\ntests_failed: {failed}\n---")
sys.exit(1 if failed else 0)
