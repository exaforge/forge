"""Small, public, dependency-free coding workloads. Verifiers live separately."""

from textwrap import dedent


def source(text):
    return dedent(text).lstrip("\n")


TASKS = {
    "bug-fix": {
        "category": "bug fix",
        "prompt": "Fix ranges.collapse(values): return sorted inclusive (start, end) tuples for consecutive integers. Handle duplicates, negative numbers and empty input, and never mutate the input. Run the local tests. Do not modify tests.",
        "files": {
            "ranges.py": source('''
                def collapse(values):
                    if not values:
                        return []
                    values.sort()
                    result = []
                    start = previous = values[0]
                    for value in values[1:]:
                        if value != previous + 1:
                            result.append((start, previous))
                            start = value
                        previous = value
                    return result
            '''),
            "test_public.py": source('''
                import unittest
                from ranges import collapse

                class TestRanges(unittest.TestCase):
                    def test_runs(self):
                        self.assertEqual(collapse([4, 1, 2, 5]), [(1, 2), (4, 5)])
                    def test_empty(self):
                        self.assertEqual(collapse([]), [])
            '''),
        },
    },
    "multi-file-feature": {
        "category": "multi-file feature",
        "prompt": "Extend this shop package with per-SKU revenue reporting. Add shop.report.by_sku(orders), returning a dict of SKU to total quantity*price_cents across all rows, including negative quantities (returns). Add `python -m shop.cli --by-sku`: read the existing CSV format from stdin and print sorted `sku:total` lines, no heading; empty input produces no output. Preserve the existing default total output. Run tests; do not modify tests.",
        "files": {
            "shop/__init__.py": "",
            "shop/model.py": source('''
                import csv
                import io
                from dataclasses import dataclass

                @dataclass(frozen=True)
                class Order:
                    sku: str
                    quantity: int
                    price_cents: int

                def parse(text):
                    return [Order(row["sku"], int(row["quantity"]), int(row["price_cents"]))
                            for row in csv.DictReader(io.StringIO(text))]
            '''),
            "shop/report.py": source('''
                def total(orders):
                    return sum(order.quantity * order.price_cents for order in orders)
            '''),
            "shop/cli.py": source('''
                import sys
                from .model import parse
                from .report import total

                def main():
                    print(total(parse(sys.stdin.read())))

                if __name__ == "__main__":
                    main()
            '''),
            "test_public.py": source('''
                import unittest
                from shop.model import parse
                from shop.report import total

                class TestShop(unittest.TestCase):
                    def test_total(self):
                        orders = parse("sku,quantity,price_cents\\na,2,125\\nb,1,80\\n")
                        self.assertEqual(total(orders), 330)
            '''),
        },
    },
    "test-diagnosis": {
        "category": "test diagnosis",
        "prompt": "Diagnose the failing retry tests and fix retrying.retry(call, attempts, retry_on=(ValueError,), wait=lambda: None). attempts is the maximum total number of call invocations, must be a positive integer (bool is invalid). Retry only the supplied exception types, call wait exactly once between failed retryable attempts, return the successful value, and re-raise the final exception on exhaustion. Preserve non-retryable exceptions without waiting. Run tests; do not modify tests.",
        "files": {
            "retrying.py": source('''
                def retry(call, attempts, retry_on=(ValueError,), wait=lambda: None):
                    for _ in range(attempts - 1):
                        try:
                            return call()
                        except Exception:
                            wait()
                    return call()
            '''),
            "test_public.py": source('''
                import unittest
                from retrying import retry

                class TestRetry(unittest.TestCase):
                    def test_non_retryable(self):
                        waits = []
                        def call():
                            raise TypeError("stop")
                        with self.assertRaises(TypeError):
                            retry(call, 3, wait=lambda: waits.append(1))
                        self.assertEqual(waits, [])
                    def test_invalid(self):
                        with self.assertRaises(ValueError):
                            retry(lambda: 1, 0)
            '''),
        },
    },
    "exploration-change": {
        "category": "exploration plus change",
        "prompt": "Explore how batchrun resolves worker counts, then implement the documented precedence in batchrun.workers.resolve_workers(cli=None, config=None, environ=None): explicit CLI, then config['workers'], then environ['BATCH_WORKERS'], then 4. Only None means absent. The selected value must be an integer or a stripped base-10 integer string, greater than zero; reject bool, floats, malformed strings and nonpositive values with ValueError. Validate only the selected value, do not mutate mappings, and keep explicit environ={} independent of ambient environment. Run tests; do not modify tests.",
        "files": {
            "batchrun/__init__.py": "",
            "batchrun/defaults.py": "DEFAULT_WORKERS = 4\n",
            "batchrun/workers.py": source('''
                import os
                from .defaults import DEFAULT_WORKERS

                def resolve_workers(cli=None, config=None, environ=None):
                    config = config or {}
                    environ = environ or os.environ
                    return int(cli or environ.get("BATCH_WORKERS") or config.get("workers") or DEFAULT_WORKERS)
            '''),
            "batchrun/main.py": source('''
                from .workers import resolve_workers

                def describe(config, cli_workers=None):
                    return "workers=" + str(resolve_workers(cli_workers, config))
            '''),
            "test_public.py": source('''
                import unittest
                from batchrun.workers import resolve_workers

                class TestWorkers(unittest.TestCase):
                    def test_config_precedes_env(self):
                        self.assertEqual(resolve_workers(config={"workers": 2}, environ={"BATCH_WORKERS": "8"}), 2)
                    def test_default(self):
                        self.assertEqual(resolve_workers(environ={}), 4)
            '''),
        },
    },
    "refactor": {
        "category": "refactor with regression tests",
        "prompt": "Refactor pricing.py to remove the duplicated validation and integer rounding logic. Keep quote_regular(unit_cents, quantity), quote_student(...), quote_senior(...) signatures. Each wrapper must delegate to one shared helper defined in pricing.py; the shared helper owns validation and rounding. Preserve behavior: nonnegative integer inputs only (bool invalid), otherwise ValueError; regular/student/senior charge 100/90/80 percent of the extended price with half-up rounding to cents. Zero is valid. No float arithmetic. Run tests; do not modify tests.",
        "files": {
            "pricing.py": source('''
                def quote_regular(unit_cents, quantity):
                    if type(unit_cents) is not int or type(quantity) is not int or min(unit_cents, quantity) < 0:
                        raise ValueError("nonnegative integer inputs required")
                    return (unit_cents * quantity * 100 + 50) // 100

                def quote_student(unit_cents, quantity):
                    if type(unit_cents) is not int or type(quantity) is not int or min(unit_cents, quantity) < 0:
                        raise ValueError("nonnegative integer inputs required")
                    return (unit_cents * quantity * 90 + 50) // 100

                def quote_senior(unit_cents, quantity):
                    if type(unit_cents) is not int or type(quantity) is not int or min(unit_cents, quantity) < 0:
                        raise ValueError("nonnegative integer inputs required")
                    return (unit_cents * quantity * 80 + 50) // 100
            '''),
            "test_public.py": source('''
                import unittest
                import pricing

                class TestPricing(unittest.TestCase):
                    def test_quotes(self):
                        self.assertEqual(pricing.quote_regular(105, 2), 210)
                        self.assertEqual(pricing.quote_student(105, 1), 95)
                        self.assertEqual(pricing.quote_senior(103, 1), 82)
            '''),
        },
    },
}


def materialize(task_id, destination):
    """Produce the exact public starting workspace, with no inherited files."""
    task = TASKS[task_id]
    for relative, content in task["files"].items():
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (destination / "README.md").write_text(
        "# Evaluation fixture\n\nPython standard library only. Run `python3 -m unittest discover -v`.\n",
        encoding="utf-8",
    )
