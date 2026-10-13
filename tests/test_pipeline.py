"""Pipeline stages (bin/pl-*): stream formats, composition, sharding,
resumability. Fully mocked — no network, no key, no charges."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
sys.path.insert(0, BIN)
import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402


def load(name):
    path = os.path.join(BIN, name)
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = path
    loader.exec_module(mod)
    return mod


ask = load("pl-ask")
tally = load("pl-tally")
core = ask.core

SKELETON = {
    "tests_pass": {"type": "noul", "instructions": "Every case passes?"},
    "risk": {"type": "score", "instructions": "Blast radius?", "criteria": ["none", "low", "high"]},
    "fix": {"type": "choice", "instructions": "Fix ok?", "criteria": {"accept": "a", "review": "r"}},
}
GOAL = {"done": {"type": "choice", "instructions": "Done?",
                 "criteria": {"done": "yes", "not_done": "no"}}}

CLEAN_ENV = {k: v for k, v in os.environ.items()
             if not k.startswith(("CLEF_", "EXPERIENTIAL_", "EXPLABS_", "LUNA_", "PANEL_LOOP_"))}


def sh(args, stdin="", env=None):
    e = dict(CLEAN_ENV, HOME=tempfile.gettempdir() + "/pl-nohome")
    e.update(env or {})
    p = subprocess.run([sys.executable, os.path.join(BIN, args[0])] + args[1:],
                       input=stdin, capture_output=True, text=True, env=e, timeout=30)
    return p.returncode, p.stdout, p.stderr


def jl(text):
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def fake_call(log, votes=None):
    """Stand-in for core.call_judge; records calls, answers by type."""
    def call(judge, state, questions, *a, **kw):
        log.append({"judge": judge, "state": state, "qids": list(questions)})
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "choice":
                c = (votes or {}).get(judge) or next(iter(q["criteria"]))
                answers[qid] = {"type": "choice", "choice": c, "confidence": 0.9,
                                "probabilities": {c: 0.9}}
            elif q["type"] == "score":
                answers[qid] = {"type": "score", "score": 1.0, "confidence": 0.8}
            else:
                answers[qid] = {"type": "noul", "noul": 1.0}
        return {"answers": answers, "usage": {"input_tokens": 1, "output_tokens": 1}}, 1
    return call


def args_for(**kw):
    a = dict(jobs=1, shard=None, state_dir=None, timeout=60, endpoint=None,
             luna_endpoint=None, config=None, verbose=False)
    a.update(kw)
    return SimpleNamespace(**a)


class Files:
    def __init__(self, tmp):
        self.state = os.path.join(tmp, "state.md")
        self.skel = os.path.join(tmp, "skeleton.json")
        self.goal = os.path.join(tmp, "goal.json")
        with open(self.state, "w") as f:
            f.write("12/14 cases pass")
        with open(self.skel, "w") as f:
            json.dump(SKELETON, f)
        with open(self.goal, "w") as f:
            json.dump(GOAL, f)


class TestSplit(unittest.TestCase):
    def test_one_request_per_question_in_order(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            code, out, _ = sh(["pl-split", "--state-file", f.state, "--questions", f.skel, "--judge", "jev"])
        self.assertEqual(code, 0)
        reqs = jl(out)
        self.assertEqual([r["id"] for r in reqs], list(SKELETON))
        for r in reqs:
            self.assertEqual(len(r["questions"]), 1)
            self.assertEqual(r["judge"], "jev")
            self.assertEqual(r["state"], "12/14 cases pass")

    def test_batch_is_one_request(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            code, out, _ = sh(["pl-split", "--state-file", f.state, "--questions", f.skel, "--batch"])
        self.assertEqual(code, 0)
        self.assertEqual(len(jl(out)), 1)
        self.assertEqual(set(jl(out)[0]["questions"]), set(SKELETON))

    def test_per_judge_fans_out_to_enabled_judges(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            code, out, _ = sh(["pl-split", "--state-file", f.state, "--questions", f.goal, "--per-judge"],
                              env={"PANEL_LOOP_JUDGE_LUNA": "false"})
        self.assertEqual(code, 0)
        self.assertEqual([r["judge"] for r in jl(out)], ["clef", "clef-flash", "jev"])

    def test_per_judge_rejects_judge_flag(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            code, _, err = sh(["pl-split", "--state-file", f.state, "--questions", f.goal,
                               "--per-judge", "--judge", "jev"])
        self.assertEqual(code, core.EXIT_USAGE)

    def test_budget_checked_before_sending(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            with open(f.state, "w") as fh:
                fh.write("x" * 70000)
            code, out, err = sh(["pl-split", "--state-file", f.state, "--questions", f.skel])
        self.assertEqual(code, core.EXIT_DATA)
        self.assertEqual(out, "")
        self.assertIn("16384", err)


class TestAsk(unittest.TestCase):
    def reqs(self, n=5):
        return [{"id": f"q{i}", "judge": "clef", "state": "s",
                 "questions": {f"q{i}": SKELETON["tests_pass"]}} for i in range(n)]

    def run_ask(self, reqs, call, **kw):
        out = io.StringIO()
        with patch("sys.stderr", io.StringIO()):
            code = ask.process(reqs, args_for(**kw), call, out)
        return code, jl(out.getvalue())

    def test_parallel_preserves_input_order(self):
        log = []
        code, res = self.run_ask(self.reqs(8), fake_call(log), jobs=4)
        self.assertEqual(code, 0)
        self.assertEqual([r["id"] for r in res], [f"q{i}" for i in range(8)])
        self.assertEqual(len(log), 8)

    def test_resume_skips_cached_successes(self):
        with tempfile.TemporaryDirectory() as d:
            log = []
            self.run_ask(self.reqs(3), fake_call(log), state_dir=d)
            self.assertEqual(len(log), 3)
            code, res = self.run_ask(self.reqs(4), fake_call(log), state_dir=d)
            self.assertEqual(code, 0)
            self.assertEqual(len(log), 4, "only the new request is sent")
            self.assertEqual([r["cached"] for r in res], [True, True, True, False])
            self.assertFalse([p for p in os.listdir(d) if ".tmp." in p])

    def test_failures_not_cached_and_exit_code_propagates(self):
        def boom(judge, state, questions, **kw):
            raise core.ClefError("HTTP 429 from x", code=core.EXIT_RATELIMIT, status=429)
        with tempfile.TemporaryDirectory() as d:
            code, res = self.run_ask(self.reqs(2), boom, state_dir=d)
            self.assertEqual(os.listdir(d), [])
        self.assertEqual(code, core.EXIT_RATELIMIT)
        self.assertFalse(res[0]["ok"])
        self.assertEqual(res[0]["error"]["status"], 429)
        self.assertEqual(res[0]["error"]["stage"], "pl-ask")

    def test_round_changes_cache_key(self):
        r1 = dict(self.reqs(1)[0], round=1)
        r2 = dict(r1, round=2)
        self.assertNotEqual(ask.request_key(r1), ask.request_key(r2))

    def test_shards_partition_the_stream(self):
        stream = "\n".join(json.dumps(r) for r in self.reqs(7))
        seen = []
        for i in range(3):
            log = []
            out = io.StringIO()
            with patch.object(ask.core, "call_judge", fake_call(log)), patch("sys.stderr", io.StringIO()):
                ask.main(["--shard", f"{i}/3"], stdin=io.StringIO(stream), out=out)
            seen += [r["id"] for r in jl(out.getvalue())]
        self.assertEqual(sorted(seen), sorted(f"q{i}" for i in range(7)))

    def test_bad_shard_is_usage_error(self):
        with self.assertRaises(core.ClefError) as cm:
            ask.parse_shard("3/3")
        self.assertEqual(cm.exception.code, core.EXIT_USAGE)


class TestTally(unittest.TestCase):
    def vote(self, judge, choice):
        return {"id": "done", "judge": judge, "ok": True,
                "answers": {"done": {"type": "choice", "choice": choice,
                                     "probabilities": {choice: 1.0}}}}

    def test_majority_wins_exit_0(self):
        stream = "\n".join(json.dumps(self.vote(j, c)) for j, c in
                           [("clef", "done"), ("jev", "done"), ("luna", "not_done")])
        code, out, _ = sh(["pl-tally"], stdin=stream)
        self.assertEqual(code, 0)
        self.assertEqual(jl(out)[0]["winner"], "done")

    def test_tie_exits_2(self):
        stream = "\n".join(json.dumps(self.vote(j, c)) for j, c in
                           [("clef", "done"), ("jev", "not_done")])
        code, out, err = sh(["pl-tally"], stdin=stream)
        self.assertEqual(code, 2)
        self.assertTrue(jl(out)[0]["tie"])
        self.assertIn("--round 2", err)

    def test_double_vote_rejected(self):
        with self.assertRaises(core.ClefError):
            tally.tally_records([self.vote("clef", "done"), self.vote("clef", "done")])

    def test_failed_vote_rejected(self):
        with self.assertRaises(core.ClefError):
            tally.tally_records([self.vote("clef", "done"), {"judge": "jev", "ok": False}])

    def test_same_rule_as_clef_decide_panel(self):
        self.assertEqual(core.tally({"a": "x", "b": "y"})[1], ["x", "y"])
        self.assertEqual(core.tally({"a": "x", "b": "x", "c": "y"})[1], ["x"])


class TestComposition(unittest.TestCase):
    def test_sot_pipeline_matches_clef_decide_sot_state(self):
        """pl-split | pl-ask | pl-aggregate builds the same goal state as --sot."""
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            _, split_out, _ = sh(["pl-split", "--state-file", f.state, "--questions", f.skel])
            log = []
            out = io.StringIO()
            with patch.object(core, "call_judge", fake_call(log)), patch("sys.stderr", io.StringIO()):
                self.assertEqual(ask.main(["-P", "3"], stdin=io.StringIO(split_out), out=out), 0)
            code, agg_out, err = sh(["pl-aggregate", "--state-file", f.state, "--goal-file", f.goal],
                                    stdin=out.getvalue())
            self.assertEqual(code, 0, err)
            goal_req = jl(agg_out)[0]

            sot_log = []
            with patch.object(core, "call_judge", fake_call(sot_log)):
                core.sot_judge("12/14 cases pass", SKELETON, GOAL)
        self.assertEqual(goal_req["state"], sot_log[-1]["state"])
        self.assertEqual(list(goal_req["questions"]), ["done"])

    def test_aggregate_refuses_partial_skeleton(self):
        with tempfile.TemporaryDirectory() as t:
            f = Files(t)
            code, out, err = sh(["pl-aggregate", "--state-file", f.state, "--goal-file", f.goal],
                                stdin=json.dumps({"id": "risk", "ok": False}))
        self.assertEqual(code, core.EXIT_DATA)
        self.assertEqual(out, "")

    def test_render(self):
        rec = {"id": "q", "judge": "clef", "ok": True,
               "answers": {"q": {"type": "noul", "noul": 1.0}}, "usage": {}}
        code, out, _ = sh(["pl-render"], stdin=json.dumps(rec))
        self.assertEqual(code, 0)
        self.assertIn("[q] noul -> 1.0", out)

    def test_stages_are_executable(self):
        for n in ("pl-split", "pl-ask", "pl-aggregate", "pl-tally", "pl-render", "pl-judges"):
            self.assertTrue(os.access(os.path.join(BIN, n), os.X_OK), n)


if __name__ == "__main__":
    unittest.main()
