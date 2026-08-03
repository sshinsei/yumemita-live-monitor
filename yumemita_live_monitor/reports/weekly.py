"""Weekly personal brief report generation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Sequence

from ..models import Channel, StreamRecord, ViewerSample
from ..utils import atomic_write_text, ensure_dir, utc_now_iso
from .html_common import esc, fmt_coverage, fmt_num, json_script, kpi_card, page_shell
from .stats import MemberStats, build_member_stats, format_duration
from .windows import TimeWindow

logger = logging.getLogger("yumemita_live_monitor.reports.weekly")


def render_weekly_member_html(summary: Dict[str, Any], member: Dict[str, Any]) -> str:
    color = member.get("color") or "#7c6cff"
    name = member.get("member_name") or member.get("member_key")
    empty = (member.get("stream_count") or 0) == 0 and (member.get("sample_count") or 0) == 0

    kpis = ""
    if not empty:
        kpis = f"""
<div class="grid g3" style="margin-bottom:20px">
  {kpi_card("本周开播场次", fmt_num(member.get("stream_count")), f"有采样活跃 {fmt_num(member.get('active_stream_count'))} 场")}
  {kpi_card("总直播时长", format_duration(member.get("total_duration_seconds") or 0))}
  {kpi_card("本程序采集峰值", fmt_num(member.get("peak_concurrent_viewers")), member.get("peak_at") or "")}
  {kpi_card("时间加权平均同接", fmt_num(member.get("time_weighted_avg"), 1))}
  {kpi_card("中位同接", fmt_num(member.get("median_viewers"), 1))}
  {kpi_card("采样覆盖率", fmt_coverage(member.get("coverage")), f"有效样本 {fmt_num(member.get('sample_count'))} / 预期 {fmt_num(member.get('expected_samples'))}")}
</div>
"""

    rows = []
    for st in member.get("streams") or []:
        rows.append(
            "<tr>"
            f"<td>{esc(st.get('title') or st.get('video_id'))}<br>"
            f"<span style='color:var(--muted);font-size:11px'>{esc(st.get('video_id'))}</span></td>"
            f"<td>{esc(st.get('actual_start_at') or '—')}</td>"
            f"<td>{esc(format_duration(st.get('duration_seconds') or 0))}</td>"
            f"<td>{fmt_num(st.get('peak_concurrent_viewers'))}</td>"
            f"<td>{fmt_num(st.get('time_weighted_avg'), 1)}</td>"
            f"<td>{fmt_coverage(st.get('coverage'))}</td>"
            "</tr>"
        )
    table = (
        "<div class='empty'><b>本周无直播</b>该成员在本统计周内没有可统计的直播或采样记录。</div>"
        if empty
        else f"""
<div class="card" style="overflow-x:auto">
<table>
  <thead><tr>
    <th>标题</th><th>开始时间 (UTC)</th><th>时长</th><th>峰值</th><th>平均同接</th><th>覆盖率</th>
  </tr></thead>
  <tbody>{''.join(rows) if rows else '<tr><td colspan="6">无明细</td></tr>'}</tbody>
</table>
</div>
"""
    )

    charts = (
        ""
        if empty
        else """
<section>
  <div class="sec-title"><h2>同接趋势</h2><p>按 video 区分 · 本程序采集样本</p></div>
  <div class="card"><div class="chart-box"><canvas id="trendChart"></canvas></div></div>
</section>
<section>
  <div class="sec-title"><h2>各场峰值与平均</h2><p>柱状对比</p></div>
  <div class="card"><div class="chart-box sm"><canvas id="barChart"></canvas></div></div>
</section>
"""
    )

    body = f"""
<div class="hero">
  <div class="badge">周报 · {esc(summary.get('label'))}</div>
  <h1><span class="avatar avatar-member">{esc((name or '?')[0])}</span><span class="accent">{esc(name)}</span> 个人周报</h1>
  <p class="sub">统计区间（{esc(summary.get('timezone'))} 自然周，UTC 边界：{esc(summary.get('start_utc'))} ~ {esc(summary.get('end_utc'))}，左闭右开）</p>
  <div class="meta-row">
    <span>生成时间：{esc(summary.get('generated_at'))}</span>
    <span>数据来源：streams.csv + viewer_samples</span>
    <span>峰值：本程序采集峰值</span>
  </div>
</div>
{kpis}
{charts}
<section>
  <div class="sec-title"><h2>直播明细</h2><p>跨周直播仅统计落入本周窗口的采样与时长</p></div>
  {table}
</section>
<section>
  <div class="note">
    <b>数据口径</b><br>
    · 周期为左闭右开区间，时区 {esc(summary.get('timezone'))}。<br>
    · 平均同接为时间加权平均；API 失败导致的缺失不按 0 计算。<br>
    · 峰值均为「本程序采集峰值」，不声明为 YouTube 官方完整峰值。<br>
    · 覆盖率 = 有效样本 / 按直播时长与采样间隔估算的预期样本数。
  </div>
</section>
{json_script(
        {
            "member": {k: v for k, v in member.items() if k != "_chart"},
            "chart": member.get("_chart") or {},
        },
        "WEEKLY",
    )}
