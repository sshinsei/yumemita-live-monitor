"""Parse X schedule posts into ScheduleHint records."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .models import MEMBER_NAME_ALIASES, ScheduleHint
from .utils import format_utc, parse_iso

logger = logging.getLogger("yumemita_live_monitor.schedule_parser")

_FW_TRANS = str.maketrans(
    {
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "：": ":",
        "〜": "~",
        "～": "~",
        "－": "-",
        "—": "-",
        "　": " ",
    }
)

WEEKDAY_JP = {
    "月": 0,
    "火": 1,
    "水": 2,
    "木": 3,
    "金": 4,
    "土": 5,
    "日": 6,
}

DATE_RE = re.compile(
    r"(?P<m>\d{1,2})\s*/\s*(?P<d>\d{1,2})"
    r"(?:\s*[\(（]\s*(?P<wd>[月火水木金土日])\s*[\)）])?"
)

# After emoji / メン限 / 明日 stripping: optional junk, then H:MM, then member body
MEMBER_LINE_RE = re.compile(
    r"^[^0-9]*?"
    r"(?P<h>\d{1,2})\s*:\s*(?P<min>\d{2})\s*[~-]?\s*"
    r"(?P<body>.+)$"
)

# Always scanned so the marker can be stripped for time/member matching
MEMBER_ONLY_RE = re.compile(
    r"(?:【\s*)?(?:メンバー限定|メン限)(?:\s*】)?"
    r"|〖\s*(?:メンバー限定|メン限)\s*〗"
    r"|\[\s*(?:メンバー限定|メン限)\s*\]"
)

TOMORROW_RE = re.compile(r"明日")
# "朝" immediately before a clock time — noise for parsing, not semantic
ASA_BEFORE_TIME_RE = re.compile(r"朝(?=\s*\d{1,2}\s*:)")

# Broad emoji / symbol strip (decorative only; keeps CJK, ASCII, common punct)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # pictographs, supplemental
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F000-\U0001F02F"
    "\U0000200D"  # ZWJ
    "\U0000203C-\U00003299"  # some enclosed / misc (narrow: careful)
    "]+",
    flags=re.UNICODE,
)
# Safer second pass: So/Sk categories outside BMP-ish presentation
_EMOJI_CHAR_RE = re.compile(
    r"[\U0001F000-\U0001FFFF"
    r"\u2600-\u27BF"
    r"\uFE0F"
    r"\u200D"
    r"\u23E9-\u23F3"
    r"\u23F8-\u23FA"
    r"\u25AA-\u25FE"
    r"\u2B05-\u2B55"
    r"]+"
)

YT_VIDEO_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?v=|live/)|youtu\.be/)"
    r"(?P<id>[A-Za-z0-9_-]{11})"
)
YT_CHANNEL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?youtube\.com/(?:@[\w.-]+|channel/UC[\w-]+)",
    re.IGNORECASE,
)

SCHEDULE_MARKERS = ("#夢限大みゅーたいぷ", "夢限大みゅーたいぷ", "配信スケジュール")


@dataclass
class ParseWarning:
    message: str
    raw_line: str = ""


@dataclass
class ParseResult:
    is_schedule_post: bool
    schedule_date: str = ""  # YYYY-MM-DD local calendar (post header date)
    hints: List[ScheduleHint] = field(default_factory=list)
    warnings: List[ParseWarning] = field(default_factory=list)


def normalize_text(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    return t.translate(_FW_TRANS)


def strip_emojis(text: str) -> str:
    """Remove decorative emoji/symbols for parsing only; original kept in raw_text."""
    t = _EMOJI_CHAR_RE.sub("", text or "")
    # collapse leftover spaces
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t


def is_schedule_post(text: str) -> bool:
    n = normalize_text(text)
    has_marker = any(m in text or m in n for m in SCHEDULE_MARKERS)
    has_date = bool(DATE_RE.search(n))
    return has_marker and has_date


def extract_video_id(url: str) -> str:
    if not url:
        return ""
    m = YT_VIDEO_RE.search(url)
    if m:
        return m.group("id")
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        host = (parsed.netloc or "").lower()
        if "youtu.be" in host:
            part = (parsed.path or "").strip("/").split("/")[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", part):
                return part
        if "youtube.com" in host:
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                v = qs["v"][0]
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", v):
                    return v
            path = parsed.path or ""
            live_m = re.search(r"/live/([A-Za-z0-9_-]{11})", path)
            if live_m:
                return live_m.group(1)
    except Exception:
        return ""
    return ""


def _resolve_year(month: int, day: int, post_created: datetime, tz: ZoneInfo) -> int:
    local = post_created.astimezone(tz)
    year = local.year
    try:
        candidate = datetime(year, month, day, tzinfo=tz)
    except ValueError:
        return year
    if (local.date() - candidate.date()).days > 60:
        return year + 1
    return year


def parse_schedule_date(
    text: str,
    post_created_at: datetime,
    *,
    tz_name: str = "Asia/Tokyo",
) -> Tuple[Optional[str], List[ParseWarning]]:
    warnings: List[ParseWarning] = []
    n = normalize_text(text)
    m = DATE_RE.search(n)
    if not m:
        return None, warnings
    month = int(m.group("m"))
    day = int(m.group("d"))
    wd = m.group("wd")
    tz = ZoneInfo(tz_name)
    if post_created_at.tzinfo is None:
        post_created_at = post_created_at.replace(tzinfo=ZoneInfo("UTC"))
    year = _resolve_year(month, day, post_created_at, tz)
    try:
        local_date = datetime(year, month, day, tzinfo=tz).date()
    except ValueError:
        warnings.append(ParseWarning(f"invalid date {year}-{month}-{day}"))
        return None, warnings
    if wd and wd in WEEKDAY_JP:
        expected = WEEKDAY_JP[wd]
        if local_date.weekday() != expected:
            warnings.append(
                ParseWarning(
                    f"weekday mismatch: post says {wd} but {local_date.isoformat()} "
                    f"is weekday {local_date.weekday()}"
                )
            )
    return local_date.isoformat(), warnings


def _scan_member_only(text: str) -> Tuple[bool, str]:
    """Always scan メン限 markers; return (found, text_with_markers_removed)."""
    found = bool(MEMBER_ONLY_RE.search(text))
    cleaned = MEMBER_ONLY_RE.sub(" ", text)
    cleaned = re.sub(r"[〖】\[\]]", " ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return found, cleaned


def _prepare_member_line(line: str) -> Tuple[str, bool, bool]:
    """
    Prepare a schedule line for H:MM matching.
    Returns (prepared_line, member_only_marker_found, is_tomorrow).
    Emoji stripped; メン限 markers stripped (always); 明日/朝 handled by rules.
    """
    s = normalize_text(line)
    s = strip_emojis(s)
    is_tomorrow = bool(TOMORROW_RE.search(s))
    s = TOMORROW_RE.sub(" ", s)
    s = ASA_BEFORE_TIME_RE.sub(" ", s)
    marker_found, s = _scan_member_only(s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s, marker_found, is_tomorrow


def _find_url_near(
    lines: Sequence[str],
    index: int,
    expanded_urls: Sequence[str],
) -> Tuple[str, str]:
    candidates: List[str] = []
    candidates.extend(expanded_urls)
    for j in range(index, min(index + 3, len(lines))):
        candidates.append(lines[j].strip())
    for c in candidates:
        vid = extract_video_id(c)
        if vid:
            m = YT_VIDEO_RE.search(c)
            url = m.group(0) if m else c
            if not url.startswith("http"):
                url = "https://" + url.lstrip("/")
            return url, vid
        if YT_CHANNEL_RE.search(c):
            m = YT_CHANNEL_RE.search(c)
            url = m.group(0) if m else c
            if not url.startswith("http"):
                url = "https://" + url.lstrip("/")
            return url, ""
    return "", ""


def parse_schedule_post(
    text: str,
    *,
    source_post_id: str,
    source_post_created_at: str,
    fetched_at: str,
    expanded_urls: Optional[Sequence[str]] = None,
    edit_history_tweet_ids: Optional[Sequence[str]] = None,
    tz_name: str = "Asia/Tokyo",
    aliases: Optional[Dict[str, str]] = None,
    member_only_enabled: bool = False,
) -> ParseResult:
    """
    Parse a single X post body into ScheduleHints.

    member_only_enabled:
      True  — 检出メン限时写入 ScheduleHint.member_only=True（元数据/日志用）
      False — 仍扫描并剥掉メン限字符以便解析时间/成员，但不打标、按普通 hint 处理
    """
    aliases = aliases or MEMBER_NAME_ALIASES
    expanded_urls = list(expanded_urls or [])
    edit_ids = list(edit_history_tweet_ids or [])

    if not is_schedule_post(text):
        return ParseResult(is_schedule_post=False)

    post_dt = parse_iso(source_post_created_at)
    if post_dt is None:
        post_dt = datetime.now(ZoneInfo("UTC"))

    schedule_date, date_warnings = parse_schedule_date(text, post_dt, tz_name=tz_name)
    warnings = list(date_warnings)
    if not schedule_date:
        warnings.append(ParseWarning("could not parse schedule date"))
        return ParseResult(
            is_schedule_post=True,
            schedule_date="",
            hints=[],
            warnings=warnings,
        )

    lines = text.splitlines()
    # URL-adjacent lines: normalize only (keep structure for URL detection)
    norm_lines = [normalize_text(ln) for ln in lines]
    tz = ZoneInfo(tz_name)
    base_date = datetime.strptime(schedule_date, "%Y-%m-%d").date()

    hints: List[ScheduleHint] = []
    for i, original_line in enumerate(lines):
        prepared, marker_found, is_tomorrow = _prepare_member_line(original_line)
        if not prepared or prepared.startswith("※"):
            continue

        m = MEMBER_LINE_RE.match(prepared)
        if not m:
            continue

        hour = int(m.group("h"))
        minute = int(m.group("min"))
        body = m.group("body").strip()
        if hour > 23 or minute > 59:
            warnings.append(
                ParseWarning("invalid time", raw_line=original_line.strip())
            )
            continue

        # member_only flag only when switch is on
        if marker_found and not member_only_enabled:
            logger.info(
                "X schedule member_only marker ignored (switch off): %s",
                original_line.strip()[:60],
            )
        member_only = bool(marker_found and member_only_enabled)

        # body may still contain leftover brackets
        cleaned = re.sub(r"[〖】\[\]【】()（）]", " ", body).strip()
        # also strip any residual marker text
        cleaned = MEMBER_ONLY_RE.sub(" ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        member_key = ""
        member_name = ""
        for name in sorted(aliases.keys(), key=len, reverse=True):
            if name in cleaned or cleaned.startswith(name):
                member_key = aliases[name]
                member_name = name
                break
        if not member_key:
            warnings.append(
                ParseWarning(
                    f"unknown member or invalid line: {body[:40]}",
                    raw_line=original_line.strip(),
                )
            )
            continue

        url, video_id = _find_url_near(norm_lines, i + 1, expanded_urls)
        if not video_id:
            vid2 = extract_video_id(body)
            if vid2:
                video_id = vid2
                url = url or body

        event_date = base_date + timedelta(days=1) if is_tomorrow else base_date
        try:
            local_start = datetime(
                event_date.year,
                event_date.month,
                event_date.day,
                hour,
                minute,
                tzinfo=tz,
            )
        except ValueError:
            warnings.append(
                ParseWarning("invalid local datetime", raw_line=original_line.strip())
            )
            continue
        planned_utc = format_utc(local_start.astimezone(ZoneInfo("UTC")))

        # schedule_date on hint: calendar day of the planned start (after 明日)
        hint_schedule_date = event_date.isoformat()

        if url and not video_id and YT_CHANNEL_RE.search(url):
            logger.info(
                "X schedule hint has channel URL only: member=%s", member_key
            )
        if member_only:
            logger.info(
                "X schedule hint member_only: member=%s planned_start=%s",
                member_key,
                planned_utc,
            )

        hints.append(
            ScheduleHint(
                source_post_id=source_post_id,
                source_post_created_at=source_post_created_at,
                schedule_date=hint_schedule_date,
                member_key=member_key,
                member_name=member_name,
                planned_start_at=planned_utc,
                youtube_url=url,
                youtube_video_id=video_id,
                member_only=member_only,
                raw_text=original_line.strip(),
                fetched_at=fetched_at,
                status="active",
                edit_history_tweet_ids=edit_ids,
            )
        )

    return ParseResult(
        is_schedule_post=True,
        schedule_date=schedule_date,
        hints=hints,
        warnings=warnings,
    )
