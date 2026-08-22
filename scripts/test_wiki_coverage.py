#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-check for wiki_coverage.py using a synthetic vault (tempfile)."""
import sys
import tempfile
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import wiki_coverage as wc

tmp = Path(tempfile.mkdtemp())
wiki = tmp / 'wiki'
docs = tmp
wiki.mkdir()
docs.mkdir(exist_ok=True)

# docs/source-a.md : 3 sections, terms "认证" "超时" "重试"
(docs / 'source-a.md').write_text(
    "# 认证机制\n## 超时配置\n## 重试策略\n正文...", encoding='utf-8')
# src-a.md links raw -> source-a.md ; body covers 认证 + 超时, misses 重试
(wiki / 'src-a.md').write_text(
    "---\nraw: source-a.md\n---\n# 认证机制\n本页介绍认证与超时配置。", encoding='utf-8')
# con-b.md links [[src-a]] and covers 重试
(wiki / 'con-b.md').write_text(
    "---\ntype: concept\n---\n见 [[src-a]]。重试策略在本文展开。", encoding='utf-8')

# a page with NO raw -> skipped
(wiki / 'src-c.md').write_text("# no raw here", encoding='utf-8')

pages = wc.collect_pages(wiki)
assert any(p.name == 'src-a.md' for p in pages)
r = wc.coverage_for(wiki / 'src-a.md', pages, tmp, None)
print('src-a =>', r)
assert r['sec_total'] == 3, r
# 认证+超时 in src-a body, 重试 in con-b => all 3 covered
assert r['sec_hit'] == 3, r
assert r['rate'] == 100.0, r
assert r['uncovered'] == []

# src-c skipped (no raw)
rc = wc.coverage_for(wiki / 'src-c.md', pages, tmp, None)
assert rc is None, rc

# uncovered detection: make a page that misses one section
(docs / 'source-d.md').write_text("# 安装\n## 卸载\n", encoding='utf-8')
(wiki / 'src-d.md').write_text(
    "---\nraw: source-d.md\n---\n# 安装\n介绍安装步骤。", encoding='utf-8')
pages2 = wc.collect_pages(wiki)
rd = wc.coverage_for(wiki / 'src-d.md', pages2, tmp, None)
print('src-d =>', rd)
assert rd['sec_total'] == 2
assert rd['sec_hit'] == 1, rd   # 安装 covered in body, 卸载 not
assert '卸载' in rd['uncovered'], rd

shutil.rmtree(tmp)
print('OK: all wiki_coverage self-checks passed')