<script>
(function() {{
  if (typeof Chart === 'undefined') return;
  const m = WEEKLY.member || {{}};
  const chart = WEEKLY.chart || {{}};
  const color = m.color || '{color}';
  // Chart.js 3+ defaults borderColor to rgba(0,0,0,0.1) — nearly invisible on dark UI.
  // Assign an explicit palette so every stream series is readable.
  const palette = [color, '#f472b6', '#34d399', '#fbbf24', '#60a5fa', '#c084fc', '#fb7185', '#2dd4bf'];
  const trend = chart.trend || [];
  if (document.getElementById('trendChart') && trend.length) {{
    const datasets = trend.map((s, i) => {{
      const c = palette[i % palette.length];
      return {{
        label: s.title || s.video_id,
        data: (s.points || []).map(p => ({{x: p.t, y: p.v}})),
        borderColor: c,
        backgroundColor: c,
        tension: 0.2,
        pointRadius: 2,
        spanGaps: false,
      }};
    }});
    new Chart(document.getElementById('trendChart'), {{
      type: 'line',
      data: {{ datasets }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        interaction: {{ mode: 'nearest', intersect: false }},
        scales: {{
          x: {{ type: 'category', ticks: {{ maxTicksLimit: 8, color: '#9aa3b8' }}, grid: {{ color: 'rgba(255,255,255,.06)' }} }},
          y: {{ beginAtZero: true, ticks: {{ color: '#9aa3b8' }}, grid: {{ color: 'rgba(255,255,255,.06)' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#eef1f8' }} }} }}
      }}
    }});
  }}
  const bars = chart.bars || {{labels: [], peak: [], avg: []}};
  if (document.getElementById('barChart') && (bars.labels || []).length) {{
    new Chart(document.getElementById('barChart'), {{
      type: 'bar',
      data: {{
        labels: bars.labels,
        datasets: [
          {{ label: '峰值', data: bars.peak, backgroundColor: color }},
          {{ label: '平均', data: bars.avg, backgroundColor: '#6b7280' }}
        ]
      }},
      options: {{
        responsive: true, maintainAspectRatio: false,
        scales: {{
          x: {{ ticks: {{ color: '#9aa3b8' }}, grid: {{ display: false }} }},
          y: {{ beginAtZero: true, ticks: {{ color: '#9aa3b8' }}, grid: {{ color: 'rgba(255,255,255,.06)' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#eef1f8' }} }} }}
      }}
    }});
  }}
}})();
</script>
"""
    return page_shell(title=f"{name} 周报 {summary.get('label')}", member_color=color, body=body)


def _attach_chart_data(member_dict: Dict[str, Any], ms: MemberStats) -> None:
    trend = []
    labels = []
    peak = []
    avg = []
    for st in ms.streams:
        if st.samples_in_window:
            trend.append(
                {
                    "video_id": st.video_id,
                    "title": (st.title or st.video_id)[:40],
                    "points": [{"t": t, "v": v} for t, v in st.samples_in_window],
                }
            )
        labels.append((st.title or st.video_id)[:24])
        peak.append(st.peak_concurrent_viewers)
        avg.append(st.time_weighted_avg if st.time_weighted_avg is not None else 0)
    member_dict["_chart"] = {
        "trend": trend,
        "bars": {"labels": labels, "peak": peak, "avg": avg},
    }


def generate_weekly_report(
    *,
    window: TimeWindow,
    channels: Sequence[Channel],
    streams: Sequence[StreamRecord],
    samples: Sequence[ViewerSample],
    output_dir: Path,
    sampling_interval_seconds: float = 45.0,
) -> Path:
    """
    Generate weekly reports into output_dir (e.g. 2026-W31).
    Writes summary.json and <member_key>.html for each enabled member.
    """
    ensure_dir(output_dir)

    member_stats: List[MemberStats] = []
    for ch in channels:
        if not ch.enabled:
            continue
        ms = build_member_stats(
            member_key=ch.member_key,
            member_name=ch.member_name,
            color=ch.resolved_color(),
            streams=streams,
            samples=samples,
            window=window,
            sampling_interval_seconds=sampling_interval_seconds,
        )
        member_stats.append(ms)

    summary: Dict[str, Any] = {
        "type": "weekly",
        "label": window.label,
        "timezone": window.timezone,
        "start_utc": window.start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_utc": window.end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at": utc_now_iso(),
        "metrics_notes": {
            "peak": "本程序采集峰值，非 YouTube 官方完整峰值",
            "avg": "时间加权平均同接；缺失样本不按 0 计入",
            "coverage": "有效样本数 / 按时长与采样间隔估算的预期样本数",
        },
        "members": [],
    }

    for ms in member_stats:
        md = ms.to_dict()
        _attach_chart_data(md, ms)
        summary["members"].append({k: v for k, v in md.items() if k != "_chart"})
        html = render_weekly_member_html(summary, md)
        out = output_dir / f"{ms.member_key}.html"
        atomic_write_text(out, html)
        logger.info("Wrote weekly report %s", out)

    summary_path = output_dir / "summary.json"
    atomic_write_text(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    logger.info("Wrote weekly summary %s", summary_path)
    return output_dir
