from __future__ import annotations

from collections.abc import Iterable

from release_proof.domain.models import EvidenceItem


class EvidenceConflictError(ValueError):
    pass


class EvidenceLedger:
    """Idempotent in-memory ledger keyed by stable evidence IDs."""

    def __init__(self, items: Iterable[EvidenceItem] = ()) -> None:
        self._items: dict[str, EvidenceItem] = {}
        for item in items:
            self.add(item)

    def add(self, item: EvidenceItem) -> bool:
        existing = self._items.get(item.id)
        if existing is None:
            self._items[item.id] = item
            return True
        if existing.content_hash != item.content_hash or existing.source_uri != item.source_uri:
            raise EvidenceConflictError(f"evidence ID {item.id!r} was reused for different content")
        return False

    def extend(self, items: Iterable[EvidenceItem]) -> int:
        return sum(1 for item in items if self.add(item))

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return self._items.get(evidence_id)

    def items(self) -> list[EvidenceItem]:
        return list(self._items.values())

    def refs(self):
        return [item.as_ref() for item in self._items.values()]

    def __len__(self) -> int:
        return len(self._items)

