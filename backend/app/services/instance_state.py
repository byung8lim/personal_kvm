from typing import Dict, FrozenSet


class InvalidStateTransition(ValueError):
    pass


INSTANCE_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "CREATING": frozenset({"RUNNING", "STOPPED", "ERROR", "DELETING"}),
    "RUNNING": frozenset({"STOPPING", "ERROR"}),
    "STOPPING": frozenset({"STOPPED", "RUNNING", "ERROR"}),
    "STOPPED": frozenset({"STARTING", "RUNNING", "RESIZING", "DELETING", "ERROR"}),
    "STARTING": frozenset({"RUNNING", "STOPPED", "ERROR"}),
    "RESIZING": frozenset({"STOPPED", "ERROR"}),
    "DELETING": frozenset({"DELETED", "ERROR"}),
    "ERROR": frozenset({"STOPPED", "RUNNING", "DELETING"}),
    "DELETED": frozenset(),
}


def ensure_transition(current: str, target: str) -> None:
    if current == target:
        return
    allowed = INSTANCE_TRANSITIONS.get(current)
    if allowed is None:
        raise InvalidStateTransition(f"알 수 없는 인스턴스 상태입니다: {current}")
    if target not in allowed:
        raise InvalidStateTransition(
            f"허용되지 않은 인스턴스 상태 전이입니다: {current} -> {target}"
        )

