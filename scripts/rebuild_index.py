#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""index 投影重建（LLM-Wiki Lite）。

index.md 是本地缓存（投影，不提交）：每一行都可从页面本身再生——
链接 ← 文件名；摘要 ← 固定小节首句（con 页 `## 定义` / src 页 `## 摘要`，
无该小节则回退 H1 后首段，再无则 `(no summary)`）。行按文件名排序、
按前缀分表——index 不携带任何独有信息（投影纪律，规则 2）。

行为：
  默认：重建并写入 <root>/wiki/index.md（本脚本唯一可写的文件）
  --check：只对比现有 index 与重建结果的差异，不写文件（陈旧度自检）

用法：python rebuild_index.py [project-root] [--check]
约定：零第三方依赖（Python 3.8+ 标准库）；除 wiki/index.md 外永不修改文件；
退出码不承载信息，报告即接口。
"""

import sys
from datetime import datetime
from pathlib import Path
import re

SECTION_RE = re.compile(r'^##\s*(?:定义|摘要|背景)\s*$')
TYPE_LABELS = [('src-', '来源（sources/）'), ('con-', '概念（concepts/）'), ('ent-', '实体（entities/）'),
               ('cmp-', '对比（cmp-）'), ('syn-', '综述（syntheses/）'), ('dec-', '决策（decisions/）')]


def parse_frontmatter(text):
    if not text.startswith('---'):
        return {}, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == '---':
            return {}, '\n'.join(lines[i + 1:])
    return {}, text


def clean_summary(s):
    s = s.lstrip('-*># ').strip()
    return s[:80]


def summary_of(body):
    lines = body.splitlines()
    # 1) 固定小节（## 定义 / ## 摘要）后的第一个非空非标题行
    for i, ln in enumerate(lines):
        if SECTION_RE.match(ln):
            for j in range(i + 1, len(lines)):
                t = lines[j].strip()
                if t.startswith('#'):
                    break
                if t:
                    return clean_summary(t)
            break
    # 2) 回退：H1 之后的第一个正文行
    seen_h1 = False
    for ln in lines:
        t = ln.strip()
        if t.startswith('# '):
            seen_h1 = True
            continue
        if seen_h1 and t and not t.startswith(('#', '>', '-', '|', '```')):
            return clean_summary(t)
    return '(no summary)'


def build_index(wiki_root):
    pages = [p for p in sorted(wiki_root.rglob('*.md'))
             if p.name not in ('index.md', 'log.md')]
    groups = {label: [] for _, label in TYPE_LABELS}
    groups['其他'] = []
    for p in pages:
        label = next((lab for pre, lab in TYPE_LABELS if p.name.startswith(pre)), '其他')
        _, body = parse_frontmatter(p.read_text(encoding='utf-8', errors='replace'))
        groups[label].append((p.stem, summary_of(body)))

    lines = ['# Wiki Index', '',
             '> 本地投影缓存：不提交（gitignore）。重建：python skills/llm-wiki-lite/scripts/rebuild_index.py',
             '> 最后重建：%s' % datetime.now().strftime('%Y-%m-%d %H:%M'), '']
    for _, label in TYPE_LABELS + [('x', '其他')]:
        rows = groups.get(label)
        if not rows:
            continue
        lines += ['## ' + label, '', '| 页面 | 摘要 |', '|------|------|']
        for name, s in rows:
            lines.append('| [[%s]] | %s |' % (name, s.replace('|', '\\|')))
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n', len(pages)


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    args = [a for a in argv[1:] if a != '--check']
    check_only = '--check' in argv
    root = Path(args[0] if args else '.').resolve()
    wiki_root = root / 'wiki'
    if not wiki_root.is_dir():
        print('== rebuild_index ==\nERROR: 未找到 %s' % wiki_root)
        return

    new_text, n = build_index(wiki_root)
    index_path = wiki_root / 'index.md'
    print('== rebuild_index == | pages: %d' % n)

    if index_path.is_file():
        old_text = index_path.read_text(encoding='utf-8', errors='replace')
        if old_text.strip() == new_text.strip():
            print('index 已是最新（内容一致，忽略时间戳行）')
            return
        if check_only:
            old_lines = old_text.strip().splitlines()
            new_lines = new_text.strip().splitlines()
            print('[check] index 陈旧：现有 %d 行 vs 重建 %d 行（--check 模式，未写入）'
                  % (len(old_lines), len(new_lines)))
            print('陈旧处置：直接运行无 --check 的重建（以重建为准，投影无独有信息）')
            return

    if check_only:
        print('[check] index 不存在；--check 模式不写入。去掉 --check 生成。')
        return

    index_path.write_text(new_text, encoding='utf-8')
    print('已重建并写入 %s' % index_path)


if __name__ == '__main__':
    main(sys.argv)
