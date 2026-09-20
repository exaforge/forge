#!/usr/bin/env python3
"""Independent fixed verifier; run outside the agent's working directory.

This is an integrity check, not an OS security sandbox for hostile submissions.
Only fixed check labels and counters are emitted, never source or exception text.
"""

import ast
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True

# Fixed public identifiers only: the runner allowlists these before retention.
CHECK_LABELS = {
    "bug-fix": {f"{kind}-{i}" for kind in ("ranges", "input-preserved") for i in range(5)},
    "multi-file-feature": {"grouped-revenue", "empty-revenue", "original-total", "cli-group", "cli-group-empty", "cli-total"},
    "test-diagnosis": {"successful-value", "attempt-count", "exhaustion", "exhaustion-counts",
                       "exception-filter-original", "nonretryable-single-call", "no-wait-on-other",
                       "invalid-attempts", "invalid-attempts-no-call", "single-attempt",
                       "original-final-exception", "custom-retry-type"},
    "exploration-change": {"cli-precedence", "config-precedence", "env-precedence", "empty-env-is-explicit",
                           "ambient-env", "none-falls-through", "invalid-selected-value", "mapping-preservation"},
    "refactor": {f"quote_{kind}-{check}" for kind in ("regular", "student", "senior")
                 for check in ("arithmetic", "validation")} | {"shared-local-helper", "no-float-arithmetic"},
}


class Checks:
    def __init__(self):
        self.passed = 0
        self.total = 0
        self.failed = []

    def check(self, label, condition):
        self.total += 1
        if condition:
            self.passed += 1
        else:
            self.failed.append(label)

    def raises(self, label, exception, call):
        try:
            call()
        except exception:
            self.check(label, True)
        except Exception:
            self.check(label, False)
        else:
            self.check(label, False)


def bug_fix(checks, workspace):
    collapse = importlib.import_module("ranges").collapse
    for i, (values, expected) in enumerate([
        ([], []), ([3], [(3, 3)]), ([4, 1, 2, 5], [(1, 2), (4, 5)]),
        ([2, 2, 1, 4, 4], [(1, 2), (4, 4)]),
        ([-1, -3, -2, 2, 0], [(-3, 0), (2, 2)]),
    ]):
        original = values.copy()
        checks.check(f"ranges-{i}", collapse(values) == expected)
        checks.check(f"input-preserved-{i}", values == original)


def multi_file_feature(checks, workspace):
    model = importlib.import_module("shop.model")
    report = importlib.import_module("shop.report")
    text = "sku,quantity,price_cents\nz,3,101\na,2,50\nz,-1,101\na,1,7\n"
    orders = model.parse(text)
    checks.check("grouped-revenue", report.by_sku(orders) == {"z": 202, "a": 107})
    checks.check("empty-revenue", report.by_sku([]) == {})
    checks.check("original-total", report.total(orders) == 309)
    # -I removes environment/user import paths; the known fixture path is explicit.
    launch = "import runpy,sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('shop.cli',run_name='__main__')"
    for args, data, expected in [
        (["--by-sku"], text, "a:107\nz:202\n"),
        (["--by-sku"], "", ""), ([], text, "309\n"),
    ]:
        result = subprocess.run([sys.executable, "-I", "-c", launch, str(workspace), *args],
                                input=data, capture_output=True, text=True, timeout=5)
        checks.check("cli-" + ("group" if args else "total") + ("-empty" if not data else ""),
                     result.returncode == 0 and result.stdout == expected)


