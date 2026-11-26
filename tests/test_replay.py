import json

import pytest

from faultreplay.corridor import BUGGY, FIXED
from faultreplay.replay import Case, InputError, Replay, read_events


@pytest.fixture
def make_case(tmp_path):
    initial_path = tmp_path / "initial.json"
    initial_path.write_text('{"motion":false}', encoding="utf-8")
    assertions_path = tmp_path / "assertions.json"
    assertions_path.write_text(json.dumps({
        "target_assertion_id": "once",
        "assertions": [{"id": "once", "window_ms": [0, 500], "max_count": 1, "output_type": "light.turn_on"}],
    }), encoding="utf-8")
    events_path = tmp_path / "events.jsonl"

    def write(events):
        events_path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
        return Case.load(events_path, initial_path, assertions_path)

    return write


def motion(identifier, time, active):
    return {"id": identifier, "offset_ms": time, "type": "sensor.motion", "payload": {"active": active}}


def test_same_time_uses_file_order_and_does_not_rebase_time(make_case):
    case = make_case([motion("z", 50, True), motion("a", 50, False), motion("m", 50, True)])
    replay = Replay(case, BUGGY)
    original = replay.run()
    reduced = replay.run(frozenset({"m", "z"}))
    assert case.target.offsets(original.outputs) == (50, 50)
    assert original.failures == ("once",)
    assert case.target.offsets(reduced.outputs) == (50,)
    assert not reduced.failures


def test_window_excludes_end_and_history_is_never_injected(make_case):
    case = make_case([
        motion("first", 0, True), motion("clear", 100, False), motion("next", 500, True),
        {"id": "past", "offset_ms": 500, "type": "light.turn_on", "payload": {}, "direction": "output"},
    ])
    for adapter in (BUGGY, FIXED):
        run = Replay(case, adapter).run()
        assert tuple(o.offset_ms for o in run.outputs) == (0, 500)
        assert case.target.offsets(run.outputs) == (0,)
        assert not run.failures


@pytest.mark.parametrize("records, message", [
    ([motion("first", 20, True), motion("second", 19, False)], "chronological"),
    ([motion("first", 20, True), motion("first", 20, False)], "unique"),
    ([motion("first", True, True)], "integer"),
    ([{**motion("first", 0, True), "direction": "history"}], "direction"),
    ([{**motion("first", 0, True), "payload": {"bad": float("nan")}}], "Out of range"),
])
def test_log_validation_identifies_line(tmp_path, records, message):
    path = tmp_path / "invalid.jsonl"
    path.write_text("\n".join(map(json.dumps, records)), encoding="utf-8")
    with pytest.raises(InputError, match=rf"invalid\.jsonl:{len(records)}: .*{message}"):
        read_events(path)


def test_invalid_selection_is_undetermined_without_using_budget(make_case):
    replay = Replay(make_case([motion("first", 50, True)]), BUGGY)
    run = replay.run(frozenset({"unknown"}))
    assert run.error == "input selection contains unknown event IDs"
    assert replay.runs == 0
