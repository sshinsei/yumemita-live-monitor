"""
Yumemita Live Monitor
Slim YouTube live discovery + concurrent-viewer sampling with X schedule hints.

Sibling project to Live_Viewers_Count — does not read/write that tree.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from yumemita_live_monitor.channels import ChannelConfigError, load_channels
from yumemita_live_monitor.config import load_config
from yumemita_live_monitor.monitor import ViewerMonitor
from yumemita_live_monitor.schedule_feed import ScheduleFeedService
from yumemita_live_monitor.schedule_store import ScheduleHintStore
from yumemita_live_monitor.utils import setup_logging

logger = logging.getLogger("yumemita_live_monitor")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="YouTube 同接监控（精简）+ X 日程辅助发现",
    )
    p.add_argument(
        "-c",
        "--config",
        default="config.json",
        help="配置文件路径（默认 config.json）",
    )
    sub = p.add_subparsers(dest="command")
    sub.add_parser("run", help="启动常驻监控（默认）")

    rep = sub.add_parser("report", help="手工生成周报")
    rep.add_argument(
        "--week",
        required=True,
        help="ISO 周，例如 2026-W31",
    )

    ref = sub.add_parser("refresh-x", help="单次刷新 X 日程（只读接入）")
    ref.add_argument(
        "--text-file",
        help="离线模式：从文本文件解析日程（不调用 X API）",
    )
    ref.add_argument("--post-id", default="offline-1", help="离线帖子 ID")
    ref.add_argument(
        "--created-at",
        default="2026-02-21T03:00:00Z",
        help="离线帖子创建时间 UTC ISO",
    )

    sub.add_parser("version", help="打印版本")
    return p


def cmd_run(cfg_path: str) -> int:
    try:
        cfg = load_config(cfg_path)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1

    setup_logging(cfg.log_dir)

    try:
        channels = load_channels(cfg.channels_file)
    except ChannelConfigError as e:
        logger.error("%s", e)
        print(f"频道配置错误: {e}", file=sys.stderr)
        return 1

    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.samples_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.weekly_reports_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)

    monitor = ViewerMonitor(cfg, channels)

    def _stop(*_a):
        monitor.request_stop()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _stop)
        except Exception:
            pass

    monitor.run_forever()
    return 0


def cmd_report(cfg_path: str, week: str) -> int:
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1

    setup_logging(cfg.log_dir)
    try:
        channels = load_channels(cfg.channels_file)
    except ChannelConfigError as e:
        print(f"频道配置错误: {e}", file=sys.stderr)
        return 1

    from yumemita_live_monitor.reports.scheduler import ReportScheduler
    from yumemita_live_monitor.storage import SampleStore, StreamsStore

    Path(cfg.weekly_reports_dir).mkdir(parents=True, exist_ok=True)
    streams_store = StreamsStore(cfg.streams_path)
    sample_store = SampleStore(cfg.samples_path)
    scheduler = ReportScheduler(cfg, channels, streams_store, sample_store)
    try:
        out = scheduler.rebuild_week(week)
        print(f"周报已生成: {out}")
        return 0
    except Exception as e:
        logger.exception("周报生成失败")
        print(f"周报生成失败: {e}", file=sys.stderr)
        return 1


def cmd_refresh_x(cfg_path: str, text_file: str | None, post_id: str, created_at: str) -> int:
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        # Allow offline parse with example config placeholder
        if text_file:
            from yumemita_live_monitor.config import load_config_allow_placeholder

            try:
                cfg = load_config_allow_placeholder(cfg_path)
            except Exception as e2:
                print(f"配置错误: {e2}", file=sys.stderr)
                return 1
        else:
            print(f"配置错误: {e}", file=sys.stderr)
            return 1

    setup_logging(cfg.log_dir)
    Path(cfg.data_dir).mkdir(parents=True, exist_ok=True)
    store = ScheduleHintStore(cfg.schedule_hints_path)
    feed = ScheduleFeedService(cfg, store)

    if text_file:
        text = Path(text_file).read_text(encoding="utf-8")
        result = feed.ingest_text(
            text,
            source_post_id=post_id,
            source_post_created_at=created_at,
        )
        print(
            f"offline parse: is_schedule={result.is_schedule_post} "
            f"hints={len(result.hints)} warnings={len(result.warnings)}"
        )
        for h in result.hints:
            print(
                f"  - {h.member_key} {h.planned_start_at} "
                f"vid={h.youtube_video_id or '-'} only={h.member_only}"
            )
        for w in result.warnings:
            print(f"  warn: {w.message}")
        return 0

    if not cfg.x_schedule_enabled:
        print("x_schedule_enabled=false；请在 config 中启用并设置 X_BEARER_TOKEN", file=sys.stderr)
        return 2

    stats = feed.refresh()
    print(
        f"X refresh: fetched={stats.fetched} new={stats.new_posts} "
        f"parsed_hints={stats.parsed_hints} error={stats.error or '-'}"
    )
    return 0 if not stats.error else 1


def _extract_config_arg(argv: list[str]) -> tuple[str, list[str]]:
    """Allow -c/--config before or after the subcommand."""
    config = "config.json"
    cleaned: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-c", "--config") and i + 1 < len(argv):
            config = argv[i + 1]
            i += 2
            continue
        if a.startswith("--config="):
            config = a.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(a)
        i += 1
    return config, cleaned


def main(argv: list[str] | None = None) -> int:
    raw = list(argv if argv is not None else sys.argv[1:])
    config_path, cleaned = _extract_config_arg(raw)
    parser = build_parser()
    args = parser.parse_args(cleaned)
    # Prefer explicitly extracted path; parent default may still be on args
    if config_path:
        args.config = config_path
    command = args.command or "run"
    if command == "version":
        from yumemita_live_monitor import __version__

        print(__version__)
        return 0
    if command == "report":
        return cmd_report(args.config, args.week)
    if command == "refresh-x":
        return cmd_refresh_x(
            args.config,
            getattr(args, "text_file", None),
            getattr(args, "post_id", "offline-1"),
            getattr(args, "created_at", "2026-02-21T03:00:00Z"),
        )
    return cmd_run(args.config)


if __name__ == "__main__":
    sys.exit(main())
