from __future__ import annotations

from .replay import Adapter, Event, InputError, VirtualClock


def validate_initial(state: dict) -> None:
    if state.keys() != {"motion"} or type(state["motion"]) is not bool:
        raise InputError("expected a boolean motion field in the initial state")


def validate_event(event: Event) -> None:
    if event.type == "sensor.motion":
        if event.payload.keys() != {"active"} or type(event.payload["active"]) is not bool:
            raise InputError("sensor.motion requires a boolean active field")
    elif event.type in {"sensor.temperature", "sensor.humidity"}:
        if event.payload.keys() != {"value"} or type(event.payload["value"]) not in (int, float):
            raise InputError(f"{event.type} requires a numeric value field")
    else:
        raise InputError(f"unknown corridor input type {event.type!r}")


class Corridor:
    def __init__(self, initial_state: dict, cooldown_ms: int):
        self.motion = initial_state["motion"]
        self.cooldown_ms = cooldown_ms
        self.last_turn_on_ms: int | None = None

    def handle(self, event: Event, clock: VirtualClock) -> None:
        if event.type != "sensor.motion":
            return
        active = event.payload["active"]
        entering = active and not self.motion
        self.motion = active
        if not entering:
            return
        if self.last_turn_on_ms is not None and clock.now_ms - self.last_turn_on_ms < self.cooldown_ms:
            return
        clock.emit("light.turn_on", {"light": "corridor"})
        self.last_turn_on_ms = clock.now_ms


BUGGY = Adapter(lambda state: Corridor(state, cooldown_ms=0), validate_initial, validate_event)
FIXED = Adapter(lambda state: Corridor(state, cooldown_ms=500), validate_initial, validate_event)
