from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys

from .export import write_report, write_result
from .reduce import reduce_events
from .references import load_adapter, load_reference
from .replay import Case, InputError, Replay, json_text


def nonnegative_arg(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a nonnegative integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative integer")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="faultreplay", description="Reduce event logs into pytest regressions.")
    subcommands = root.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="run the offline corridor light example")
    reduce = subcommands.add_parser("reduce", help="reduce a JSONL case using a Python adapter")
    for subcommand in (demo, reduce):
        subcommand.add_argument("--output", type=Path, default=Path("faultreplay-output"))
        subcommand.add_argument("--max-replays", type=nonnegative_arg, default=100)
    reduce.add_argument("--events", type=Path, required=True)
    reduce.add_argument("--initial-state", type=Path, required=True)
    reduce.add_argument("--assertions", type=Path, required=True)
    reduce.add_argument("--simulator", required=True, help="module:adapter to reproduce the failure")
    reduce.add_argument("--regression-simulator", help="module:adapter used by the exported test by default")
    reduce.add_argument("--model", help="optional module:callable accepting system prompt and JSON message")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "demo":
        example = Path(__file__).resolve().parent / "examples" / "corridor"
        args.events = example / "original.jsonl"
        args.initial_state = example / "initial_state.json"
        args.assertions = example / "assertions.json"
        args.simulator = "faultreplay.corridor:BUGGY"
        args.regression_simulator = "faultreplay.corridor:FIXED"
        args.model = None

    regression_simulator = args.regression_simulator or args.simulator
    try:
        case = Case.load(args.events, args.initial_state, args.assertions)
        replay = Replay(case, load_adapter(args.simulator))
        if args.regression_simulator:
            load_adapter(regression_simulator)
    except InputError as exc:
        try:
            write_report(args.output, "# FaultReplay report\n\n- Status — **undetermined**\n"
                         f"- Input error — `{json_text(str(exc))}`\n- Replays — 0 / {args.max_replays}\n")
        except OSError as write_error:
            print(f"Could not write the report: {write_error}", file=sys.stderr)
        print(f"Undetermined: {exc}", file=sys.stderr)
        return 2

    model, load_error = None, None
    if args.model:
        try:
            model = load_reference(args.model)
            if not callable(model):
                raise InputError(f"{args.model!r} must refer to a callable")
        except InputError as exc:
            model, load_error = None, str(exc)
    result = reduce_events(replay, args.max_replays, model)
    if load_error is not None:
        result = replace(result, model_fallbacks=(load_error, *result.model_fallbacks))
    try:
        write_result(args.output, case, result, args.simulator, regression_simulator)
    except OSError as exc:
        print(f"Could not write results: {exc}", file=sys.stderr)
        return 2

    print(f"Original log: {len(case.events)} records ({len(replay.inputs)} replay inputs)")
    print(f"Target: {case.target.describe()}")
    print(f"Status: {result.status}; replays: {result.runs}/{result.max_replays}")
    print(f"Report: {(args.output / 'report.md').as_posix()}")
    if not result.exportable:
        return 1
    original_offsets = list(case.target.offsets(result.original.observations[0].outputs))
    selected = result.final or result.best_check
    selected_offsets = list(case.target.offsets(selected.observations[0].outputs))
    print(f"Reduced inputs: {len(result.best_events)} ({', '.join(event.id for event in result.best_events)})")
    print(f"Observed {case.target.output_type} at {original_offsets} ms (original) and {selected_offsets} ms (reduced)")
    print(f"Single-deletion verified: {'yes' if result.single_deletion_verified else 'no'}")
    test_path = (args.output / "test_regression.py").as_posix()
    command = f'python -m pytest -q "{test_path}"'
    print(f"Verify exported regression: {command}")
    if regression_simulator != args.simulator:
        print(f"Reproduce on failing simulator: {command} -p faultreplay.pytest_plugin --faultreplay-simulator {args.simulator}")
    return 0
