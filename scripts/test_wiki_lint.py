#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for wiki_lint.py: orchestrates check+coverage(+metrics) into one report."""
import json
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import wiki_lint as wl


def _make_vault(tmp):
    w = tmp / "wiki"
    w.mkdir()
    (tmp / "docs").mkdir()
    (tmp / "docs" / "auth.md").write_text("# 认证机制\n## 超时配置\n", encoding="utf-8-sig")
    (w / "src-auth.md").write_text(
        "---\nraw: docs/auth.md\n---\n# 认证\n介绍认证与超时配置。", encoding="utf-8-sig")
    (w / "con-b.md").write_text("见 [[src-auth]]。重试策略在此。", encoding="utf-8-sig")
    return w


def test_without_events_skips_metrics():
    t = Path(tempfile.mkdtemp())
    try:
        _make_vault(t)
        report = wl.run(t, None)
        assert "== LLM-Wiki Lint report ==" in report
        assert "== wiki_check report ==" in report
        assert "== wiki_coverage report ==" in report
        assert "skipped: 未提供 --events" in report
    finally:
        shutil.rmtree(t)


def test_with_events_includes_gaps():
    t = Path(tempfile.mkdtemp())
    try:
        _make_vault(t)
        evf = t / "tel.jsonl"
        evf.write_text("\n".join(json.dumps(e) for e in [
            {"ts": "2026-08-22T10:00:00Z", "tool": "search_notes",
             "vault": str(t).replace("\\", "/"), "query": "限流策略",
             "isError": False, "contentItems": 1, "primaryTextBytes": 5, "durationMs": 3},
        ]), encoding="utf-8")
        report = wl.run(t, evf)
        assert "[INGEST]" in report, report
        assert "skipped" not in report.split("Knowledge gaps (actionable")[-1]
    finally:
        shutil.rmtree(t)


def test_dump_error_writes_log():
    t = Path(tempfile.mkdtemp())
    try:
        import wiki_log
        wiki_log.dump_error(t, RuntimeError("boom"))
        logf = wiki_log.log_file(t)
        assert logf.is_file(), logf
        assert "boom" in logf.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(t)


if __name__ == "__main__":
    test_without_events_skips_metrics()
    test_with_events_includes_gaps()
    test_dump_error_writes_log()
    print("OK: all wiki_lint self-checks passed")