def test_diagnosis(checks, workspace):
    retry = importlib.import_module("retrying").retry
    calls, waits = [], []
    def succeeds():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("retry")
        return 17
    checks.check("successful-value", retry(succeeds, 3, wait=lambda: waits.append(1)) == 17)
    checks.check("attempt-count", len(calls) == 3 and len(waits) == 2)
    calls.clear()
    waits.clear()
    def fails():
        calls.append(1)
        raise ValueError("retry")
    checks.raises("exhaustion", ValueError, lambda: retry(fails, 2, wait=lambda: waits.append(1)))
    checks.check("exhaustion-counts", len(calls) == 2 and len(waits) == 1)
    waits.clear()
    other_calls = []
    nonretryable = TypeError("stop")
    def other():
        other_calls.append(1)
        raise nonretryable
    try:
        retry(other, 5, wait=lambda: waits.append(1))
    except TypeError as error:
        checks.check("exception-filter-original", error is nonretryable)
    except Exception:
        checks.check("exception-filter-original", False)
    else:
        checks.check("exception-filter-original", False)
    checks.check("nonretryable-single-call", len(other_calls) == 1)
    checks.check("no-wait-on-other", waits == [])
    for value in [0, -1, True, 1.5, "2"]:
        invalid_calls = []
        checks.raises("invalid-attempts", ValueError, lambda value=value: retry(lambda: invalid_calls.append(1), value))
        checks.check("invalid-attempts-no-call", not invalid_calls)
    checks.check("single-attempt", retry(lambda: 8, 1) == 8)
    custom_calls = []
    sentinel = KeyError("final")
    def custom():
        custom_calls.append(1)
        raise sentinel
    try:
        retry(custom, 2, retry_on=(KeyError,))
    except KeyError as error:
        checks.check("original-final-exception", error is sentinel)
    else:
        checks.check("original-final-exception", False)
    checks.check("custom-retry-type", len(custom_calls) == 2)


def exploration_change(checks, workspace):
    resolve = importlib.import_module("batchrun.workers").resolve_workers
    checks.check("cli-precedence", resolve(3, {"workers": "bad"}, {"BATCH_WORKERS": "bad"}) == 3)
    checks.check("config-precedence", resolve(None, {"workers": " 7 "}, {"BATCH_WORKERS": "bad"}) == 7)
    checks.check("env-precedence", resolve(None, {}, {"BATCH_WORKERS": "6"}) == 6)
    os.environ["BATCH_WORKERS"] = "91"
    checks.check("empty-env-is-explicit", resolve(environ={}) == 4)
    checks.check("ambient-env", resolve() == 91)
    checks.check("none-falls-through", resolve(config={"workers": None}, environ={}) == 4)
    for value in [0, -1, True, 1.1, "bad", "0", "1.0", "", []]:
        checks.raises("invalid-selected-value", ValueError, lambda value=value: resolve(value, {}, {}))
    config, environ = {"workers": "8"}, {"BATCH_WORKERS": "9"}
    resolve(config=config, environ=environ)
    checks.check("mapping-preservation", config == {"workers": "8"} and environ == {"BATCH_WORKERS": "9"})


def refactor(checks, workspace):
    pricing = importlib.import_module("pricing")
    wrappers = ("quote_regular", "quote_student", "quote_senior")
    for name, percent in zip(wrappers, (100, 90, 80)):
        quote = getattr(pricing, name)
        for price, quantity in [(0, 7), (5, 1), (105, 1), (103, 2), (10**18 + 5, 3)]:
            checks.check(name + "-arithmetic", quote(price, quantity) == (price * quantity * percent + 50) // 100)
        for price, quantity in [(-1, 2), (2, -1), (True, 2), (1, False), (1.5, 2), (2, "3")]:
            checks.raises(name + "-validation", ValueError, lambda p=price, q=quantity: quote(p, q))
    tree = ast.parse((workspace / "pricing.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    shared = set(functions) - set(wrappers)
    for name in wrappers:
        body = [node for node in functions[name].body if not (
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))]
        direct = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
        shared &= {direct.func.id} if isinstance(direct, ast.Call) and isinstance(direct.func, ast.Name) else set()
    checks.check("shared-local-helper", bool(shared))
    checks.check("no-float-arithmetic", not any(
        isinstance(node, ast.Div) or isinstance(node, ast.Constant) and isinstance(node.value, float)
        or isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float"
        for node in ast.walk(tree)))


VERIFIERS = {"bug-fix": bug_fix, "multi-file-feature": multi_file_feature,
             "test-diagnosis": test_diagnosis, "exploration-change": exploration_change,
             "refactor": refactor}


def main():
    task, workspace = sys.argv[1], Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(workspace))
    checks = Checks()
    failure = None
    # Submitted code can print; discard it rather than accidentally retaining content.
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            VERIFIERS[task](checks, workspace)
    except BaseException:
        failure = "submission_exception"
    result = {"passed": failure is None and not checks.failed and checks.total > 0,
              "checks_passed": checks.passed, "checks_total": checks.total,
              "failed_checks": checks.failed, "error": failure}
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
