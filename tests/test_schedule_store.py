"""ScheduleHint persistence tests (ticket 03)."""

from __future__ import annotations

from pathlib import Path

from x_schedule_monitor.models import ScheduleHint
from x_schedule_monitor.schedule_store import ScheduleHintStore


def _hint(
    post_id: str,
    member: str,
    date: str,
    planned: str,
    created: str,
    **kw,
) -> ScheduleHint:
    return ScheduleHint(
        source_post_id=post_id,
        source_post_created_at=created,
        schedule_date=date,
        member_key=member,
        member_name=member,
        planned_start_at=planned,
        fetched_at="2026-02-20T12:00:00Z",
        status="active",
        **kw,
    )


def test_dedupe_same_post(tmp_path: Path):
    store = ScheduleHintStore(tmp_path / "hints.json")
    h = _hint("10", "arale", "2026-02-21", "2026-02-21T13:00:00Z", "2026-02-20T10:00:00Z")
    assert store.upsert_hints([h]) == 1
    assert store.upsert_hints([h]) == 0
    assert len(store.active()) == 1


def test_supersede_same_date(tmp_path: Path):
    store = ScheduleHintStore(tmp_path / "hints.json")
    old = _hint("10", "arale", "2026-02-21", "2026-02-21T13:00:00Z", "2026-02-20T10:00:00Z")
    new = _hint("20", "arale", "2026-02-21", "2026-02-21T14:00:00Z", "2026-02-20T15:00:00Z")
    store.upsert_hints([old])
    store.upsert_hints([new])
    actives = store.active()
    assert len(actives) == 1
    assert actives[0].source_post_id == "20"
    superseded = [h for h in store.all() if h.status == "superseded"]
    assert len(superseded) == 1
    assert superseded[0].source_post_id == "10"


def test_persist_reload(tmp_path: Path):
    path = tmp_path / "hints.json"
    store = ScheduleHintStore(path)
    store.upsert_hints(
        [
            _hint(
                "10",
                "miyako",
                "2026-02-21",
                "2026-02-21T13:30:00Z",
                "2026-02-20T10:00:00Z",
                youtube_video_id="abc",
            )
        ]
    )
    store.save()
    store2 = ScheduleHintStore(path)
    assert len(store2.active()) == 1
    assert store2.active()[0].youtube_video_id == "abc"


def test_damaged_file_safe(tmp_path: Path):
    path = tmp_path / "hints.json"
    path.write_text("{not json", encoding="utf-8")
    store = ScheduleHintStore(path)
    assert store.all() == []
    # original left in place
    assert path.read_text(encoding="utf-8") == "{not json"


def test_expire_before(tmp_path: Path):
    store = ScheduleHintStore(tmp_path / "hints.json")
    store.upsert_hints(
        [
            _hint("1", "arale", "2026-02-20", "2026-02-20T13:00:00Z", "2026-02-19T10:00:00Z"),
            _hint("2", "arale", "2026-02-21", "2026-02-21T13:00:00Z", "2026-02-20T10:00:00Z"),
        ]
    )
    n = store.expire_before("2026-02-21")
    assert n == 1
    assert len(store.active()) == 1
    assert store.active()[0].schedule_date == "2026-02-21"
