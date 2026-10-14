"""Behavioral proof for the README's hard-rules claims.

Each test produces the evidence the "Verified claims" section cites.
Fully mocked — no network, no key, no charges.

Run: python3 -m unittest tests.test_claims
"""
import io
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
BIN_PATH = os.path.join(REPO_ROOT, "bin", "clef-decide")

import importlib.util
import importlib.machinery
spec = importlib.util.spec_from_file_location(
    "clef_decide",
    BIN_PATH,
    loader=importlib.machinery.SourceFileLoader("clef_decide", BIN_PATH))
clef = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clef)

with open(BIN_PATH, "r", encoding="utf-8") as f:
    SOURCE = f.read()

QUESTIONS = {
    "review": {"type": "choice", "instructions": "Accept or request review?",
               "criteria": {"accept": "safe to merge", "review": "needs human eyes"}},
}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        return json.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def counting_opener(captured, behavior="ok"):
    def opener(req, timeout=None):
        captured["calls"] = captured.get("calls", 0) + 1
        captured["headers"] = dict(req.header_items())
        if behavior == "boom":
            import urllib.error
            raise urllib.error.URLError("connection reset")
        return FakeResp({"answers": {}, "usage": {}})
    return opener


class TestClaimEvidence(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"EXPERIENTIAL_API_KEY": "test-key-dummy"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_endpoints_are_the_only_real_urls_in_source(self):
        raw = set(re.findall(r"https?://[^\s\"']+", SOURCE))
        # strip trailing punctuation picked up from prose; drop placeholders
        urls = {u.rstrip(";,).") for u in raw}
        urls = {u for u in urls if "..." not in u and "example" not in u
                and u not in {"https://", "http://"}}  # bare scheme checks in code
        self.assertEqual(urls, {"https://api.experientiallabs.ai/v1/systemone",
                                "https://api.experientiallabs.ai/v1/decisions",
                                "https://api.subq.ai/v1/systemone"},
                         f"unexpected URLs in source: {urls}")
        self.assertEqual(clef.ENDPOINT, "https://api.experientiallabs.ai/v1/systemone")
        self.assertEqual(clef.LUNA_ENDPOINT, "https://api.experientiallabs.ai/v1/decisions")
        self.assertEqual(clef.SUBQ_ENDPOINT, "https://api.subq.ai/v1/systemone")

    def test_no_retry_on_transport_error(self):
        cap = {}
        import urllib.error
        with self.assertRaises(clef.ClefError):
            clef.call_clef("clef", "s", QUESTIONS,
                           _opener=counting_opener(cap, behavior="boom"))
        self.assertEqual(cap.get("calls"), 1,
                         "a transport failure must trigger exactly one attempt")

    def test_single_post_on_success(self):
        cap = {}
        clef.call_clef("clef", "s", QUESTIONS, _opener=counting_opener(cap))
        self.assertEqual(cap.get("calls"), 1)

    def test_no_idempotency_header_sent(self):
        cap = {}
        clef.call_clef("clef", "s", QUESTIONS, _opener=counting_opener(cap))
        lowered = {k.lower(): v for k, v in cap["headers"].items()}
        self.assertNotIn("idempotency-key", lowered)

    def test_key_never_prompted_interactively(self):
        self.assertNotRegex(SOURCE, r"\binput\s*\(")
        self.assertNotRegex(SOURCE, r"\bgetpass\b")
        self.assertIn("EXPERIENTIAL_API_KEY", SOURCE)

    def test_constants_match_claims(self):
        self.assertEqual(clef.MAX_CONTEXT_TOKENS, 16384)
        self.assertEqual(clef.MAX_QUESTIONS, 32)
        self.assertEqual(clef.MAX_CHOICE_OPTIONS, 64)
        self.assertEqual(clef.MIN_SCORE_LEVELS, 2)
        self.assertEqual(clef.MAX_SCORE_LEVELS, 10)
        self.assertEqual(clef.DEFAULT_TIMEOUT, 60)

    def test_model_allowlist_pinned(self):
        self.assertEqual(clef.ALLOWED_MODELS, ("clef", "clef-flash", "jev-latest"))
        self.assertEqual(clef.JUDGES, ("clef", "clef-flash", "jev", "luna", "subq"))
        self.assertEqual(clef.LUNA_MODEL, "gpt-6-luna-decisions")
        self.assertEqual(clef.SUBQ_MODEL, "subq-snap-preview")
        self.assertEqual(clef.DEFAULT_MODEL, "clef")

    def test_helper_is_executable(self):
        self.assertTrue(os.access(BIN_PATH, os.X_OK))

    def test_stdlib_only_imports(self):
        stdlib = {"argparse", "base64", "io", "json", "os", "re", "sys",
                  "time", "unittest", "urllib.error", "urllib.request"}
        mods = set(re.findall(r"^import (\S+)|^from (\S+) import", SOURCE, re.M))
        # findall returns tuples; flatten
        flat = set()
        for a, b in mods:
            flat.add((a or b).split(".")[0])
        third_party = {m for m in flat if m not in stdlib and not m.startswith("_")}
        # top-level module names actually imported
        imported = set()
        for m in re.finditer(r"^(?:import|from)\s+([a-zA-Z_][\w.]*)", SOURCE, re.M):
            imported.add(m.group(1).split(".")[0])
        non_std = imported - {"argparse", "json", "os", "sys", "urllib",
                              "importlib", "unittest", "io", "re", "base64"}
        self.assertEqual(non_std, set(), f"non-stdlib imports: {non_std}")


if __name__ == "__main__":
    unittest.main()
