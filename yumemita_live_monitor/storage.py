"""CSV storage for streams metadata and viewer samples."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import StreamRecord, ViewerSample
from .utils import ensure_dir, parse_iso

logger = logging.getLogger("yumemita_live_monitor.storage")

STREAMS_FIELDS = [
    "video_id",
    "channel_id",
    "member_key",
    "member_name",
    "title",
    "status",
    "scheduled_start_at",
    "actual_start_at",
    "actual_end_at",
    "discovered_at",
    "last_seen_at",
    "peak_concurrent_viewers",
]

SAMPLE_FIELDS = [
    "sampled_at",
    "video_id",
    "channel_id",
    "member_key",
    "member_name",
    "concurrent_viewers",
]


class StreamsStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        ensure_dir(self.path.parent)
        self._records: Dict[str, StreamRecord] = {}
        self.load()

    def load(self) -> Dict[str, StreamRecord]:
        self._records = {}
        if not self.path.exists():
            return self._records
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row or not (row.get("video_id") or "").strip():
                    continue
                rec = StreamRecord.from_csv_row(row)
                self._records[rec.video_id] = rec
        logger.info("Loaded %d streams from %s", len(self._records), self.path)
        return self._records

    @property
    def records(self) -> Dict[str, StreamRecord]:
        return self._records

    def get(self, video_id: str) -> Optional[StreamRecord]:
        return self._records.get(video_id)

    def upsert(self, rec: StreamRecord) -> StreamRecord:
        existing = self._records.get(rec.video_id)
        if existing is None:
            self._records[rec.video_id] = rec
            return rec
        if not existing.discovered_at and rec.discovered_at:
            existing.discovered_at = rec.discovered_at
        if rec.title:
            existing.title = rec.title
        if rec.status:
            existing.status = rec.status
        if rec.scheduled_start_at:
            existing.scheduled_start_at = rec.scheduled_start_at
        if rec.actual_start_at:
            existing.actual_start_at = rec.actual_start_at
        if rec.actual_end_at:
            existing.actual_end_at = rec.actual_end_at
        if rec.last_seen_at:
            existing.last_seen_at = rec.last_seen_at
        if rec.member_key:
            existing.member_key = rec.member_key
        if rec.member_name:
            existing.member_name = rec.member_name
        if rec.channel_id:
            existing.channel_id = rec.channel_id
        if rec.peak_concurrent_viewers > existing.peak_concurrent_viewers:
            existing.peak_concurrent_viewers = rec.peak_concurrent_viewers
        return existing

    def update_peak(self, video_id: str, peak: int) -> None:
        rec = self._records.get(video_id)
        if rec and peak > rec.peak_concurrent_viewers:
            rec.peak_concurrent_viewers = peak

    def save(self) -> None:
        ensure_dir(self.path.parent)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=STREAMS_FIELDS, lineterminator="\n")
                writer.writeheader()
                for vid in sorted(self._records.keys()):
                    writer.writerow(self._records[vid].to_csv_row())
                f.flush()
            tmp.replace(self.path)
        except Exception:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise

    def all(self) -> List[StreamRecord]:
        return list(self._records.values())


class SampleStore:
    def __init__(self, samples_dir: str | Path):
        self.samples_dir = Path(samples_dir)
        ensure_dir(self.samples_dir)
        self._open_month: Optional[str] = None
        self._file = None
        self._writer: Optional[csv.DictWriter] = None

    def _month_key(self, sampled_at: str) -> str:
        dt = parse_iso(sampled_at)
        if dt is None:
            return sampled_at[:7] if len(sampled_at) >= 7 else "unknown"
        return dt.strftime("%Y-%m")

    def _ensure_writer(self, month_key: str) -> None:
        if self._open_month == month_key and self._writer is not None:
            return
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
        path = self.samples_dir / f"samples_{month_key}.csv"
        new_file = not path.exists()
        self._file = path.open("a", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(
            self._file, fieldnames=SAMPLE_FIELDS, lineterminator="\n"
        )
        if new_file:
            self._writer.writeheader()
        self._open_month = month_key

    def append(self, sample: ViewerSample) -> None:
        month = self._month_key(sample.sampled_at)
        self._ensure_writer(month)
        assert self._writer is not None and self._file is not None
        self._writer.writerow(sample.to_csv_row())
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
            self._writer = None
            self._open_month = None

    def _path_for_month(self, month: str) -> Path:
        # Prefer samples_YYYY-MM.csv; also accept YYYY-MM.csv for flexibility
        p = self.samples_dir / f"samples_{month}.csv"
        if p.exists():
            return p
        alt = self.samples_dir / f"{month}.csv"
        return alt if alt.exists() else p

    def read_months(self, months: Iterable[str]) -> List[ViewerSample]:
        samples: List[ViewerSample] = []
        for month in months:
            path = self._path_for_month(month)
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if not row or not (row.get("video_id") or "").strip():
                        continue
                    try:
                        viewers = int(row.get("concurrent_viewers") or 0)
                    except (TypeError, ValueError):
                        continue
                    samples.append(
                        ViewerSample(
                            sampled_at=(row.get("sampled_at") or "").strip(),
                            video_id=(row.get("video_id") or "").strip(),
                            channel_id=(row.get("channel_id") or "").strip(),
                            member_key=(row.get("member_key") or "").strip(),
                            member_name=(row.get("member_name") or "").strip(),
                            concurrent_viewers=viewers,
                        )
                    )
        return samples

    def list_available_months(self) -> List[str]:
        months: List[str] = []
        if not self.samples_dir.exists():
            return months
        for p in self.samples_dir.glob("samples_????-??.csv"):
            months.append(p.stem.replace("samples_", "", 1))
        for p in self.samples_dir.glob("????-??.csv"):
            if p.stem not in months:
                months.append(p.stem)
        return sorted(months)
