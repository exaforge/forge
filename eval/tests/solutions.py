"""Known-correct implementations used only for offline runner/verifier tests."""
from textwrap import dedent

SOLUTIONS = {
    "ranges.py": '''
        def collapse(values):
            ordered = sorted(set(values))
            result = []
            for value in ordered:
                if result and value == result[-1][1] + 1:
                    result[-1] = (result[-1][0], value)
                else:
                    result.append((value, value))
            return result
    ''',
    "shop/report.py": '''
        def total(orders):
            return sum(order.quantity * order.price_cents for order in orders)
        def by_sku(orders):
            result = {}
            for order in orders:
                result[order.sku] = result.get(order.sku, 0) + order.quantity * order.price_cents
            return result
    ''',
    "shop/cli.py": '''
        import argparse
        import sys
        from .model import parse
        from .report import total, by_sku
        def main():
            parser = argparse.ArgumentParser()
            parser.add_argument("--by-sku", action="store_true")
            args = parser.parse_args()
            orders = parse(sys.stdin.read())
            if args.by_sku:
                for sku, value in sorted(by_sku(orders).items()):
                    print(f"{sku}:{value}")
            else:
                print(total(orders))
        if __name__ == "__main__":
            main()
    ''',
    "retrying.py": '''
        def retry(call, attempts, retry_on=(ValueError,), wait=lambda: None):
            if type(attempts) is not int or attempts <= 0:
                raise ValueError("positive integer attempts required")
            for attempt in range(attempts):
                try:
                    return call()
                except retry_on:
                    if attempt + 1 == attempts:
                        raise
                    wait()
    ''',
    "batchrun/workers.py": '''
        import os
        from .defaults import DEFAULT_WORKERS
        def resolve_workers(cli=None, config=None, environ=None):
            config = {} if config is None else config
            environ = os.environ if environ is None else environ
            selected = next((value for value in (cli, config.get("workers"), environ.get("BATCH_WORKERS"))
                             if value is not None), DEFAULT_WORKERS)
            if type(selected) is not int and type(selected) is not str:
                raise ValueError("integer required")
            try:
                result = int(selected)
            except (ValueError, TypeError):
                raise ValueError("integer required") from None
            if result <= 0:
                raise ValueError("positive required")
            return result
    ''',
    "pricing.py": '''
        def quote(unit_cents, quantity, percent):
            if type(unit_cents) is not int or type(quantity) is not int or min(unit_cents, quantity) < 0:
                raise ValueError("nonnegative integer inputs required")
            return (unit_cents * quantity * percent + 50) // 100
        def quote_regular(unit_cents, quantity):
            return quote(unit_cents, quantity, 100)
        def quote_student(unit_cents, quantity):
            return quote(unit_cents, quantity, 90)
        def quote_senior(unit_cents, quantity):
            return quote(unit_cents, quantity, 80)
    ''',
}


def solve(workspace):
    for relative, content in SOLUTIONS.items():
        path = workspace / relative
        if path.exists():
            path.write_text(dedent(content).lstrip("\n"), encoding="utf-8")
