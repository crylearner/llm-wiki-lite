#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机械证据核查（LLM-Wiki Lite）——report-only，永不修改任何文件。

三项扫描：
1. Fidelity（字面量保真）：从每个 wiki 页面正文提取高信号字面量
   （版本号、千分位/带后缀数字、ISO 日期、>=15 字符引语），逐字核对
   是否出现在该页证据文件（src 页 raw 所指文档）中。未命中 = 嫌疑人
   ——嫌疑人是候选不是判决：派生值、产品名、页面自身的录入日期会误报，
   语义复核与处置由 AI/人完成（机器报告，AI 判断，事实永不自动修）。
2. Drift（版本漂移）：比对 src 页 frontmatter `version` 与证据文件中
   出现的版本号集合。页面锚点不在集合中 = 源文档已升级，需复核。
3. Evidence errors：无法验证的页面——raw 缺失、文件不存在、绝对路径、
   旧格式 frontmatter（source_path 等迁移前遗留字段）。

提取范围排除：frontmatter、行内代码 `...`、围栏代码块、`> **Status:` 行。
con 页的证据文件经其正文中的 [[src-xxx]] 双链解析（src 页统一持有坐标）；
con 页若无可解析的来源页链接，记为证据错误（规则 4：无来源不得写入）。

约定：零第三方依赖（Python 3.8+ 标准库）；退出码不承载信息，报告即接口。
用法：python wiki_check.py [project-root] [页面文件名 ...]
默认 project-root = 当前目录；范围 = wiki/ 全部 .md（index.md / log.md 除外）。
"""

import re
import sys
from pathlib import Path

# ---- 字面量候选集（closed set：新增形态改这里，不改散落的正则） ----
VERSION_RE = re.compile(r'(?<![0-9A-Za-z.])V?\d+(?:\.\d+){1,3}(?![0-9])')
THOUSANDS_RE = re.compile(r'(?<![0-9])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![0-9])')
SUFFIX_RE = re.compile(r'(?<![0-9])\d+(?:\.\d+)?\s?[KMB%](?![A-Za-z0-9])')
DATE_RE = re.compile(r'(?<![0-9])\d{4}-\d{2}(?:-\d{2})?(?![0-9])')
QUOTE_RES = [re.compile(r'"([^"\n]{15,})"'), re.compile(r'\u201c([^\u201d\n]{15,})\u201d')]
MIN_QUOTE = 15

INLINE_CODE_RE = re.compile(r'`[^`\n]*`')
FENCE_RE = re.compile(r'^\s*(?:```|~~~)')
STATUS_RE = re.compile(r'^\s*>\s*\*\*Status:')
WIKILINK_RE = re.compile(r'\[\[([^\]|]+)')


def parse_frontmatter(text):
    """返回 (frontmatter dict, body)。无 frontmatter 时 ({}, text)。"""
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
    """取 raw 字段的值列表（字符串或 YAML flow 数组）。"""
    v = (fm.get('raw') or '').strip()
    if not v:
        return []
    if v.startswith('['):
        return [x.strip().strip('\'"') for x in v[1:-1].split(',') if x.strip()]
    return [v.strip('\'"')]


def is_absolute(p):
    return bool(re.match(r'^[A-Za-z]:[\\/]', p)) or p.startswith(('/', '\\')) or '://' in p


def extract_literals(body):
    """从正文提取字面量候选 [(literal, kind)]，排除代码块/行内代码/Status 行。"""
    out = []
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence or STATUS_RE.match(line):
            continue
        clean = INLINE_CODE_RE.sub(' ', line)
        for rx, kind in ((VERSION_RE, 'version'), (THOUSANDS_RE, 'number'),
                         (SUFFIX_RE, 'number'), (DATE_RE, 'date')):
            for m in rx.finditer(clean):
                out.append((m.group(0).strip(), kind))
        for rx in QUOTE_RES:
            for m in rx.finditer(clean):
                out.append((m.group(1).strip(), 'quote'))
    seen, uniq = set(), []
    for lit, kind in out:
        if lit not in seen:
            seen.add(lit)
            uniq.append((lit, kind))
    return uniq


def collect_pages(wiki_root, only=None):
    pages = []
    for p in sorted(wiki_root.rglob('*.md')):
        if p.name in ('index.md', 'log.md'):
            continue
        if only and p.name not in only and p.stem not in only:
            continue
        pages.append(p)
    return pages


def read(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


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
        print('== wiki_check ==\nERROR: 未找到 %s' % wiki_root)
        return

    pages = collect_pages(wiki_root, only)
    # 重复文件名检测（vault 唯一性：同名即歧义，规则 1 硬约束）
    stems = [p.stem for p in pages]
    dups = {s for s in stems if stems.count(s) > 1}
    # 预读全部 src 页 frontmatter，供 con 页解析 [[src-xxx]] 证据坐标
    src_fm = {}
    for p in pages:
        if p.name.startswith('src-'):
            fm, _ = parse_frontmatter(read(p) or '')
            src_fm[p.stem] = fm

    n_lit = n_sus = n_drift = n_err = 0
    print('== wiki_check report ==')
    print('root: %s | pages: %d\n' % (root, len(pages)))

    for p in pages:
        text = read(p) or ''
        fm, body = parse_frontmatter(text)

        # ---- vault 唯一性 ----
        if p.stem in dups:
            n_err += 1
            print('[evidence] %s — ERROR: 文件名重复（%s），违反 vault 唯一性，双链歧义'
                  % (p.name, p.stem))
            continue

        # ---- 证据文件解析 ----
        evidence_files, errors = [], []
        if p.name.startswith('src-'):
            if 'source_path' in fm and 'raw' not in fm:
                errors.append('旧格式 frontmatter（source_path），待迁移为 raw')
            vals = raw_values(fm)
            if not vals and not errors:
                errors.append('frontmatter 缺 raw 字段（规则 4）')
            for v in vals:
                if is_absolute(v):
                    errors.append('raw 为绝对/外部路径，违反坐标化铁律: %s' % v)
                    continue
                f = root / v
                if f.is_file():
                    evidence_files.append(f)
                else:
                    errors.append('证据文件不存在: %s' % v)
        elif p.name.startswith('dec-'):
            # dec 页：raw 可选（决策依据文档）；有则校验，无则不要求
            for v in raw_values(fm):
                if is_absolute(v):
                    errors.append('raw 为绝对/外部路径，违反坐标化铁律: %s' % v)
                else:
                    f = root / v
                    if f.is_file():
                        evidence_files.append(f)
                    else:
                        errors.append('证据文件不存在: %s' % v)
        else:
            links = [m.group(1).strip() for m in WIKILINK_RE.finditer(body)]
            src_links = [l for l in links if l.startswith('src-')]
            if not src_links:
                errors.append('正文无 [[src-xxx]] 来源链接（规则 4）')
            for l in set(src_links):
                if l not in src_fm:
                    errors.append('来源页不存在: [[%s]]' % l)
                elif not raw_values(src_fm[l]):
                    errors.append('来源页 [[%s]] 无 raw 坐标（先修复该 src 页）' % l)
                else:
                    for v in raw_values(src_fm[l]):
                        if not is_absolute(v) and (root / v).is_file():
                            evidence_files.append(root / v)

        # ---- Fidelity ----
        if evidence_files:
            src_text = '\n'.join(read(f) or '' for f in evidence_files)
            lits = extract_literals(body)
            suspects = [(l, k) for l, k in lits if l not in src_text]
            n_lit += len(lits)
            n_sus += len(suspects)
            if lits:
                tag = 'OK' if not suspects else 'SUSPECT'
                print('[fidelity] %s — %d literals, %d suspect (%s)'
                      % (p.name, len(lits), len(suspects), tag))
                for l, k in suspects:
                    print('    SUSPECT (%s): %r' % (k, l))
        else:
            print('[fidelity] %s — 跳过（无证据文件）' % p.name)

        # ---- Drift（仅 src 页） ----
        if p.name.startswith('src-') and fm.get('version'):
            pv = fm['version'].strip()
            if evidence_files:
                src_text = '\n'.join(read(f) or '' for f in evidence_files)
                found = sorted(set(VERSION_RE.findall(src_text)))
                if pv in found:
                    print('[drift] %s — 页面 %s ∈ 源版本集合 OK' % (p.name, pv))
                elif not found:
                    print('[drift] %s — 源文档无版本号，漂移不可检测（降级）' % p.name)
                else:
                    n_drift += 1
                    print('[drift] %s — DRIFT: 页面锚点 %s，源含 %s → 需复核'
                          % (p.name, pv, '/'.join(found)))
            elif not errors:
                print('[drift] %s — 跳过（无证据文件）' % p.name)

        # ---- Evidence errors ----
        for e in errors:
            n_err += 1
            print('[evidence] %s — ERROR: %s' % (p.name, e))
        print()

    print('Summary: pages=%d, literals=%d, suspects=%d, drift=%d, evidence_errors=%d'
          % (len(pages), n_lit, n_sus, n_drift, n_err))
    print('提示：suspects 是候选不是判决（派生值/产品名/页面录入日期会误报）；'
          'drift 复核后按 Status 块处置；evidence_errors 需修复或迁移。')


if __name__ == '__main__':
    main(sys.argv)
