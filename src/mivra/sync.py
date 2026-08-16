from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
import json
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class Operation:
    op_id: str
    entity: str
    entity_id: str
    kind: str
    payload: Dict[str, Any]
    base_version: int
    created_at: int

    @classmethod
    def build(
        cls,
        *,
        entity: str,
        entity_id: str,
        kind: str,
        payload: Dict[str, Any],
        base_version: int,
        created_at: int,
    ) -> "Operation":
        canonical = json.dumps(
            {
                "entity": entity,
                "entity_id": entity_id,
                "kind": kind,
                "payload": payload,
                "base_version": base_version,
                "created_at": created_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        op_id = sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return cls(
            op_id=op_id,
            entity=entity,
            entity_id=entity_id,
            kind=kind,
            payload=dict(payload),
            base_version=base_version,
            created_at=created_at,
        )


@dataclass
class SyncResult:
    applied: List[str]
    duplicate: List[str]
    conflicts: List[str]
    state: Dict[str, Dict[str, Dict[str, Any]]]


class OfflineSyncEngine:
    """Deterministic, dependency-free mobile offline reconciliation engine.

    The server state shape is::

        {entity: {entity_id: {"version": int, "data": {...}}}}

    Operations are idempotent by content-derived ``op_id``.  An operation is
    applied only when its ``base_version`` matches the current server version,
    making conflicts explicit instead of silently overwriting remote state.
    """

    VALID_KINDS = {"upsert", "delete"}

    def __init__(self, state: Optional[Dict[str, Any]] = None) -> None:
        self.state: Dict[str, Dict[str, Dict[str, Any]]] = json.loads(
            json.dumps(state or {})
        )
        self._seen: set[str] = set()

    def snapshot(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self.state, sort_keys=True))

    def _current(self, op: Operation) -> Dict[str, Any]:
        return self.state.get(op.entity, {}).get(
            op.entity_id,
            {"version": 0, "data": None},
        )

    def apply(self, operations: Iterable[Operation]) -> SyncResult:
        applied: List[str] = []
        duplicate: List[str] = []
        conflicts: List[str] = []

        # Stable ordering means clients can resend the same batch in any input
        # order and obtain the same reconciliation result.
        ordered = sorted(
            operations,
            key=lambda op: (op.created_at, op.op_id),
        )

        for op in ordered:
            if op.kind not in self.VALID_KINDS:
                raise ValueError(f"unsupported operation kind: {op.kind}")

            if op.op_id in self._seen:
                duplicate.append(op.op_id)
                continue

            current = self._current(op)
            current_version = int(current.get("version", 0))

            if op.base_version != current_version:
                conflicts.append(op.op_id)
                continue

            bucket = self.state.setdefault(op.entity, {})
            next_version = current_version + 1

            if op.kind == "delete":
                bucket[op.entity_id] = {
                    "version": next_version,
                    "data": None,
                    "deleted": True,
                }
            else:
                existing = current.get("data") or {}
                merged = dict(existing)
                merged.update(op.payload)
                bucket[op.entity_id] = {
                    "version": next_version,
                    "data": merged,
                    "deleted": False,
                }

            self._seen.add(op.op_id)
            applied.append(op.op_id)

        return SyncResult(
            applied=applied,
            duplicate=duplicate,
            conflicts=conflicts,
            state=self.snapshot(),
        )


def retry_delay_seconds(attempt: int, *, cap: int = 300) -> int:
    """Deterministic bounded exponential backoff for mobile sync retries."""
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    if cap < 1:
        raise ValueError("cap must be >= 1")
    return min(cap, 2 ** min(attempt, 16))


def export_operations(operations: Iterable[Operation]) -> str:
    return json.dumps(
        [asdict(op) for op in operations],
        sort_keys=True,
        separators=(",", ":"),
    )
