"""Mocked tests for multi-judge routing (--judge), the Luna decisions route,
and the --panel tribunal vote. No network, no key, no charges.

Run: python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

import importlib.util
import importlib.machinery
spec = importlib.util.spec_from_file_location(
    "clef_decide_j",
    os.path.join(os.path.dirname(__file__), "..", "bin", "clef-decide"),
    loader=importlib.machinery.SourceFileLoader("clef_decide_j",
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


def luna_opener(captured, choice="done"):
    def opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResp({"answers": [{"choice": choice, "confidence": 0.9,
                                      "probabilities": [{"value": "done", "probability": 0.9},
                                                        {"value": "not_done", "probability": 0.1}]}]})
    return opener


CHOICE_Q = {"done": {"type": "choice", "instructions": "All exit criteria true?",
                     "criteria": {"done": "everything green", "not_done": "work remains"}}}
MIXED_Q = {"done": {"type": "choice", "instructions": "x",
                    "criteria": {"done": "y", "not_done": "z"}},
           "risk": {"type": "noul", "instructions": "risky?"}}


class TestJudgeRouting(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXPERIENTIAL_API_KEY": "test-key-dummy"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_judge_routes(self):
        self.assertEqual(clef.resolve_judge("clef"), ("systemone", "clef"))
        self.assertEqual(clef.resolve_judge("clef-flash"), ("systemone", "clef-flash"))
        self.assertEqual(clef.resolve_judge("jev"), ("systemone", "jev-latest"))
        self.assertEqual(clef.resolve_judge("luna"), ("decisions", "gpt-6-luna-decisions"))

    def test_unknown_judge_rejected(self):
        with self.assertRaises(clef.ClefError):
            clef.resolve_judge("gpt-4")

    def test_model_override_systemone(self):
        self.assertEqual(clef.resolve_judge("clef", "clef-flash:free"),
                         ("systemone", "clef-flash:free"))
        self.assertEqual(clef.resolve_judge("jev", "jev-latest"),
                         ("systemone", "jev-latest"))

    def test_model_override_rejected_for_luna(self):
        with self.assertRaises(clef.ClefError):
            clef.resolve_judge("luna", "clef")

    def test_model_override_still_allowlisted(self):
        with self.assertRaises(clef.ClefError):
            clef.resolve_judge("clef", "gpt-4")

    def test_jev_posts_to_systemone(self):
        cap = {}
        def opener(req, timeout=None):
            cap["url"] = req.full_url
            cap["body"] = json.loads(req.data.decode())
            return FakeResp({"answers": {}, "usage": {}})
        clef.call_judge("jev", "s", {"q": {"type": "noul", "instructions": "x"}},
                        _opener=opener)
        self.assertEqual(cap["url"], "https://api.experientiallabs.ai/v1/systemone")
        self.assertEqual(cap["body"]["model"], "jev-latest")


class TestLunaRoute(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXPERIENTIAL_API_KEY": "test-key-dummy"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_luna_posts_to_decisions_with_proven_shape(self):
        cap = {}
        payload, _ = clef.call_judge("luna", "state blob", CHOICE_Q,
                                     _opener=luna_opener(cap))
        self.assertEqual(cap["url"], "https://api.experientiallabs.ai/v1/decisions")
        body = cap["body"]
        self.assertEqual(body["model"], "gpt-6-luna-decisions")
        self.assertEqual(body["input"], "state blob")
        q = body["questions"][0]
        self.assertEqual(q["type"], "choice")
        self.assertEqual(q["name"], "done")
        self.assertEqual(q["choices"],
                         [{"value": "done", "description": "everything green"},
                          {"value": "not_done", "description": "work remains"}])
        # normalized to the SystemOne answers shape
        self.assertEqual(payload["answers"]["done"]["choice"], "done")
        self.assertEqual(payload["answers"]["done"]["probabilities"]["done"], 0.9)

    def test_luna_rejects_non_choice(self):
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("luna", "s", MIXED_Q, _opener=luna_opener({}))
        self.assertIn("choice", str(cm.exception).lower())

    def test_luna_endpoint_precedence(self):
        self.assertEqual(clef.resolve_luna_endpoint("https://x.test/d"),
                         "https://x.test/d")
        with patch.dict(os.environ, {"LUNA_ENDPOINT": "https://e.test/d"}):
            self.assertEqual(clef.resolve_luna_endpoint(), "https://e.test/d")
        self.assertEqual(clef.resolve_luna_endpoint(),
                         "https://api.experientiallabs.ai/v1/decisions")

    def test_luna_endpoint_must_be_https(self):
        with self.assertRaises(clef.ClefError):
            clef.resolve_luna_endpoint("http://x.test/d")

    def test_luna_answer_count_mismatch_raises(self):
        def opener(req, timeout=None):
            return FakeResp({"answers": [{"choice": "done"}]})  # 1 answer, 2 questions
        two_q = {"a": {"type": "choice", "instructions": "x",
                       "criteria": {"done": "y", "not_done": "z"}},
                 "b": {"type": "choice", "instructions": "x",
                       "criteria": {"done": "y", "not_done": "z"}}}
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("luna", "s", two_q, _opener=opener)
        self.assertIn("2 questions", str(cm.exception))

    def test_luna_shares_key_chain(self):
        cap = {}
        def opener(req, timeout=None):
            cap["auth"] = req.get_header("Authorization")
            return FakeResp({"answers": [{"choice": "done", "confidence": 0.9,
                                           "probabilities": []}]})
        with patch.dict(os.environ, {"CLEF_API_KEY": "shared-key"}):
            clef.call_judge("luna", "s", CHOICE_Q, _opener=opener)
        self.assertEqual(cap["auth"], "Bearer " + "shared" + "-key")


def ballot(votes):
    """_judge_fn factory: judge -> (choice, probs). votes: {judge: choice}."""
    def fn(judge):
        c = votes[judge]
        other = "not_done" if c == "done" else "done"
        return c, {c: 0.8, other: 0.2}
    return fn


class TestPanelVote(unittest.TestCase):
    def test_majority_wins(self):
        res = clef.panel_vote("s", CHOICE_Q, _judge_fn=ballot(
            {"clef": "done", "clef-flash": "done", "jev": "not_done", "luna": "done"}))
        self.assertEqual(res["winner"], "done")
        self.assertFalse(res["tie"])
        self.assertEqual(len(res["rounds"]), 1)
        self.assertAlmostEqual(res["mean_probabilities"]["done"], 0.65)

    def test_tie_reruns_then_decides(self):
        calls = {"n": 0}
        def fn(judge):
            calls["n"] += 1
            # round 1: 2-2 tie; round 2: 3-1
            first = calls["n"] <= 4
            c = {"clef": "done", "clef-flash": "done",
                 "jev": "not_done", "luna": "not_done"}[judge]
            if not first and judge == "luna":
                c = "done"
            other = "not_done" if c == "done" else "done"
            return c, {c: 0.8, other: 0.2}
        res = clef.panel_vote("s", CHOICE_Q, _judge_fn=fn)
        self.assertEqual(len(res["rounds"]), 2)
        self.assertEqual(res["winner"], "done")
        self.assertFalse(res["tie"])

    def test_double_tie_escalates(self):
        res = clef.panel_vote("s", CHOICE_Q, _judge_fn=ballot(
            {"clef": "done", "clef-flash": "done",
             "jev": "not_done", "luna": "not_done"}))
        self.assertEqual(len(res["rounds"]), 2)
        self.assertIsNone(res["winner"])
        self.assertTrue(res["tie"])
        human = clef.render_panel(res)
        self.assertIn("TIE", human)
        self.assertIn("escalate", human)

    def test_panel_needs_exactly_one_choice_question(self):
        with self.assertRaises(clef.ClefError):
            clef.panel_vote("s", {}, _judge_fn=ballot({}))
        with self.assertRaises(clef.ClefError):
            clef.panel_vote("s", MIXED_Q, _judge_fn=ballot({}))
        with self.assertRaises(clef.ClefError):
            clef.panel_vote("s", {"q": {"type": "noul", "instructions": "x"}},
                            _judge_fn=ballot({}))

    def test_panel_error_names_the_judge(self):
        def fn(judge):
            if judge == "jev":
                raise clef.ClefError("transport boom")
            return "done", {"done": 0.9, "not_done": 0.1}
        with self.assertRaises(clef.ClefError) as cm:
            clef.panel_vote("s", CHOICE_Q, _judge_fn=fn)
        self.assertIn("[jev]", str(cm.exception))
        self.assertIn("transport boom", str(cm.exception))

    def test_panel_error_before_any_round_completes(self):
        # a judge failure aborts the vote; it is not a tie and never re-runs
        calls = {"n": 0}
        def fn(judge):
            calls["n"] += 1
            raise clef.ClefError("down")
        with self.assertRaises(clef.ClefError):
            clef.panel_vote("s", CHOICE_Q, _judge_fn=fn)
        self.assertLessEqual(calls["n"], 4)

    def test_render_panel_shows_votes(self):
        res = clef.panel_vote("s", CHOICE_Q, _judge_fn=ballot(
            {"clef": "done", "clef-flash": "done", "jev": "done", "luna": "done"}))
        human = clef.render_panel(res)
        self.assertIn("winner: done", human)
        self.assertIn("clef=done", human)


class TestPanelFlagConflict(unittest.TestCase):
    def _files(self):
        import tempfile
        sf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        sf.write("state"); sf.close()
        qf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(CHOICE_Q, qf); qf.close()
        self.addCleanup(os.unlink, sf.name)
        self.addCleanup(os.unlink, qf.name)
        return sf.name, qf.name

    def test_panel_rejects_judge_flag(self):
        s, q = self._files()
        with patch.object(clef, "panel_vote") as pv:
            rc = clef.main(["--state-file", s, "--questions", q,
                            "--panel", "--judge", "luna"])
        self.assertEqual(rc, clef.EXIT_USAGE)  # usage errors: 64; 2 is reserved for a panel tie
        pv.assert_not_called()

    def test_panel_rejects_model_flag(self):
        s, q = self._files()
        with patch.object(clef, "panel_vote") as pv:
            rc = clef.main(["--state-file", s, "--questions", q,
                            "--panel", "--model", "clef-flash"])
        self.assertEqual(rc, clef.EXIT_USAGE)  # usage errors: 64; 2 is reserved for a panel tie
        pv.assert_not_called()

    def test_panel_alone_reaches_vote(self):
        s, q = self._files()
        res = {"qid": "done", "options": ["done", "not_done"], "rounds": [],
               "winner": "done", "tie": False, "mean_probabilities": {}}
        with patch.object(clef, "panel_vote", return_value=res) as pv:
            rc = clef.main(["--state-file", s, "--questions", q, "--panel"])
        self.assertEqual(rc, 0)
        pv.assert_called_once()

    def test_default_judge_is_clef(self):
        s, q = self._files()
        with patch.object(clef, "call_judge",
                          return_value=({"answers": {}}, 1)) as cj:
            clef.main(["--state-file", s, "--questions", q])
        self.assertEqual(cj.call_args[0][0], "clef")


if __name__ == "__main__":
    unittest.main()
