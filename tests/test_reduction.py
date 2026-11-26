from dataclasses import replace
import json
from pathlib import Path

import pytest

from faultreplay import corridor
from faultreplay.reduce import Verdict, reduce_events
from faultreplay.replay import Adapter, Case, CountAssertion, Event, Replay


EXAMPLE = Path(corridor.__file__).parent / "examples" / "corridor"
MOTION = ("motion-01", "motion-02", "motion-03")


@pytest.fixture
def corridor_case():
    return Case.load(EXAMPLE / "original.jsonl", EXAMPLE / "initial_state.json", EXAMPLE / "assertions.json")


def test_reduces_to_three_motion_inputs_and_rechecks(corridor_case):
    replay = Replay(corridor_case, corridor.BUGGY)
    result = reduce_events(replay)
    assert result.status == "complete"
    assert result.single_deletion_verified
    assert result.unchecked == result.inconclusive == ()
    assert len(corridor_case.events) == 15
    assert len(corridor_case.inputs) == 13
    assert tuple(e.id for e in result.best_events) == MOTION
    assert [e.offset_ms for e in result.best_events] == [50, 150, 250]
    assert all(event == next(e for e in corridor_case.inputs if e.id == event.id) for event in result.best_events)
    assert result.final == result.best_check
    assert result.runs == replay.runs == 18
    assert corridor_case.target.offsets(result.final.observations[0].outputs) == (50, 250)
    fixed = Replay(corridor_case, corridor.FIXED)
    for ids in (None, frozenset(MOTION)):
        run = fixed.run(ids)
        assert run.error is None
        assert not run.failures
        assert corridor_case.target.offsets(run.outputs) == (50,)


def test_baseline_that_passes_stops_without_asking_model(corridor_case):
    def model(*args):
        pytest.fail("model called before reproducing the baseline")

    result = reduce_events(Replay(corridor_case, corridor.FIXED), model=model)
    assert result.status == "baseline not reproduced"
    assert result.original.verdict == Verdict.PASS
    assert result.runs == 2
    assert result.attempts == ()
    assert not result.exportable


def test_other_assertion_alone_never_qualifies():
    target = CountAssertion("target", "target.output", 0, 100, 1)
    other = CountAssertion("other", "other.output", 0, 100, 0)
    events = (Event("t1", 0, "target.output", {}), Event("t2", 1, "target.output", {}),
              Event("o1", 2, "other.output", {}))
    case = Case(events, {}, (target, other), target)

    class EmitsInputType:
        def handle(self, event, clock):
            clock.emit(event.type, {})

    result = reduce_events(Replay(case, Adapter(lambda state: EmitsInputType())))
    first = result.attempts[0]
    assert first.proposal.remove_ids == ("t1", "t2")
    assert first.check.verdict == Verdict.OTHER_FAILURE
    assert first.check.observations[0].failures == ("other",)
    assert tuple(e.id for e in result.best_events) == ("t1", "t2")


def test_exception_during_candidate_does_not_certify_single_deletions():
    target = CountAssertion("target", "output", 0, 100, 1)
    events = (Event("guard", 0, "guard", {}), Event("first", 1, "trigger", {}), Event("second", 2, "trigger", {}))
    case = Case(events, {"count": 0}, (target,), target)

    class RequiresGuard:
        def __init__(self, state):
            self.state = state

        def handle(self, event, clock):
            if event.type == "guard":
                self.state["guard"] = True
                return
            self.state["count"] += 1
            clock.emit("output", {})
            if self.state["count"] == 2 and not self.state.get("guard"):
                raise RuntimeError("guard disappeared")

    replay = Replay(case, Adapter(RequiresGuard))
    result = reduce_events(replay)
    crash = next(a for a in result.attempts if a.proposal.remove_ids == ("guard",))
    assert crash.check.verdict == Verdict.UNDETERMINED
    assert all(run.error == "RuntimeError: guard disappeared" for run in crash.check.observations)
    assert [p.remove_ids for p in result.inconclusive] == [("guard",)]
    assert all(len(run.outputs) == 2 for run in crash.check.observations)
    assert result.best_events == events
    assert result.unchecked == ()
    assert not result.single_deletion_verified
    assert replay.case.initial_state == {"count": 0}


@pytest.mark.parametrize("failure", ["exception", "unstable traces"])
def test_undetermined_baseline_records_observations_and_stops(corridor_case, failure):
    executions = 0

    class InstrumentedCorridor(corridor.Corridor):
        def handle(self, event, clock):
            super().handle(event, clock)
            if event.id == "motion-03":
                if failure == "exception":
                    raise RuntimeError("simulator failed")
                clock.emit("debug", {"run": executions})

    def create(state):
        nonlocal executions
        executions += 1
        return InstrumentedCorridor(state, cooldown_ms=0)

    result = reduce_events(Replay(corridor_case, replace(corridor.BUGGY, create=create)))
    assert result.original.verdict == Verdict.UNDETERMINED
    assert len(result.original.observations) == result.runs == executions == 2
    assert not result.attempts
    assert not result.exportable


