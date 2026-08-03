"""Shared HTML theme and helpers for weekly reports."""

from __future__ import annotations

import html
import json
from typing import Any, Optional

from ..models import text_color_for_bg


def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def fmt_num(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if digits == 0:
        return f"{int(round(f)):,}"
    return f"{f:,.{digits}f}"


def fmt_coverage(c: Optional[float]) -> str:
    if c is None:
        return "—"
    return f"{c * 100:.1f}%"


BASE_CSS = """
:root {
  --bg: #0b0c10;
  --panel: #141722;
  --panel2: #1a1f2e;
  --border: #2a3145;
  --text: #eef1f8;
  --muted: #9aa3b8;
  --accent: #7c6cff;
  --good: #3dd68c;
  --warn: #ffb020;
  --radius: 16px;
  --shadow: 0 10px 40px rgba(0,0,0,.35);
  --member: #7c6cff;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background:
    radial-gradient(1200px 600px at 10% -10%, rgba(124,108,255,.18), transparent 60%),
    radial-gradient(900px 500px at 90% 0%, rgba(255,126,179,.12), transparent 55%),
    radial-gradient(800px 400px at 50% 100%, rgba(245,208,0,.08), transparent 50%),
    var(--bg);
  color: var(--text);
  line-height: 1.6;
}
a { color: #a5b4fc; text-decoration: none; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 28px 20px 80px; }
.hero {
  position: relative; overflow: hidden;
  border: 1px solid var(--border); border-radius: 24px;
  background: linear-gradient(135deg, #171a28 0%, #121525 50%, #1a1428 100%);
  box-shadow: var(--shadow);
  padding: 36px 32px 28px; margin-bottom: 28px;
}
.hero h1 { margin: 0 0 10px; font-size: clamp(1.5rem, 3vw, 2.1rem); font-weight: 800; line-height: 1.25; }
.hero h1 .accent { color: var(--member); }
.hero .sub { color: var(--muted); max-width: 860px; margin: 0 0 18px; font-size: 15px; }
.badge {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 12px; letter-spacing: .06em; text-transform: uppercase;
  color: var(--muted); border: 1px solid var(--border);
  background: rgba(255,255,255,.03); border-radius: 999px;
  padding: 6px 12px; margin-bottom: 14px;
}
.meta-row { display: flex; flex-wrap: wrap; gap: 10px 16px; color: var(--muted); font-size: 13px; }
.grid { display: grid; gap: 16px; }
.g3 { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 960px) { .g3 { grid-template-columns: 1fr; } }
.card {
  background: linear-gradient(180deg, rgba(255,255,255,.03), transparent 40%), var(--panel);
  border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: var(--shadow); padding: 18px;
}
.kpi { display: flex; flex-direction: column; gap: 6px; min-height: 100px; }
.kpi .label { color: var(--muted); font-size: 13px; }
.kpi .value { font-size: clamp(1.4rem, 2.4vw, 1.9rem); font-weight: 800; letter-spacing: -.02em; color: var(--member); }
.kpi .hint { color: var(--muted); font-size: 12px; }
section { margin-bottom: 32px; }
.sec-title { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin: 0 0 14px; }
.sec-title h2 { margin: 0; font-size: 1.2rem; font-weight: 700; }
.sec-title p { margin: 0; color: var(--muted); font-size: 13px; }
.chart-box { position: relative; height: 320px; }
.chart-box.sm { height: 260px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; }
.note {
  color: var(--muted); font-size: 13px; line-height: 1.55;
  border: 1px solid var(--border); border-radius: 14px;
  background: rgba(255,255,255,.03); padding: 14px 16px;
}
.empty {
  text-align: center; padding: 48px 20px; color: var(--muted);
  border: 1px dashed var(--border); border-radius: 16px;
}
.empty b { display: block; color: var(--text); font-size: 1.2rem; margin-bottom: 8px; }
.avatar {
  width: 42px; height: 42px; border-radius: 14px;
  display: inline-grid; place-items: center; font-weight: 800; font-size: 16px;
  margin-right: 10px; vertical-align: middle;
}
.cdn-warn {
  display: none; margin: 12px 0; padding: 10px 12px; border-radius: 10px;
  border: 1px solid rgba(255,176,32,.35); background: rgba(255,176,32,.1);
  color: #fde68a; font-size: 13px;
}
.footer { margin-top: 40px; color: var(--muted); font-size: 12px; text-align: center; }
"""


def chart_js_includes() -> str:
    return """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<div class="cdn-warn" id="cdn-warn">未能加载 Chart.js CDN。若需离线浏览，请联网一次。</div>
<script>
window.addEventListener('load', function() {
  if (typeof Chart === 'undefined') {
    var el = document.getElementById('cdn-warn');
    if (el) el.style.display = 'block';
  }
});
</script>
"""


def page_shell(
    *,
    title: str,
    member_color: str = "#7c6cff",
    body: str,
    extra_head: str = "",
) -> str:
    text_c = text_color_for_bg(member_color)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{esc(title)}</title>
{chart_js_includes()}
<style>
{BASE_CSS}
:root {{ --member: {esc(member_color)}; }}
.avatar-member {{ background: {esc(member_color)}; color: {esc(text_c)}; }}
</style>
{extra_head}
</head>
<body>
<div class="wrap">
{body}
<div class="footer">X Schedule Discovery · 峰值均为本程序采集峰值，非 YouTube 官方完整峰值 · 失败采样不按 0 计入</div>
</div>
</body>
</html>
"""


def kpi_card(label: str, value: str, hint: str = "") -> str:
    return f"""
<div class="card kpi">
  <div class="label">{esc(label)}</div>
  <div class="value">{value}</div>
  {f'<div class="hint">{esc(hint)}</div>' if hint else ''}
</div>
"""


def json_script(data: Any, var_name: str = "REPORT_DATA") -> str:
    payload = json.dumps(data, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    return f"<script>const {var_name} = {payload};</script>"
