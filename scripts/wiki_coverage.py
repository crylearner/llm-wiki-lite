#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态覆盖度（LLM-Wiki Lite，§10.2 供给侧维度「静态覆盖度」）。

冷启动兜底与结构性缺口发现：把每个源文档 doc-X 的章节树与关键术语，
与吸收它的 wiki 表达层（src 页正文 + 通过 [[src-xxx]] 链接到它的所有页面）
做零 LLM 命中比对，算出「源文档有多少被 wiki 吸收」。

这是"我以为你粗"的冷启动代理——不等于用户真觉得粗（§10.7 诚实边界），
仅作：① 新建库尚无查询行为时（需求侧全空）的唯一覆盖信号；
② 定位"源文档整段未被任何 wiki 页纳管"的结构性缺口。

匹配方式（纯机械、零 LLM）：
- 章节命中：源文档某 `#` 标题（归一化）作为子串出现在"吸收文本"中。
  吸收文本 = src 页正文 ∪ 所有 [[src-xxx]] 指向它的页面正文。
- 术语命中：从源文档标题树抽取的显着 token（len>=2，去标点）出现在吸收文本。

依赖：复用 wiki_check.py 的 parse_frontmatter / raw_values（raw 坐标解析唯一来源）。
约定：零第三方依赖（Python 3.8+ 标准库）；report-only，永不修改文件。

用法：
  python wiki_coverage.py [project-root] [页面文件名 ...]
  # 默认 project-root = 当前目录；范围 = wiki/ 全 .md（index.md/log.md 除外）
  # 传额外文件名 = 只算这些页面（按文件名或 stem 匹配）

输出：
  [coverage] <src 页> — 章节 X/Y 命中, 术语 a/b 命中, 覆盖率 Z%
  [uncovered] <src 页> — 未被吸收章节: ...
  Summary: sources=N, coverage_avg=Z%
