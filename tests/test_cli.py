import json
from pathlib import Path
import subprocess
import sys

from faultreplay import corridor
from faultreplay.cli import main


EXAMPLE = Path(corridor.__file__).parent / "examples" / "corridor"


def reduce_args(output, simulator, events=None):
    return [
        "reduce", "--events", str(events or EXAMPLE / "original.jsonl"),
        "--initial-state", str(EXAMPLE / "initial_state.json"),
        "--assertions", str(EXAMPLE / "assertions.json"),
        "--simulator", simulator, "--output", str(output),
    ]


def test_exported_pytest_reproduces_same_assertion_on_both_logs(tmp_path, capsys):
    output = tmp_path / "demo output"
    assert main(["demo", "--output", str(output)]) == 0
    assert "Single-deletion verified: yes" in capsys.readouterr().out
    assert {file.name for file in output.iterdir()} == {
        "original.jsonl", "reduced.jsonl", "initial_state.json", "assertions.json", "test_regression.py", "report.md",
    }
    reduced = [json.loads(line) for line in (output / "reduced.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["offset_ms"], r["payload"]["active"]) for r in reduced] == [(50, True), (150, False), (250, True)]
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "15 records, 13 inputs, 2 historical outputs" in report
    assert "Final verification" in report
    assert "[50, 250]" in report

    pytest_args = [sys.executable, "-m", "pytest", "-q", "test_regression.py"]
    fixed = subprocess.run(pytest_args, cwd=output, capture_output=True, text=True, timeout=30)
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    assert "2 passed" in fixed.stdout
    buggy = subprocess.run(
        pytest_args + ["-p", "faultreplay.pytest_plugin", "--faultreplay-simulator", "faultreplay.corridor:BUGGY"],
        cwd=output, capture_output=True, text=True, timeout=30,
    )
    assert buggy.returncode == 1, buggy.stdout + buggy.stderr
    assert "2 failed" in buggy.stdout
    assert "test_target_assertion[original]" in buggy.stdout
    assert "test_target_assertion[reduced]" in buggy.stdout
    expected = "AssertionError: corridor.turn_on_once | light.turn_on [0, 500) ms <= 1. Observed 2 at [50, 250] ms."
    assert buggy.stdout.count(expected) == 2, buggy.stdout


def test_invalid_last_input_is_reported_before_any_replay(tmp_path, capsys):
    events = tmp_path / "bad.jsonl"
    events.write_text(
        (EXAMPLE / "original.jsonl").read_text(encoding="utf-8")
        + '{"id":"bad","offset_ms":500,"type":"sensor.motion","payload":{"active":"yes"}}\n',
        encoding="utf-8",
    )
    output = tmp_path / "results"
    assert main(reduce_args(output, "faultreplay.corridor:BUGGY", events)) == 2
    assert "Undetermined" in capsys.readouterr().err
    assert set(p.name for p in output.iterdir()) == {"report.md"}
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "sensor.motion requires a boolean active field" in report
    assert "Replays — 0 / 100" in report


def test_stable_non_reproduction_exports_report_only(tmp_path):
    assert main(reduce_args(tmp_path, "faultreplay.corridor:FIXED")) == 1
    assert set(p.name for p in tmp_path.iterdir()) == {"report.md"}
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "baseline not reproduced" in report
    assert report.count("1 matching output(s) at `[50]` ms") == 2


def test_missing_cli_model_falls_back_and_reports_budget(tmp_path):
    args = reduce_args(tmp_path, "faultreplay.corridor:BUGGY")
    assert main(args + ["--model", "faultreplay.corridor:missing", "--max-replays", "8"]) == 0
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "budget exhausted" in report
    assert "Replays — 8 / 8" in report
    assert 'single `["motion-01"]`' in report
    assert "Single-deletion verified — no" in report
    assert "could not load" in report
