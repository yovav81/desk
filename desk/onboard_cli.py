"""CLI to validate the onboarding engine (desk/onboarding.py). No UI.

  python -m desk.onboard_cli suggest "<query>"
  python -m desk.onboard_cli resolve <market> <identifier> [--add]

`resolve` prints the ResolvedSecurity (or NotFound + reason) and only writes to
`securities` when --add is passed. `suggest` never writes.
"""
import json
import sys

from desk.onboarding import NotFound, add_to_db, resolve, suggest, to_dict


def _cmd_suggest(args: list[str]) -> int:
    if len(args) != 1:
        print('usage: python -m desk.onboard_cli suggest "<query>"', file=sys.stderr)
        return 2
    results = suggest(args[0])
    if not results:
        print(f'no suggestions for {args[0]!r}')
        return 0
    print(f'{len(results)} suggestion(s) for {args[0]!r}:')
    for s in results:
        print(f'  [{s.market:4}] {s.symbol_or_number:10} {s.display_name}  ({s.hint})')
    return 0


def _cmd_resolve(args: list[str]) -> int:
    add = "--add" in args
    rest = [a for a in args if a != "--add"]
    if len(rest) != 2:
        print("usage: python -m desk.onboard_cli resolve <market> <identifier> [--add]", file=sys.stderr)
        return 2
    market, identifier = rest
    result = resolve(market, identifier)
    if isinstance(result, NotFound):
        print(f"NotFound: {result.reason}")
        return 1
    print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))
    if add:
        outcome = add_to_db(result)
        print(f"add_to_db: {outcome}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m desk.onboard_cli {suggest|resolve} ...", file=sys.stderr)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "suggest":
        return _cmd_suggest(rest)
    if cmd == "resolve":
        return _cmd_resolve(rest)
    print(f"unknown command {cmd!r} (expected suggest|resolve)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
