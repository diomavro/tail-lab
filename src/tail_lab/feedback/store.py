"""In-app feedback: a two-tier lifecycle store.

Two kinds of record, differing in **lifecycle**, not just tag:

- ``"big_picture"`` — a standing directive (goal / design direction). Never
  auto-resolved by any code path here; it stays open until Dio explicitly
  resolves it. The daily agent (``docs/AGENT_MISSION.md``) treats every
  open ``big_picture`` record as always-on context, read fresh every run.
- ``"issue"`` — transient (bug / small fix). The daily agent picks it up as
  concrete work and resolves it once addressed, at which point it drops out
  of :meth:`FeedbackStore.list_open`.

Records persist as individual JSON blobs via :class:`~tail_lab.lake.blob_store.BlobStore`,
rooted at the same local directory / Tigris bucket the medallion lakehouse
uses (``tail_lab.config.get_feedback_store``) — a `feedback/` layer sits
beside `ingestion/`/`transforms/` in the import-linter stack (peers, both
importing only `lake/` + `contracts/`) rather than folding this into
`lake/` itself, so the medallion abstraction (bronze/silver/gold, as-of
reads) stays about the research data pipeline and doesn't grow an
unrelated "ops metadata" concern (see ``docs/adr/0014``).

Resolving is idempotent: resolving an already-resolved record is a no-op
that returns the existing record unchanged, rather than bumping
``resolved_at`` again.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

from tail_lab.lake.blob_store import BlobStore

FeedbackKind = Literal["big_picture", "issue"]
FeedbackStatus = Literal["open", "resolved"]

#: The prefix every feedback blob is written under, relative to the lake
#: root — matches the shape described in the ADR: local dev gets
#: ``<lake_root>/ops/feedback/``, prod gets ``s3://<bucket>/ops/feedback/``.
_PREFIX = "ops/feedback"


class FeedbackRecord(BaseModel):
    id: str
    text: str
    kind: FeedbackKind
    created_at: dt.datetime
    status: FeedbackStatus
    resolved_at: dt.datetime | None = None


class FeedbackStore:
    """Add, list, and resolve feedback records on top of a :class:`BlobStore`."""

    def __init__(self, blob_store: BlobStore) -> None:
        self._blobs = blob_store

    def _key(self, record_id: str) -> str:
        return f"{_PREFIX}/{record_id}.json"

    def add(self, text: str, kind: FeedbackKind) -> FeedbackRecord:
        record = FeedbackRecord(
            id=str(uuid.uuid4()),
            text=text,
            kind=kind,
            created_at=dt.datetime.now(dt.UTC),
            status="open",
            resolved_at=None,
        )
        self._blobs.write_json(self._key(record.id), record.model_dump(mode="json"))
        return record

    def list_open(self) -> list[FeedbackRecord]:
        """Every open record, both kinds, oldest first."""
        records = [FeedbackRecord(**raw) for raw in self._blobs.list_json(_PREFIX)]
        open_records = [r for r in records if r.status == "open"]
        return sorted(open_records, key=lambda r: r.created_at)

    def resolve(self, record_id: str) -> FeedbackRecord:
        """Mark a record resolved. Raises ``LookupError`` if ``record_id``
        is unknown. A ``big_picture`` record is only ever resolved by this
        explicit call — no code path here resolves one automatically."""
        record = FeedbackRecord(**self._blobs.read_json(self._key(record_id)))
        if record.status == "resolved":
            return record
        resolved = record.model_copy(
            update={"status": "resolved", "resolved_at": dt.datetime.now(dt.UTC)}
        )
        self._blobs.write_json(self._key(resolved.id), resolved.model_dump(mode="json"))
        return resolved
