"""Shared plumbing for the pl-* pipeline stages. Stdlib only.

Every stage reuses the core in bin/clef-decide (validation, routes, key
chain, no-retry POST, tribunal rule) so the Clef API contract lives in
exactly one place. Streams are JSONL: one JSON object per line.
"""
import importlib.machinery
import importlib.util
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def load_core():
    path = os.path.join(_HERE, "clef-decide")
    loader = importlib.machinery.SourceFileLoader("clef_decide_core", path)
    spec = importlib.util.spec_from_loader("clef_decide_core", loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = path
    loader.exec_module(mod)
    return mod


core = load_core()
ClefError = core.ClefError


def read_jsonl(stream, stage):
    """Yield (lineno, obj) for each non-blank line; bad JSON is EXIT_DATA."""
    for n, line in enumerate(stream, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ClefError(f"stdin line {n}: invalid JSON ({e})", code=core.EXIT_DATA,
                            stage=stage, fix="each stdin line must be one JSON object (JSONL)")
        if not isinstance(obj, dict):
            raise ClefError(f"stdin line {n}: expected a JSON object", code=core.EXIT_DATA,
                            stage=stage)
        yield n, obj


def write_jsonl(obj, out=None):
    out = out or sys.stdout
    out.write(json.dumps(obj, sort_keys=True) + "\n")
    out.flush()


def read_text(path, stage):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ClefError(f"cannot read {path}: {e}", code=core.EXIT_NOINPUT, stage=stage,
                        fix="check the file path")


def read_json(path, stage):
    try:
        return json.loads(read_text(path, stage))
    except json.JSONDecodeError as e:
        raise ClefError(f"invalid JSON in {path}: {e}", code=core.EXIT_DATA, stage=stage,
                        fix=f"validate with: python3 -m json.tool {path}")


def parser(prog, description):
    ap = core.UsageParser.make(prog=prog, description=description)
    core.add_common_flags(ap)
    return ap


def run(prog, fn, argv):
    """Run a stage body; map errors to structured stderr + exit code."""
    try:
        return fn(argv)
    except ClefError as e:
        return core.emit_error(prog, e, stage=prog)
    except BrokenPipeError:
        return core.EXIT_OK  # downstream closed early (e.g. | head): not our failure
