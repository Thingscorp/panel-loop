"""Judge on/off switches and structured, actionable errors.
Fully mocked — no network, no key, no charges."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402

_p = os.path.join(BIN, "clef-decide")
_l = importlib.machinery.SourceFileLoader("clef_decide_err", _p)
_s = importlib.util.spec_from_loader(_l.name, _l)
clef = importlib.util.module_from_spec(_s)
clef.__file__ = _p
_l.exec_module(clef)

Q = {"done": {"type": "choice", "instructions": "Done?", "criteria": {"done": "y", "not_done": "n"}}}
CLEAN = {k: v for k, v in os.environ.items()
         if not k.startswith(("CLEF_", "EXPERIENTIAL_", "EXPLABS_", "LUNA_", "PANEL_LOOP_"))}


def http_opener(status, body="nope"):
    def opener(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(body.encode()))
    return opener


class TestSwitches(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jf = os.path.join(self.tmp.name, "judges.conf")
        self.cfg = os.path.join(self.tmp.name, "config")
        e = patch.dict(os.environ, dict(CLEAN, PANEL_LOOP_JUDGES_FILE=self.jf), clear=True)
        e.start()
        self.addCleanup(e.stop)

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def test_default_all_on(self):
        self.assertEqual(clef.enabled_judges(self.cfg), clef.JUDGES)

    def test_repo_judges_conf_ships_all_on(self):
        sw = clef.judge_switches(judges_file=clef.DEFAULT_JUDGES_FILE)
        self.assertTrue(all(sw.values()))
        self.assertEqual(set(clef.read_config_file(clef.DEFAULT_JUDGES_FILE)), set(clef.JUDGES))

    def test_file_switch_off(self):
        self.write(self.jf, "luna=false\njev = off\n")
        self.assertEqual(clef.enabled_judges(self.cfg), ("clef", "clef-flash"))

    def test_precedence_env_over_config_over_file(self):
        self.write(self.jf, "jev=false\n")
        self.write(self.cfg, "judge.jev=true\n")
        self.assertIn("jev", clef.enabled_judges(self.cfg))
        with patch.dict(os.environ, {"PANEL_LOOP_JUDGE_JEV": "0"}):
            self.assertNotIn("jev", clef.enabled_judges(self.cfg))

    def test_hyphenated_env_name(self):
        with patch.dict(os.environ, {"PANEL_LOOP_JUDGE_CLEF_FLASH": "no"}):
            self.assertNotIn("clef-flash", clef.enabled_judges(self.cfg))

    def test_bad_value_is_config_error(self):
        self.write(self.jf, "luna=maybe\n")
        with self.assertRaises(clef.ClefError) as cm:
            clef.enabled_judges(self.cfg)
        self.assertEqual(cm.exception.code, clef.EXIT_CONFIG)

    def test_disabled_judge_refused_before_any_request(self):
        self.write(self.jf, "jev=false\n")
        called = []
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("jev", "s", Q, config_path=self.cfg, api_key="k",
                            _opener=lambda *a, **k: called.append(1))
        self.assertEqual(cm.exception.code, clef.EXIT_CONFIG)
        self.assertIn("PANEL_LOOP_JUDGE_JEV", cm.exception.fix)
        self.assertEqual(called, [])

    def test_panel_votes_only_with_enabled_judges(self):
        self.write(self.jf, "luna=false\nclef-flash=false\n")
        seen = []
        res = clef.panel_vote("s", Q, config_path=self.cfg,
                              _judge_fn=lambda j: (seen.append(j), ("done", {"done": 1.0}))[1])
        self.assertEqual(sorted(seen), ["clef", "jev"])
        self.assertEqual(res["winner"], "done")

    def test_panel_all_off(self):
        self.write(self.jf, "\n".join(f"{j}=false" for j in clef.JUDGES))
        with self.assertRaises(clef.ClefError) as cm:
            clef.panel_vote("s", Q, config_path=self.cfg, _judge_fn=lambda j: ("done", {}))
        self.assertEqual(cm.exception.code, clef.EXIT_CONFIG)


class TestStructuredErrors(unittest.TestCase):
    def setUp(self):
        e = patch.dict(os.environ, dict(CLEAN, CLEF_API_KEY="dummy",
                                        PANEL_LOOP_JUDGES_FILE="/nonexistent"), clear=True)
        e.start()
        self.addCleanup(e.stop)

    def err_for(self, status):
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("clef", "s", Q, config_path="/nonexistent", _opener=http_opener(status))
        return cm.exception

    def test_http_status_mapping(self):
        for status, code in [(401, clef.EXIT_AUTH), (403, clef.EXIT_AUTH),
                             (429, clef.EXIT_RATELIMIT), (404, clef.EXIT_CONFIG),
                             (400, clef.EXIT_DATA), (500, clef.EXIT_UNAVAILABLE),
                             (503, clef.EXIT_UNAVAILABLE)]:
            e = self.err_for(status)
            self.assertEqual(e.code, code, status)
            d = e.to_dict()
            self.assertEqual(d["status"], status)
            self.assertEqual(d["endpoint"], clef.ENDPOINT)
            self.assertEqual(d["model"], "clef")
            self.assertTrue(d["cause"] and d["fix"], status)

    def test_message_contract_unchanged(self):
        self.assertTrue(str(self.err_for(500)).startswith("HTTP 500 from "))

    def test_transport_and_protocol(self):
        def down(req, timeout=None):
            raise urllib.error.URLError("dns")
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("clef", "s", Q, config_path="/nonexistent", _opener=down)
        self.assertEqual(cm.exception.code, clef.EXIT_UNAVAILABLE)

        class R:
            def read(self): return b"<html>"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with self.assertRaises(clef.ClefError) as cm:
            clef.call_judge("clef", "s", Q, config_path="/nonexistent", _opener=lambda r, timeout=None: R())
        self.assertEqual(cm.exception.code, clef.EXIT_PROTOCOL)

    def test_panel_error_keeps_judge_and_code(self):
        def vote(j):
            if j == "jev":
                raise clef.ClefError("HTTP 401 from x", code=clef.EXIT_AUTH, status=401)
            return "done", {}
        with self.assertRaises(clef.ClefError) as cm:
            clef.panel_vote("s", Q, config_path="/nonexistent", _judge_fn=vote)
        d = cm.exception.to_dict()
        self.assertEqual((d["judge"], d["exit"], d["stage"]), ("jev", clef.EXIT_AUTH, "panel"))
        self.assertIn("[jev]", d["error"])

    def test_debug_logs_post_without_key(self):
        class R:
            def read(self): return json.dumps({"answers": {}, "usage": {}}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False
        err = io.StringIO()
        with patch.dict(os.environ, {"PANEL_LOOP_DEBUG": "1", "CLEF_API_KEY": "sekrit-xyz"}), \
                patch("sys.stderr", err):
            clef.call_judge("clef", "s", Q, config_path="/nonexistent",
                            _opener=lambda r, timeout=None: R())
        self.assertIn("debug: POST https://api.experientiallabs.ai/v1/systemone model=clef", err.getvalue())
        self.assertNotIn("sekrit-xyz", err.getvalue())

    def test_codes_distinct(self):
        codes = [clef.EXIT_INTERNAL, clef.EXIT_TIE, clef.EXIT_USAGE, clef.EXIT_DATA,
                 clef.EXIT_NOINPUT, clef.EXIT_UNAVAILABLE, clef.EXIT_RATELIMIT,
                 clef.EXIT_PROTOCOL, clef.EXIT_AUTH, clef.EXIT_CONFIG]
        self.assertEqual(len(set(codes)), len(codes))
        self.assertEqual(clef.EXIT_TIE, 2, "tie exit code is the original contract")

    def test_explabs_key_env_honored(self):
        with patch.dict(os.environ, {"EXPLABS_API_KEY": "ex"}, clear=True):
            self.assertEqual(clef.resolve_key(config_path="/nonexistent"), "ex")


class TestCliErrors(unittest.TestCase):
    def run_cli(self, args, env=None):
        e = dict(CLEAN, HOME=tempfile.gettempdir() + "/pl-nohome")
        e.update(env or {})
        p = subprocess.run([sys.executable, os.path.join(BIN, args[0])] + args[1:],
                           capture_output=True, text=True, env=e, timeout=30)
        return p.returncode, p.stdout, p.stderr

    def last_json(self, err):
        return json.loads([l for l in err.splitlines() if l.startswith("{")][-1])

    def files(self, tmp):
        s, q = os.path.join(tmp, "s"), os.path.join(tmp, "q.json")
        with open(s, "w") as f:
            f.write("state")
        with open(q, "w") as f:
            json.dump(Q, f)
        return s, q

    def test_missing_key_exit_77_structured(self):
        with tempfile.TemporaryDirectory() as t:
            s, q = self.files(t)
            code, out, err = self.run_cli(["clef-decide", "--state-file", s, "--questions", q])
        self.assertEqual(code, clef.EXIT_AUTH)
        d = self.last_json(err)
        self.assertEqual(d["stage"], "clef-decide")
        self.assertIn("key.env", d["fix"])
        self.assertIn("fix:", err)

    def test_missing_file_exit_66(self):
        code, _, err = self.run_cli(["clef-decide", "--state-file", "/nope", "--questions", "/nope"])
        self.assertEqual(code, clef.EXIT_NOINPUT)
        self.assertEqual(self.last_json(err)["exit"], 66)

    def test_bad_json_exit_65(self):
        with tempfile.TemporaryDirectory() as t:
            s, q = self.files(t)
            with open(q, "w") as f:
                f.write("{not json")
            code, _, _ = self.run_cli(["clef-decide", "--state-file", s, "--questions", q])
        self.assertEqual(code, clef.EXIT_DATA)

    def test_unknown_flag_exit_64_not_2(self):
        code, _, err = self.run_cli(["clef-decide", "--bogus"])
        self.assertEqual(code, clef.EXIT_USAGE)
        self.assertEqual(self.last_json(err)["exit"], 64)

    def test_disabled_judge_cli_exit_78(self):
        with tempfile.TemporaryDirectory() as t:
            s, q = self.files(t)
            code, _, err = self.run_cli(["clef-decide", "--state-file", s, "--questions", q,
                                         "--judge", "luna"],
                                        env={"CLEF_API_KEY": "dummy", "PANEL_LOOP_JUDGE_LUNA": "off"})
        self.assertEqual(code, clef.EXIT_CONFIG)
        self.assertEqual(self.last_json(err)["judge"], "luna")

    def test_verbose_debug_never_prints_key(self):
        with tempfile.TemporaryDirectory() as t:
            s, q = self.files(t)
            code, _, err = self.run_cli(["clef-decide", "--verbose", "--state-file", s,
                                         "--questions", q, "--judge", "luna"],
                                        env={"CLEF_API_KEY": "sekrit-123", "PANEL_LOOP_JUDGE_LUNA": "off"})
        self.assertIn("debug: judge=luna", err)
        self.assertNotIn("sekrit-123", err)

    def test_pl_ask_no_key_structured(self):
        req = {"id": "done", "judge": "clef", "state": "s", "questions": Q}
        e = dict(CLEAN, HOME=tempfile.gettempdir() + "/pl-nohome")
        p = subprocess.run([sys.executable, os.path.join(BIN, "pl-ask")], input=json.dumps(req),
                           capture_output=True, text=True, env=e, timeout=30)
        self.assertEqual(p.returncode, clef.EXIT_AUTH)
        rec = json.loads(p.stdout)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"]["stage"], "pl-ask")
        self.assertEqual(rec["error"]["judge"], "clef")
        self.assertIn("[done:clef]", rec["error"]["error"])


if __name__ == "__main__":
    unittest.main()
