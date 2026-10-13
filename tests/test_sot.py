"""Mocked tests for --sot (skeleton-of-judgment). No network, no key, no charges.

Run: python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import importlib.util
import importlib.machinery
spec = importlib.util.spec_from_file_location(
    "clef_decide_sot",
    os.path.join(os.path.dirname(__file__), "..", "bin", "clef-decide"),
    loader=importlib.machinery.SourceFileLoader("clef_decide_sot",
        os.path.join(os.path.dirname(__file__), "..", "bin", "clef-decide")))
clef = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clef)


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def answer_for(qid, q):
    """Type-appropriate fake answer for a question definition."""
    qtype = q.get("type")
    if qtype == "noul":
        return {"type": "noul", "noul": True}
    if qtype == "score":
        level = q["criteria"][0]
        return {"type": "score", "score": level, "confidence": 0.8}
    first = next(iter(q["criteria"]))
    return {"type": "choice", "choice": first, "confidence": 0.9,
            "probabilities": {first: 0.9}}


def sot_opener(captured, fail_qids=()):
    """Universal fake opener: SystemOne dict shape or Luna list shape."""
    def opener(req, timeout=None):
        captured.append({"url": req.full_url,
                         "body": json.loads(req.data.decode())})
        body = captured[-1]["body"]
        if "decisions" in req.full_url:
            names = [q["name"] for q in body["questions"]]
            defs = {q["name"]: {"type": q["type"],
                                "criteria": {c["value"]: c["description"]
                                             for c in q["choices"]}}
                    for q in body["questions"]}
        else:
            names = list(body["questions"].keys())
            defs = body["questions"]
        for qid in names:
            if qid in fail_qids:
                raise urllib.error.URLError("boom")
        if "decisions" in req.full_url:
            return FakeResp({"answers": [
                {"choice": answer_for(qid, defs[qid])["choice"],
                 "confidence": 0.9,
                 "probabilities": [{"value": answer_for(qid, defs[qid])["choice"],
                                    "probability": 0.9}]}
                for qid in names]})
        return FakeResp({"answers": {qid: answer_for(qid, defs[qid])
                                     for qid in names},
                         "usage": {"input_tokens": 10, "output_tokens": 5}})
    return opener


SKELETON = {
    "coverage": {"type": "noul", "instructions": "Every case is on the sheet?"},
    "risk": {"type": "score", "instructions": "Blast radius?",
             "criteria": ["none", "low", "high"]},
    "fix_review": {"type": "choice", "instructions": "Diff verdict?",
                   "criteria": {"accept": "minimal and correct",
                                "review": "needs another look"}},
}
GOAL = {"done": {"type": "choice", "instructions": "Loop done?",
                 "criteria": {"done": "everything green",
                              "not_done": "work remains"}}}


class TestSotJudge(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXPERIENTIAL_API_KEY": "test-key-dummy"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_each_point_is_its_own_request(self):
        captured = []
        result = clef.sot_judge("state", SKELETON, GOAL,
                                _opener=sot_opener(captured))
        # 3 point requests + 1 goal request
        self.assertEqual(len(captured), 4)
        for c in captured[:3]:
            self.assertEqual(len(c["body"]["questions"]), 1)
        self.assertEqual(set(result["skeleton"].keys()), set(SKELETON))

    def test_goal_state_carries_point_verdicts(self):
        captured = []
        clef.sot_judge("the state", SKELETON, GOAL,
                       _opener=sot_opener(captured))
        goal_body = captured[-1]["body"]
        self.assertEqual(list(goal_body["questions"].keys()), ["done"])
        state_sent = goal_body["state"]
        self.assertIn("the state", state_sent)
        for line in ("coverage -> True", "risk -> none", "fix_review -> accept"):
            self.assertIn(line, state_sent)

    def test_skeleton_order_preserved(self):
        captured = []
        result = clef.sot_judge("state", SKELETON, GOAL,
                                _opener=sot_opener(captured))
        self.assertEqual(result["skeleton_ids"], list(SKELETON.keys()))

    def test_error_names_failing_point(self):
        with self.assertRaises(clef.ClefError) as cm:
            clef.sot_judge("state", SKELETON, GOAL,
                           _opener=sot_opener([], fail_qids=("risk",)))
        self.assertIn("[skeleton:risk]", str(cm.exception))

    def test_goal_id_collision_rejected(self):
        bad_goal = {"coverage": GOAL["done"]}
        with self.assertRaises(clef.ClefError) as cm:
            clef.sot_judge("state", SKELETON, bad_goal,
                           _opener=sot_opener([]))
        self.assertIn("collides", str(cm.exception))

    def test_goal_must_be_single_question(self):
        bad_goal = dict(GOAL)
        bad_goal["extra"] = GOAL["done"]
        with self.assertRaises(clef.ClefError) as cm:
            clef.sot_judge("state", SKELETON, bad_goal,
                           _opener=sot_opener([]))
        self.assertIn("exactly one", str(cm.exception))

    def test_luna_point_still_choice_only(self):
        luna_skel = {"p": {"type": "noul", "instructions": "x?"}}
        with self.assertRaises(clef.ClefError) as cm:
            clef.sot_judge("state", luna_skel, GOAL, judge="luna",
                           _opener=sot_opener([]))
        self.assertIn("[skeleton:p]", str(cm.exception))
        self.assertIn("choice", str(cm.exception))

    def test_context_budget_per_request(self):
        big_state = "x" * (clef.MAX_CONTEXT_TOKENS * 4 + 100)
        with self.assertRaises(clef.ClefError) as cm:
            clef.sot_judge(big_state, {"p1": GOAL["done"]}, GOAL,
                           _opener=sot_opener([]))
        self.assertIn("[skeleton:p1]", str(cm.exception))

    def test_final_panel_uses_tribunal(self):
        captured = []
        panel_result = {"qid": "done", "options": ["done", "not_done"],
                        "rounds": [{"votes": {"clef": "done"}, "counts": {"done": 1}}],
                        "winner": "done", "tie": False,
                        "mean_probabilities": {"done": 0.9, "not_done": 0.1}}
        with patch.object(clef, "panel_vote",
                          return_value=panel_result) as pv:
            result = clef.sot_judge("state", SKELETON, GOAL, final_panel=True,
                                    _opener=sot_opener(captured))
            self.assertTrue(pv.called)
            self.assertTrue(result["final_panel"])
            self.assertEqual(result["final"]["winner"], "done")
        # point requests still went out as single-question requests
        self.assertEqual(len(captured), 3)

    def test_human_render(self):
        captured = []
        result = clef.sot_judge("state", SKELETON, GOAL,
                                _opener=sot_opener(captured))
        text = clef.render_sot(result)
        self.assertIn("[sot:skeleton] 3 points", text)
        self.assertIn("[sot:final]", text)
        self.assertIn("done -> done", text)


def run_main(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = clef.main(argv)
    return code, out.getvalue(), err.getvalue()


def fake_call_judge(captured):
    """call_judge replacement routing every request through a fake opener.

    sot_judge passes _opener positionally; panel_vote's _judge_vote passes
    it as a keyword. Handle both.
    """
    real = clef.call_judge
    opener = sot_opener(captured)

    def fake(*a, **k):
        if "_opener" in k:
            k["_opener"] = opener
            return real(*a, **k)
        return real(*a[:-1], _opener=opener, **k)

    return fake


def write_files(tmp, skeleton=SKELETON, goal=GOAL):
    paths = {}
    for name, obj in (("state.txt", "some state"),
                      ("skeleton.json", skeleton),
                      ("goal.json", goal)):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj))
        paths[name] = p
    return paths


class TestSotCli(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXPERIENTIAL_API_KEY": "test-key-dummy"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_sot_needs_skeleton_and_goal_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            code, _, err = run_main(["--sot", "--state-file", p["state.txt"],
                                     "--skeleton-file", p["skeleton.json"]])
            self.assertEqual(code, clef.EXIT_USAGE)  # usage errors: 64; 2 is reserved for a panel tie
            self.assertIn("--goal-file", err)

    def test_sot_rejects_questions_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            q = os.path.join(tmp, "q.json")
            with open(q, "w") as f:
                f.write("{}")
            code, _, err = run_main(["--sot", "--state-file", p["state.txt"],
                                     "--questions", q,
                                     "--skeleton-file", p["skeleton.json"],
                                     "--goal-file", p["goal.json"]])
            self.assertEqual(code, clef.EXIT_USAGE)  # usage errors: 64; 2 is reserved for a panel tie
            self.assertIn("--questions", err)

    def test_sot_rejects_panel_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            code, _, err = run_main(["--sot", "--panel",
                                     "--state-file", p["state.txt"],
                                     "--skeleton-file", p["skeleton.json"],
                                     "--goal-file", p["goal.json"]])
            self.assertEqual(code, clef.EXIT_USAGE)  # usage errors: 64; 2 is reserved for a panel tie
            self.assertIn("--final-panel", err)

    def test_sot_human_output(self):
        captured = []
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            with patch.object(clef, "call_judge", new=fake_call_judge(captured)):
                code, out, _ = run_main(["--sot",
                                         "--state-file", p["state.txt"],
                                         "--skeleton-file", p["skeleton.json"],
                                         "--goal-file", p["goal.json"]])
            self.assertEqual(code, 0)
            self.assertIn("[sot:skeleton]", out)
            self.assertIn("[sot:final]", out)

    def test_sot_json_output(self):
        captured = []
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            with patch.object(clef, "call_judge", new=fake_call_judge(captured)):
                code, out, _ = run_main(["--sot", "--json",
                                         "--state-file", p["state.txt"],
                                         "--skeleton-file", p["skeleton.json"],
                                         "--goal-file", p["goal.json"]])
            self.assertEqual(code, 0)
            data = json.loads(out)
            self.assertIn("skeleton", data)
            self.assertIn("final", data)

    def test_final_panel_tie_exits_2(self):
        tie = {"qid": "done", "options": ["done", "not_done"],
               "rounds": [], "winner": None, "tie": True,
               "mean_probabilities": {"done": 0.5, "not_done": 0.5}}
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            captured = []
            with patch.object(clef, "panel_vote", return_value=tie), \
                 patch.object(clef, "call_judge", new=fake_call_judge(captured)):
                code, out, _ = run_main(["--sot", "--final-panel",
                                         "--state-file", p["state.txt"],
                                         "--skeleton-file", p["skeleton.json"],
                                         "--goal-file", p["goal.json"]])
            self.assertEqual(code, 2)
            self.assertIn("TIE", out)

    def test_final_panel_winner_exits_0(self):
        win = {"qid": "done", "options": ["done", "not_done"],
               "rounds": [{"votes": {"clef": "done", "jev": "done",
                                     "luna": "done", "clef-flash": "done"},
                           "counts": {"done": 4}}],
               "winner": "done", "tie": False,
               "mean_probabilities": {"done": 0.9, "not_done": 0.1}}
        with tempfile.TemporaryDirectory() as tmp:
            p = write_files(tmp)
            captured = []
            with patch.object(clef, "panel_vote", return_value=win), \
                 patch.object(clef, "call_judge", new=fake_call_judge(captured)):
                code, out, _ = run_main(["--sot", "--final-panel",
                                         "--state-file", p["state.txt"],
                                         "--skeleton-file", p["skeleton.json"],
                                         "--goal-file", p["goal.json"]])
            self.assertEqual(code, 0)
            self.assertIn("winner: done", out)


if __name__ == "__main__":
    unittest.main()