@pytest.mark.parametrize("budget, used, events, exportable", [
    (0, 0, 13, False), (1, 1, 13, False), (2, 2, 13, True), (5, 4, 13, True),
    (6, 6, 8, True), (8, 8, 3, True), (17, 16, 3, True),
])
def test_replay_budget_leaves_exact_pending_checks(corridor_case, budget, used, events, exportable):
    result = reduce_events(Replay(corridor_case, corridor.BUGGY), max_replays=budget)
    assert result.runs == used
    assert len(result.best_events) == events
    assert result.exportable is exportable
    assert result.unchecked
    assert not result.single_deletion_verified
    if budget >= 6:
        assert result.final == result.best_check
    if budget == 17:
        assert [p.remove_ids for p in result.unchecked] == [("motion-03",)]


def test_changed_final_trace_blocks_export(corridor_case):
    executions = 0

    class InstrumentedCorridor(corridor.Corridor):
        def handle(self, event, clock):
            super().handle(event, clock)
            if executions >= 5 and event.id == "motion-03":
                clock.emit("debug", {})

    def create(state):
        nonlocal executions
        executions += 1
        return InstrumentedCorridor(state, cooldown_ms=0)

    result = reduce_events(Replay(corridor_case, replace(corridor.BUGGY, create=create)), max_replays=6)
    assert result.status == "final replay not reproduced"
    assert result.best_check.verdict == Verdict.TARGET_FAILURE
    assert result.final.verdict == Verdict.UNDETERMINED
    assert result.final.reason == "repeat observations differ"
    assert not result.exportable
    assert result.runs == executions == 6


def test_same_candidate_must_agree_with_previous_rounds():
    target = CountAssertion("target", "output", 0, 100, 0)
    events = tuple(Event(identifier, time, "input", {}) for time, identifier in enumerate("abc"))
    executions = 0

    class ChangingSimulator:
        def __init__(self, state):
            nonlocal executions
            executions += 1
            self.saw_b = False

        def handle(self, event, clock):
            if event.id == "b":
                self.saw_b = True
                clock.emit("output", {})
            if event.id == "c" and not self.saw_b and executions >= 7:
                clock.emit("output", {})

    replies = iter(['{"remove_ids":["a","b"]}', '{"remove_ids":["a"]}', '{"remove_ids":["b"]}'])
    result = reduce_events(Replay(Case(events, {}, (target,), target), Adapter(ChangingSimulator)),
                           max_replays=10, model=lambda system, message: next(replies))
    assert [attempt.check.verdict for attempt in result.attempts] == [
        Verdict.PASS, Verdict.TARGET_FAILURE, Verdict.UNDETERMINED,
    ]
    assert [event.id for event in result.best_events] == ["b", "c"]
    assert result.final == result.best_check


def test_model_reads_feedback_then_falls_back_offline(corridor_case):
    requests = []

    def model(system, message):
        request = json.loads(message)
        requests.append((system, request))
        if len(requests) == 1:
            return '{"remove_ids":["motion-02"]}'
        if len(requests) == 2:
            noise = [e["id"] for e in request["events"] if e["type"] != "sensor.motion"]
            return json.dumps({"remove_ids": noise})
        if len(requests) == 3:
            return '{"remove_ids":["motion-01"],"offset_ms":0}'
        if len(requests) == 4:
            raise TimeoutError("model offline")
        if len(requests) == 5:
            return '{"remove_ids":["temp-01"]}'
        return '{"remove_ids":[]}'

    result = reduce_events(Replay(corridor_case, corridor.BUGGY), model=model)
    system, first = requests[0]
    assert "sole key remove_ids" in system
    assert first["target"]["id"] == corridor_case.target.id
    assert len(first["events"]) == 13
    assert first["events"][2]["id"] == "motion-01"
    assert first["events"][2]["offset_ms"] == 50
    assert first["recent_replays"] == []
    feedback = requests[1][1]["recent_replays"][-1]
    assert feedback["verdict"] == "pass"
    assert feedback["remove_ids"] == ["motion-02"]
    assert [o["target_count"] for o in feedback["observations"]] == [1, 1]
    assert result.attempts[1].proposal.source == "model"
    assert result.attempts[1].check.verdict == Verdict.TARGET_FAILURE
    assert [e["id"] for e in requests[2][1]["events"]] == list(MOTION)
    assert result.attempts[2].proposal.source == "category"
    assert [f.split(":", 1)[0] for f in result.model_fallbacks[:3]] == ["InputError", "TimeoutError", "InputError"]
    assert tuple(e.id for e in result.best_events) == MOTION
    assert result.single_deletion_verified
