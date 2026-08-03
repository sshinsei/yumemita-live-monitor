"""Real-world schedule formats + member_only switch."""

from __future__ import annotations

from yumemita_live_monitor.schedule_parser import parse_schedule_post

REAL_POST = """／
🛸夢限大みゅーたいぷ
7/29(水)の配信スケジュール🌟
＼

💭22:00～ 千石ユノ
https://www.youtube.com/@yuno_yumemita

🍞23:00～ 峰月律
https://www.youtube.com/@ritsu_yumemita

【メン限】23:30～ 仲町あられ
https://www.youtube.com/@arale_yumemita

☀明日朝6:30～ 仲町あられ
https://www.youtube.com/@arale_yumemita

#バンドリ #ゆめみた
"""


def _parse(*, member_only_enabled: bool):
    return parse_schedule_post(
        REAL_POST,
        source_post_id="2082409548057616698",
        source_post_created_at="2026-07-29T10:15:00Z",
        fetched_at="2026-07-31T00:00:00Z",
        member_only_enabled=member_only_enabled,
    )


def test_real_post_four_hints_with_switch_off():
    r = _parse(member_only_enabled=False)
    assert r.is_schedule_post
    assert r.schedule_date == "2026-07-29"
    assert len(r.hints) == 4
    by = {(h.member_key, h.planned_start_at): h for h in r.hints}
    assert ("yuno", "2026-07-29T13:00:00Z") in by  # 22:00 JST
    assert ("ritsu", "2026-07-29T14:00:00Z") in by  # 23:00 JST
    # メン限 line still parsed as ordinary (flag false)
    arale_same = by[("arale", "2026-07-29T14:30:00Z")]
    assert arale_same.member_only is False
    # 明日朝6:30 → 7/30 06:30 JST = 2026-07-29T21:30:00Z
    arale_tmw = by[("arale", "2026-07-29T21:30:00Z")]
    assert arale_tmw.schedule_date == "2026-07-30"
    assert arale_tmw.member_only is False


def test_real_post_member_only_switch_on():
    r = _parse(member_only_enabled=True)
    assert len(r.hints) == 4
    marked = [h for h in r.hints if h.member_only]
    assert len(marked) == 1
    assert marked[0].member_key == "arale"
    assert marked[0].planned_start_at == "2026-07-29T14:30:00Z"
    # 明日行无メン限 → 仍为 false
    tmw = [h for h in r.hints if h.schedule_date == "2026-07-30"]
    assert len(tmw) == 1
    assert tmw[0].member_only is False


def test_member_only_after_name_with_switch():
    text = """#夢限大みゅーたいぷ 2/21(土)の配信スケジュール
⭐22:30～ 藤都子〖メンバー限定〗
youtube.com/@miyako_yumemita
"""
    off = parse_schedule_post(
        text,
        source_post_id="1",
        source_post_created_at="2026-02-20T12:00:00Z",
        fetched_at="2026-02-20T12:01:00Z",
        member_only_enabled=False,
    )
    assert len(off.hints) == 1
    assert off.hints[0].member_key == "miyako"
    assert off.hints[0].member_only is False

    on = parse_schedule_post(
        text,
        source_post_id="1",
        source_post_created_at="2026-02-20T12:00:00Z",
        fetched_at="2026-02-20T12:01:00Z",
        member_only_enabled=True,
    )
    assert on.hints[0].member_only is True
