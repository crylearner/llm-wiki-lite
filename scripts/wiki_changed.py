#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 A 变更检测（LLM-Wiki Lite 增量 Ingest 第一步，零 LLM）。

三路检测，输出"待处理清单"供阶段 B（影响面分析 / Triage）消费：

1. 版本漂移：src 页 frontmatter `version` vs 证据文件（raw 所指文档）
   中的版本号集合——带版本号文档的权威变更信号。
2. git 兜底：自上次触碰 wiki/ 的提交以来，docs/ 下发生变更的文件
   ——覆盖无版本号文档。锚点 = git log -1 --format=%H -- wiki/；
   可用 --since <ref> 覆盖。非 git 环境或 git 不可用则跳过本路。
3. 未录入盘点：docs/ 下未被任何 src 页 raw 引用的 .md 文档
   ——新素材的发现通道（karpathy inventory 思路，no-material 台账
   未入库前由人/AI 判断是否真的没营养）。

用法：python wiki_changed.py [project-root] [--since <git-ref>]
约定：report-only，零第三方依赖（Python 3.8+）；退出码不承载信息。
"""

import re
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r'(?<![0-9A-Za-z.])V?\d+(?:\.\d+){1,3}(?![0-9])')


def parse_frontmatter(text):
    if not text.startswith('---'):
        return {}, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            fm = {}
            for ln in lines[1:i]:
                if ':' in ln and not ln[:1].isspace():
                    k, v = ln.split(':', 1)
                    fm[k.strip()] = v.strip()
            return fm, '\n'.join(lines[i + 1:])
    return {}, text


def raw_values(fm):
    v = (fm.get('raw') or '').strip()
    if not v:
        return []
    if v.startswith('['):
        return [x.strip().strip('\'"') for x in v[1:-1].split(',') if x.strip()]
    return [v.strip('\'"')]


def is_absolute(p):
    return bool(re.match(r'^[A-Za-z]:[\\/]', p)) or p.startswith(('/', '\\')) or '://' in p


def git(root, *args):
    try:
        p = subprocess.run(['git', '-c', 'core.quotepath=false'] + list(args),
                           cwd=str(root), capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
    except (OSError, ValueError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    since = None
    args = []
    i = 1
    while i < len(argv):
        if argv[i] == '--since' and i + 1 < len(argv):
            since = argv[i + 1]
            i += 2
            continue
        args.append(argv[i])
        i += 1
    root = Path(args[0] if args else '.').resolve()
    wiki_root = root / 'wiki'
    if not wiki_root.is_dir():
        print('== wiki_changed ==\nERROR: 未找到 %s' % wiki_root)
        return

    print('== wiki_changed（阶段 A：变更检测）==')
    print('root: %s\n' % root)

    # ---- 路 1：版本漂移 ----
    referenced = set()
    drift = []
    for p in sorted(wiki_root.rglob('src-*.md')):
        fm, _ = parse_frontmatter(p.read_text(encoding='utf-8', errors='replace'))
        pv = (fm.get('version') or '').strip()
        files = []
        for v in raw_values(fm):
            if is_absolute(v):
                continue
            f = root / v
            referenced.add(f.resolve())
            if f.is_file():
                files.append(f)
        if pv and files:
            src_text = '\n'.join(f.read_text(encoding='utf-8', errors='replace') for f in files)
            found = sorted(set(VERSION_RE.findall(src_text)))
            if found and pv not in found:
                drift.append((p.name, pv, found))

    print('[1] 版本漂移：')
    if drift:
        for name, pv, found in drift:
            print('    DRIFT %s — 页面锚点 %s，源含 %s → 需复核' % (name, pv, '/'.join(found)))
    else:
        print('    无（所有带锚点的 src 页与源文档版本一致，或无版本号）')

    # ---- 路 2：git 兜底 ----
    print('\n[2] git 兜底（docs/ 自上次 wiki 提交以来的变更）：')
    changed_docs = []
    if since is None:
        since = git(root, 'log', '-1', '--format=%H', '--', 'wiki')
    if since is None:
        print('    跳过（非 git 仓库 / git 不可用 / wiki/ 尚无提交）')
    else:
        changed = git(root, 'diff', '--name-only', '%s..HEAD' % since, '--', 'docs')
        changed_docs = [x.strip() for x in (changed or '').splitlines()
                        if x.strip() and '.obsidian/' not in x]
        if changed_docs:
            for f in changed_docs:
                print('    CHANGED %s' % f)
        else:
            print('    无变更（锚点 %s）' % since[:8])

    # ---- 路 3：未录入盘点 ----
    print('\n[3] docs/ 未录入盘点（未被任何 src 页 raw 引用的 .md）：')
    docs_root = root / 'docs'
    orphans = []
    if docs_root.is_dir():
        for p in sorted(docs_root.rglob('*.md')):
            if p.resolve() not in referenced:
                orphans.append(p.relative_to(root).as_posix())
    if orphans:
        for o in orphans:
            print('    UNDIGESTED %s' % o)
        print('    （含从未录入的新文档；是否 No material 由阶段 B Triage 判断）')
    else:
        print('    无')

    # ---- 待处理清单 ----
    todo = ([('漂移复核', d[0]) for d in drift]
            + [('git变更', f) for f in changed_docs])
    print('\n待处理清单：')
    if todo:
        for reason, name in todo:
            print('    - %s ← %s' % (reason, name))
    else:
        print('    空（no-op，Ingest 可直接结束）')


if __name__ == '__main__':
    try:
        main(sys.argv)
    except Exception as e:
        import wiki_log
        root = sys.argv[1] if len(sys.argv) > 1 else '.'
        wiki_log.dump_error(root, e)
        print('ERROR: %s' % e)
        raise SystemExit(1)
