"""Mocked tests for configurable panel composition and custom judges:
adding/removing judges by preference (flag > $PANEL_JUDGES > config file)
and defining new judges in the config file. No network, no real keys.

Run: python3 -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import importlib.util
import importlib.machinery
spec = importlib.util.spec_from_file_location(
    "clef_decide_pc",
    os.path.join(os.path.dirname(__file__), "..", "bin", "clef-decide"),
    loader=importlib.machinery.SourceFileLoader("clef_decide_pc",
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


CHOICE_Q = {"done": {"type": "choice", "instructions": "All exit criteria true?",
                     "criteria": {"done": "everything green", "not_done": "work remains"}}}

ORACLE_CFG = ("judge_oracle_endpoint=https://oracle.test/v1/systemone\n"
              "judge_oracle_model=oracle-1\n"
              "judge_oracle_api_key=oracle-key-dummy\n")


def write_cfg(text):
    f = tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False)
    f.write(text)
    f.close()
    return f.name


def ballot(votes):
    def fn(judge):
        return votes[judge], {votes[judge]: 0.9}
    return fn


class CfgCase(unittest.TestCase):
    def setUp(self):
        self._cfgs = []
        # PANEL_JUDGES must not leak in from the environment running the tests
        self.env = patch.dict(os.environ, {"PANEL_JUDGES": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for p in self._cfgs:
            os.unlink(p)

    def cfg(self, text):
        p = write_cfg(text)
        self._cfgs.append(p)
        return p


class TestPanelPreference(CfgCase):
    def test_default_is_all_builtin_judges(self):
        empty = self.cfg("")
        self.assertEqual(clef.resolve_panel_judges(None, empty),
                         ["clef", "clef-flash", "jev", "luna", "subq"])

    def test_config_removes_judges(self):
        p = self.cfg("panel_judges=clef,jev,luna\n")
        self.assertEqual(clef.resolve_panel_judges(None, p),
                         ["clef", "jev", "luna"])

    def test_env_overrides_config(self):
        p = self.cfg("panel_judges=clef,jev,luna\n")
        with patch.dict(os.environ, {"PANEL_JUDGES": "jev,subq"}):
            self.assertEqual(clef.resolve_panel_judges(None, p), ["jev", "subq"])

    def test_flag_overrides_env_and_config(self):
        p = self.cfg("panel_judges=clef,jev,luna\n")
        with patch.dict(os.environ, {"PANEL_JUDGES": "jev,subq"}):
            self.assertEqual(clef.resolve_panel_judges("luna,clef", p),
                             ["luna", "clef"])

    def test_unknown_judge_in_list_fails_loudly(self):
        p = self.cfg("")
        with self.assertRaises(clef.ClefError):
            clef.resolve_panel_judges("clef,nobody", p)

    def test_empty_list_fails_loudly(self):
        p = self.cfg("")
        with self.assertRaises(clef.ClefError):
            clef.parse_judge_list(" , ", p)

    def test_duplicates_deduped_order_preserved(self):
        p = self.cfg("")
        self.assertEqual(clef.parse_judge_list("jev,clef,jev", p), ["jev", "clef"])

    def test_panel_vote_uses_configured_subset(self):
        seen = []
        def fn(judge):
            seen.append(judge)
            return "done", {"done": 0.9}
        p = self.cfg("panel_judges=jev,luna\n")
        res = clef.panel_vote("s", CHOICE_Q, _judge_fn=fn, config_path=p)
        self.assertEqual(res["winner"], "done")
        self.assertEqual(sorted(seen), ["jev", "luna"])
        self.assertEqual(sorted(res["rounds"][0]["votes"]), ["jev", "luna"])

    def test_panel_vote_env_subset(self):
        with patch.dict(os.environ, {"PANEL_JUDGES": "clef,subq"}):
            res = clef.panel_vote("s", CHOICE_Q,
                                  _judge_fn=ballot({"clef": "done", "subq": "done"}))
        self.assertEqual(sorted(res["rounds"][0]["votes"]), ["clef", "subq"])

    def test_panel_vote_explicit_judges_win(self):
        res = clef.panel_vote("s", CHOICE_Q, judges=["luna"],
                              _judge_fn=ballot({"luna": "not_done"}))
        self.assertEqual(res["winner"], "not_done")


class TestCustomJudges(CfgCase):
    def test_registered_includes_custom(self):
        p = self.cfg(ORACLE_CFG)
        self.assertIn("oracle", clef.registered_judges(p))
        self.assertEqual(clef.JUDGES, ("clef", "clef-flash", "jev", "luna", "subq"))

    def test_resolve_custom_judge(self):
        p = self.cfg(ORACLE_CFG)
        self.assertEqual(clef.resolve_judge("oracle", config_path=p),
                         ("custom", "oracle-1"))

    def test_custom_model_flag_rejected(self):
        p = self.cfg(ORACLE_CFG)
        with self.assertRaises(clef.ClefError):
            clef.resolve_judge("oracle", "other-model", p)

    def test_custom_call_posts_to_its_endpoint_with_its_key(self):
        cap = {}
        def opener(req, timeout=None):
            cap["url"] = req.full_url
            cap["auth"] = req.get_header("Authorization")
            cap["body"] = json.loads(req.data.decode())
            return FakeResp({"answers": {"done": {"type": "choice", "choice": "done",
                                                  "confidence": 0.8, "probabilities": {}}}})
        p = self.cfg(ORACLE_CFG)
        payload, _ = clef.call_judge("oracle", "s", CHOICE_Q,
                                     config_path=p, _opener=opener)
        self.assertEqual(cap["url"], "https://oracle.test/v1/systemone")
        self.assertEqual(cap["auth"], "Bearer oracle-key-dummy")
        self.assertEqual(cap["body"]["model"], "oracle-1")
        self.assertEqual(payload["answers"]["done"]["choice"], "done")

    def test_custom_key_from_env(self):
        p = self.cfg("judge_oracle_endpoint=https://oracle.test/v1/systemone\n"
                     "judge_oracle_model=oracle-1\n")
        with patch.dict(os.environ, {"JUDGE_ORACLE_API_KEY": "env-key"}):
            d = clef.resolve_custom_judge("oracle", p)
        self.assertEqual(d["api_key"], "env-key")

    def test_custom_missing_fields_named(self):
        p = self.cfg("judge_oracle_endpoint=https://oracle.test/v1/systemone\n")
        with self.assertRaises(clef.ClefError) as cm:
            clef.resolve_custom_judge("oracle", p)
        self.assertIn("judge_oracle_model", str(cm.exception))
        self.assertIn("judge_oracle_api_key", str(cm.exception))

    def test_custom_name_collision_with_builtin_rejected(self):
        p = self.cfg("judge_jev_endpoint=https://x.test/v1\n"
                     "judge_jev_model=m\njudge_jev_api_key=k\n")
        with self.assertRaises(clef.ClefError):
            clef.registered_judges(p)

    def test_custom_judge_can_join_panel(self):
        p = self.cfg(ORACLE_CFG + "panel_judges=clef,oracle\n")
        self.assertEqual(clef.resolve_panel_judges(None, p), ["clef", "oracle"])
        res = clef.panel_vote("s", CHOICE_Q, config_path=p,
                              _judge_fn=ballot({"clef": "done", "oracle": "done"}))
        self.assertEqual(res["winner"], "done")

    def test_unknown_judge_error_lists_registered(self):
        p = self.cfg(ORACLE_CFG)
        with self.assertRaises(clef.ClefError) as cm:
            clef.resolve_judge("nobody", config_path=p)
        self.assertIn("oracle", str(cm.exception))


class TestMainFlags(CfgCase):
    def _files(self):
        sf = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        sf.write("state"); sf.close()
        qf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(CHOICE_Q, qf); qf.close()
        self.addCleanup(os.unlink, sf.name)
        self.addCleanup(os.unlink, qf.name)
        return sf.name, qf.name

    def test_panel_judges_flag_reaches_panel_vote(self):
        s, q = self._files()
        res = {"qid": "done", "options": ["done", "not_done"], "rounds": [],
               "winner": "done", "tie": False, "mean_probabilities": {}}
        with patch.object(clef, "panel_vote", return_value=res) as pv:
            rc = clef.main(["--state-file", s, "--questions", q, "--panel",
                            "--panel-judges", "jev,luna"])
        self.assertEqual(rc, 0)
        self.assertEqual(pv.call_args.kwargs.get("judges"), ["jev", "luna"])

    def test_panel_judges_without_panel_is_an_error(self):
        s, q = self._files()
        with patch.object(clef, "call_judge") as cj:
            rc = clef.main(["--state-file", s, "--questions", q,
                            "--panel-judges", "jev,luna"])
        self.assertEqual(rc, 2)
        cj.assert_not_called()

    def test_judge_flag_accepts_custom_name(self):
        s, q = self._files()
        with patch.object(clef, "call_judge",
                          return_value=({"answers": {}}, 1)) as cj:
            clef.main(["--state-file", s, "--questions", q, "--judge", "oracle"])
        self.assertEqual(cj.call_args[0][0], "oracle")


if __name__ == "__main__":
    unittest.main()
