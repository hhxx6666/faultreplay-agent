from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .reduce import Check, Proposal, Reduction, observation_data
from .replay import Case, CountAssertion, Event, json_text


REGRESSION = '''from pathlib import Path

import pytest

from faultreplay.references import load_adapter
from faultreplay.replay import Case, Replay


HERE = Path(__file__).resolve().parent
DEFAULT_SIMULATOR = {adapter!r}


@pytest.mark.parametrize("log", ["original", "reduced"])
def test_target_assertion(log, request):
    reference = request.config.getoption("--faultreplay-simulator", default=None) or DEFAULT_SIMULATOR
    case = Case.load(HERE / f"{{log}}.jsonl", HERE / "initial_state.json", HERE / "assertions.json")
    observation = Replay(case, load_adapter(reference)).run()
    assert observation.error is None, f"{{log}} replay could not finish: {{observation.error}}"
    offsets = list(case.target.offsets(observation.outputs))
    assert case.target.id not in observation.failures, (
        f"{{case.target.describe()}}. Observed {{len(offsets)}} at {{offsets}} ms."
    )
'''


def events_jsonl(events: tuple[Event, ...]) -> str:
    lines = []
    for event in events:
        record = asdict(event)
        if event.direction == "input":
            del record["direction"]
        lines.append(json.dumps(record, allow_nan=False) + "\n")
    return "".join(lines)


def check_lines(label: str, check: Check, target: CountAssertion) -> list[str]:
    lines = [f"- {label} — **{check.verdict}**"]
    if check.reason:
        lines.append(f"  - {check.reason}")
    for index, run in enumerate(check.observations, start=1):
        data = observation_data(run, target)
        lines.append(f"  - Replay {index} — {data['target_count']} matching output(s) at "
                     f"`{data['target_offsets_ms']}` ms. Failed assertions `{json_text(data['failed_assertion_ids'])}`.")
        if run.error is not None:
            lines.append(f"    - Replay error `{json_text(run.error)}`. Output before interruption is partial.")
        if check.reason == "repeat observations differ":
            trace = [{"offset_ms": output.offset_ms, "type": output.type,
                      "payload": json.loads(output.payload_json)} for output in run.outputs]
            lines.append(f"    - Output trace `{json_text(trace)}`")
    return lines


def proposal_lines(proposals: tuple[Proposal, ...]) -> list[str]:
    return [f"- {p.source} `{json_text(p.remove_ids)}`" for p in proposals] or ["- None"]


def report(case: Case, result: Reduction, simulator: str) -> str:
    history = [event for event in case.events if event.direction == "output"]
    lines = [
        "# FaultReplay report", "",
        f"- Status — **{result.status}**",
        f"- Export — {'regression bundle' if result.exportable else 'report only'}",
        f"- Simulator — `{simulator}`",
        f"- Original log — {len(case.events)} records, {len(case.inputs)} inputs, {len(history)} historical outputs",
        f"- Shortest accepted input sequence — {len(result.best_events)} events" if result.exportable
        else f"- Input sequence under review — {len(result.best_events)} events",
        f"- Kept IDs — `{json_text([event.id for event in result.best_events])}`",
        f"- Target — `{case.target.describe()}`",
        f"- Replays — {result.runs} / {result.max_replays}",
        f"- Single-deletion verified — {'yes' if result.single_deletion_verified else 'no'}",
        "", "## Observations", "",
    ]
    lines.extend(check_lines("Original inputs", result.original, case.target))
    if result.best_events != case.inputs:
        lines.extend(check_lines("Shortest candidate when accepted", result.best_check, case.target))
    if result.final is not None:
        lines.extend(check_lines("Final verification", result.final, case.target))
    if history:
        lines.extend(["", "## Historical outputs from the log", ""])
        lines.extend(f"- `{json_text(event.id)}` `{json_text(event.type)}` at {event.offset_ms} ms" for event in history)
    lines.extend(["", "## Deletion attempts", ""])
    for attempt in result.attempts:
        proposal = attempt.proposal
        label = f"{proposal.source} `{json_text(proposal.remove_ids)}` from {attempt.remaining_before} inputs"
        lines.extend(check_lines(label, attempt.check, case.target))
    if not result.attempts:
        lines.append("- None")
    lines.extend(["", "## Unchecked deletions of the selected sequence", "", *proposal_lines(result.unchecked)])
    lines.extend(["", "## Inconclusive deletions of the selected sequence", "", *proposal_lines(result.inconclusive)])
    if result.model_fallbacks:
        lines.extend(["", "## Offline fallback", ""])
        lines.extend(f"- `{json_text(message)}`" for message in result.model_fallbacks)
    return "\n".join(lines) + "\n"


def write_report(output: Path, text: str, artifacts: dict[str, str] | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    files = {**(artifacts or {}), "report.md": text}
    with TemporaryDirectory(dir=output) as staging_path:
        staging = Path(staging_path)
        for name, content in files.items():
            (staging / name).write_text(content, encoding="utf-8", newline="\n")
        for name in files:
            (staging / name).replace(output / name)


def write_result(output: Path, case: Case, result: Reduction, simulator: str, regression_simulator: str) -> None:
    artifacts = None
    if result.exportable:
        assertions = {
            "target_assertion_id": case.target.id,
            "assertions": [{"id": a.id, "output_type": a.output_type,
                            "window_ms": [a.start_ms, a.end_ms], "max_count": a.max_count} for a in case.assertions],
        }
        artifacts = {
            "original.jsonl": events_jsonl(case.events),
            "reduced.jsonl": events_jsonl(result.best_events),
            "initial_state.json": json.dumps(case.initial_state, indent=2, allow_nan=False) + "\n",
            "assertions.json": json.dumps(assertions, indent=2) + "\n",
            "test_regression.py": REGRESSION.format(adapter=regression_simulator),
        }
    write_report(output, report(case, result, simulator), artifacts)
