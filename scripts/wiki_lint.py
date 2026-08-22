#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 Lint 检查入口（LLM-Wiki Lite）。

把三类检查编排成**一份报告**，供 AI 读完即响应（自动修 / 排 Ingest 待办）：
  A. 事实校验   — wiki_check.py （字面量/版本/证据/断链类硬错误）
  B. 覆盖度     — wiki_coverage.py（供给侧，冷启动即可跑）
  C. 行为度量   — wiki_metrics.py（需求侧，需 obsidian-mcp-pro 遥测；无则跳过）

设计：底层三个脚本保持独立可调用；本入口仅捕获其 stdout 合并输出，
不改动它们的实现。报告末尾将一行结论写入 wiki/log.md 的 `## lint` 小节。

用法：
  python wiki_lint.py <project-root> [--events telemetry.jsonl]
  # 不传 --events → 只跑 A+B，C 标注跳过
"""

import sys
import io
import argparse
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import wiki_check
import wiki_coverage
import wiki_metrics


def _capture(func, argv=None):
    buf = io.StringIO()
    with redirect_stdout(buf):
        if argv is None:
            func()
        else:
            func(argv)
    return buf.getvalue().rstrip("\n")


def run(root: Path, events: Path | None) -> str:
    root = root.resolve()
    sections = []

    # A. 事实校验
    sections.append(_capture(wiki_check.main, ["wiki_check", str(root)]))

    # B. 覆盖度（冷启动可跑）
    sections.append(_capture(wiki_coverage.main, ["wiki_coverage", str(root)]))

    # C. 行为度量（需遥测）
    if events and Path(events).is_file():
        old_argv = sys.argv
        sys.argv = ["wiki_metrics", "--events", str(events), "--vault", str(root)]
        try:
            sections.append(_capture(wiki_metrics.main))
        finally:
            sys.argv = old_argv
    else:
        sections.append(
            "=== Knowledge gaps (actionable, §10.5) ===\n"
            "(skipped: 未提供 --events 遥测；需求侧度量需 obsidian-mcp-pro 调用记录)"
        )

    report = "\n\n".join(s for s in sections if s)
    report = "== LLM-Wiki Lint report ==\n" + report
    return report


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="LLM-Wiki unified Lint check")
    ap.add_argument("root", type=Path, help="project root (contains wiki/)")
    ap.add_argument("--events", type=Path, default=None,
                    help="obsidian-mcp-pro telemetry JSONL (enables demand-side metrics)")
    args = ap.parse_args()

    if not (args.root / "wiki").is_dir():
        print("ERROR: 未找到 %s/wiki" % args.root)
        return 1

    print(run(args.root, args.events))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        import wiki_log
        root = sys.argv[1] if len(sys.argv) > 1 else '.'
        wiki_log.dump_error(root, e)
        print('ERROR: %s' % e)
        sys.exit(1)