"""

import re
import sys
from pathlib import Path

try:
    from wiki_check import parse_frontmatter, raw_values
except ImportError:  # allow running from outside scripts/ dir
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wiki_check import parse_frontmatter, raw_values

# 与 wiki_check.py 保持一致的文件过滤
INDEX_SKIP = ('index.md', 'log.md')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+)')

HEADING_RE = re.compile(r'^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$')
# 章节标题里抽取"术语 token"：去标点、去空白，保留 len>=2 的片段
TOKEN_RE = re.compile(r'[\w\u4e00-\u9fff]{2,}')
# 非结构化噪音词（中文/英文停用），命中它们不代表真覆盖
STOP_TOKENS = {
    '简介', '概述', '总结', '其他', '参考', '附录', '背景', '说明', '详情',
    'the', 'and', 'for', 'with', 'this', 'that', 'from', 'note', 'notes',
}


def read(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def collect_pages(wiki_root, only=None):
    pages = []
    for p in sorted(wiki_root.rglob('*.md')):
        if p.name in INDEX_SKIP:
            continue
        if only and p.name not in only and p.stem not in only:
            continue
        pages.append(p)
    return pages


def source_sections(text):
    """返回源文档（raw 所指 docs/X.md）的章节标题列表（去重、保序）。"""
    out = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            title = m.group(2).strip()
            if title and title not in out:
                out.append(title)
    return out


def section_terms(titles):
    """从章节标题树抽取显着术语 token（去停用词、去重、保序）。"""
    out = []
    for t in titles:
        for tok in TOKEN_RE.findall(t):
            tok = tok.strip()
            if len(tok) >= 2 and tok not in STOP_TOKENS and tok not in out:
                out.append(tok)
    return out


def normalize(s):
    return ' '.join(s.lower().split())


def coverage_for(src_page, pages, root, src_fm_lookup):
    """对单个 src 页算静态覆盖度。返回 dict 或 None（无证据则跳过）。"""
    text = read(src_page) or ''
    fm, body = parse_frontmatter(text)
    vals = raw_values(fm)
    if not vals:
        return None  # 无 raw，无法定位源文档，跳过

    evidence_texts = []
    missing = []
    for v in vals:
        f = root / v
        if f.is_file():
            evidence_texts.append(read(f) or '')
        else:
            missing.append(v)
    if not evidence_texts:
        return {'skipped': True, 'missing': missing}

    # 吸收文本 = src 页正文 ∪ 所有 [[src-xxx]] 指向它的页面正文
    target = src_page.stem  # 如 src-foo → foo
    absorb = [body]
    for p in pages:
        if p is src_page:
            continue
        pt = read(p) or ''
        linked = {m.group(1).strip() for m in WIKILINK_RE.finditer(pt)}
        if target in linked or ('src-' + target) in linked:
            _, pb = parse_frontmatter(pt)
            absorb.append(pb)
    absorb_text = normalize('\n'.join(absorb))

    titles = source_sections('\n'.join(evidence_texts))
    terms = section_terms(titles)

    sec_hit = sum(1 for t in titles if normalize(t) in absorb_text)
    term_hit = sum(1 for t in terms if normalize(t) in absorb_text)
    uncovered = [t for t in titles if normalize(t) not in absorb_text]

    rate = (sec_hit / len(titles) * 100) if titles else 0.0
    return {
        'src': src_page.name,
        'sec_total': len(titles),
        'sec_hit': sec_hit,
        'term_total': len(terms),
        'term_hit': term_hit,
        'rate': rate,
        'uncovered': uncovered,
        'missing': missing,
    }


def main(argv):
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    root = Path(argv[1] if len(argv) > 1 else '.').resolve()
    wiki_root = root / 'wiki'
    only = set(argv[2:]) or None
    if not wiki_root.is_dir():
        print('== wiki_coverage ==\nERROR: 未找到 %s' % wiki_root)
        return

    pages = collect_pages(wiki_root, only)
    src_pages = [p for p in pages if p.name.startswith('src-')]
    if not src_pages:
        print('== wiki_coverage report ==')
        print('root: %s | 未发现 src-* 页面（无覆盖对象）' % root)
        return

    print('== wiki_coverage report ==')
    print('root: %s | src 页: %d\n' % (root, len(src_pages)))

    rates = []
    for sp in src_pages:
        r = coverage_for(sp, pages, root, None)
        if r is None:
            print('[coverage] %s — 跳过（无 raw 坐标）' % sp.name)
            continue
        if r.get('skipped'):
            print('[coverage] %s — 跳过（raw 证据文件不存在: %s）'
                  % (sp.name, ', '.join(r['missing'])))
            continue
        tag = 'OK' if r['rate'] >= 80 else ('LOW' if r['rate'] >= 40 else 'GAP')
        print('[coverage] %s — 章节 %d/%d 命中, 术语 %d/%d 命中, 覆盖率 %.0f%% (%s)'
              % (sp.name, r['sec_hit'], r['sec_total'], r['term_hit'],
                 r['term_total'], r['rate'], tag))
        if r['uncovered']:
            print('    [uncovered] 未被吸收章节:')
            for t in r['uncovered']:
                print('        - %s' % t)
        rates.append(r['rate'])
        print()

    avg = (sum(rates) / len(rates)) if rates else 0.0
    print('Summary: sources=%d, coverage_avg=%.0f%%' % (len(rates), avg))
    print('提示：静态覆盖度是冷启动代理（§10.7），不等于用户体验；'
          '仅定位"源文档整段未被任何 wiki 页纳管"的结构性缺口。')


if __name__ == '__main__':
    try:
        main(sys.argv)
    except Exception as e:
        import wiki_log
        root = sys.argv[1] if len(sys.argv) > 1 else '.'
        wiki_log.dump_error(root, e)
        print('ERROR: %s' % e)
        raise SystemExit(1)
