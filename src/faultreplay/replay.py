from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable, Protocol


class InputError(ValueError):
    """A case or simulator input failed validation before replay."""


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)


def parse_json(text: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=unique_object)
        json_text(value)  # Reject NaN, Infinity and values that overflow to infinity.
        return value
    except (ValueError, RecursionError) as exc:
        raise InputError(str(exc)) from exc


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc


def read_json(path: Path) -> object:
    text = read_text(path)
    try:
        return parse_json(text)
    except InputError as exc:
        raise InputError(f"{path}: {exc}") from exc


def nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    offset_ms: int
    type: str
    payload: dict
    direction: str = "input"


def read_events(path: Path) -> tuple[Event, ...]:
    events = []
    seen_ids = set()
    previous_ms = 0
    required = {"id", "offset_ms", "type", "payload"}
    for line_number, line in enumerate(read_text(path).split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = parse_json(line)
            if not isinstance(record, dict) or not required <= record.keys() <= required | {"direction"}:
                raise InputError("expected id, offset_ms, type, payload and optional direction")
            event = Event(**record)
            if not nonempty_string(event.id) or event.id in seen_ids:
                raise InputError("event id must be a unique nonempty string")
            if not nonnegative_int(event.offset_ms) or event.offset_ms < previous_ms:
                raise InputError("offset_ms must be a nonnegative integer in chronological order")
            if not nonempty_string(event.type) or not isinstance(event.payload, dict):
                raise InputError("event type must be nonempty and payload must be a JSON object")
            if event.direction not in ("input", "output"):
                raise InputError("direction must be input or output")
        except InputError as exc:
            raise InputError(f"{path}:{line_number}: {exc}") from exc
        seen_ids.add(event.id)
        previous_ms = event.offset_ms
        events.append(event)
    return tuple(events)


@dataclass(frozen=True, slots=True)
class CountAssertion:
    id: str
    output_type: str
    start_ms: int
    end_ms: int
    max_count: int

    @classmethod
    def from_json(cls, record: object) -> CountAssertion:
        if not isinstance(record, dict) or record.keys() != {"id", "output_type", "window_ms", "max_count"}:
            raise InputError("expected assertion id, output_type, window_ms and max_count")
        identifier, output_type = record["id"], record["output_type"]
        window, maximum = record["window_ms"], record["max_count"]
        if not nonempty_string(identifier) or not nonempty_string(output_type):
            raise InputError("assertion id and output_type must be nonempty strings")
        if not isinstance(window, list) or len(window) != 2 or not all(map(nonnegative_int, window)):
            raise InputError("window_ms must contain two nonnegative integers")
        if window[0] >= window[1] or not nonnegative_int(maximum):
            raise InputError("window must have positive length and max_count must be nonnegative")
        return cls(identifier, output_type, *window, maximum)

    def describe(self) -> str:
        return f"{self.id} | {self.output_type} [{self.start_ms}, {self.end_ms}) ms <= {self.max_count}"

    def offsets(self, outputs: tuple[Output, ...]) -> tuple[int, ...]:
        return tuple(
            output.offset_ms for output in outputs
            if output.type == self.output_type and self.start_ms <= output.offset_ms < self.end_ms
        )


@dataclass(frozen=True, slots=True)
class Case:
    events: tuple[Event, ...]
    initial_state: dict
    assertions: tuple[CountAssertion, ...]
    target: CountAssertion

    @classmethod
    def load(cls, events_path: Path, initial_path: Path, assertions_path: Path) -> Case:
        events = read_events(events_path)
        initial_state = read_json(initial_path)
        if not isinstance(initial_state, dict):
            raise InputError("initial state must be a JSON object")
        spec = read_json(assertions_path)
        if not isinstance(spec, dict) or spec.keys() != {"target_assertion_id", "assertions"}:
            raise InputError("expected target_assertion_id and assertions")
        if not isinstance(spec["assertions"], list) or not nonempty_string(spec["target_assertion_id"]):
            raise InputError("expected an assertion list and a nonempty target ID")
        assertions = tuple(map(CountAssertion.from_json, spec["assertions"]))
        by_id = {assertion.id: assertion for assertion in assertions}
        target = by_id.get(spec["target_assertion_id"])
        if len(by_id) != len(assertions) or target is None:
            raise InputError("assertion IDs must be unique and the target must exist")
        return cls(events, initial_state, assertions, target)

    @property
    def inputs(self) -> tuple[Event, ...]:
        return tuple(event for event in self.events if event.direction == "input")


@dataclass(frozen=True, slots=True)
class Output:
    offset_ms: int
    type: str
    payload_json: str


@dataclass(slots=True)
class VirtualClock:
    _now_ms: int = field(init=False, default=0)
    _outputs: list[Output] = field(init=False, default_factory=list)

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def emit(self, output_type: str, payload: dict) -> None:
        if not nonempty_string(output_type) or not isinstance(payload, dict):
            raise ValueError("output type must be nonempty and payload must be a JSON object")
        self._outputs.append(Output(self.now_ms, output_type, json_text(payload)))


class Simulator(Protocol):
    def handle(self, event: Event, clock: VirtualClock) -> None: ...


@dataclass(frozen=True, slots=True)
class Adapter:
    create: Callable[[dict], Simulator]
    validate_initial: Callable[[dict], None] | None = None
    validate_event: Callable[[Event], None] | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    outputs: tuple[Output, ...]
    failures: tuple[str, ...] = ()
    error: str | None = None


class Replay:
    def __init__(self, case: Case, adapter: Adapter):
        self.case = deepcopy(case)
        self.inputs = self.case.inputs
        self._input_ids = frozenset(event.id for event in self.inputs)
        self.adapter = adapter
        self.runs = 0
        if adapter.validate_initial is not None:
            self._validate(adapter.validate_initial, self.case.initial_state, "initial state")
        if adapter.validate_event is not None:
            for event in self.inputs:
                self._validate(adapter.validate_event, event, f"event {event.id!r}")

    @staticmethod
    def _validate(validator: Callable, value: object, label: str) -> None:
        try:
            validator(deepcopy(value))
        except Exception as exc:
            raise InputError(f"{label}: {type(exc).__name__}: {exc}") from exc

    def run(self, keep_ids: frozenset[str] | None = None) -> Observation:
        if keep_ids is not None and not keep_ids <= self._input_ids:
            return Observation((), error="input selection contains unknown event IDs")
        self.runs += 1
        clock = VirtualClock()
        try:
            simulator = self.adapter.create(deepcopy(self.case.initial_state))
            for event in self.inputs:
                if keep_ids is None or event.id in keep_ids:
                    clock._now_ms = event.offset_ms
                    simulator.handle(deepcopy(event), clock)
        except Exception as exc:
            return Observation(tuple(clock._outputs), error=f"{type(exc).__name__}: {exc}")
        outputs = tuple(clock._outputs)
        failures = tuple(
            assertion.id for assertion in self.case.assertions
            if len(assertion.offsets(outputs)) > assertion.max_count
        )
        return Observation(outputs, failures)
