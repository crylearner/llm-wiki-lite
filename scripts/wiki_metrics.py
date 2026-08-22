#!/usr/bin/env python3
"""Knowledge-base quality metrics — demand-side, zero LLM (LLM-Wiki §10).

This is the consumption end of the §10.3 "tool-call DVR". obsidian-mcp-pro
emits one JSON object per tool call (see its src/lib/monitor.ts, gated by the
OBSIDIAN_TELEMETRY env var). THIS script does the wiki-domain work that the
server deliberately does NOT do:

  · §10.4  query grouping   — collapse the raw call stream into "queries"
  · §10.3  demand dimensions 4–8  — recall rate, failure rate, hit rate,
                                 path length, refine rate (behavioral stats)
  · §10.5  per-source aggregation  — roll signals up to each raw source doc,
                                 producing a sorted refine-priority list
  · §10.6  closed-loop compare   — baseline vs current snapshot diff

Nothing here asks a model to self-report. Every number is a deterministic
aggregate of the captured call stream. Per §10.1 we do NOT emit a "quality
score"; we emit behavioral counts and a priority queue.

Usage:
  # live from a telemetry file produced by obsidian-mcp-pro
  python wiki_metrics.py --events telemetry.jsonl --vault /path/to/vault

  # closed-loop compare: refine before vs after
  python wiki_metrics.py --events now.jsonl --vault /vault \
      --baseline before.jsonl --report compare

Input events (one JSON per line), emitted by monitor.ts:
  {ts, tool, vault, path?, query?, isError, contentItems, primaryTextBytes, durationMs}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse frontmatter parsing already validated by wiki_check.py so the `raw`
# coordinate resolution (§10.4 dim-4 enhancement, §10.5) stays in one place.
try:
    from wiki_check import parse_frontmatter, raw_values, collect_pages
except ImportError:  # allow running from outside the scripts/ dir
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from wiki_check import parse_frontmatter, raw_values, collect_pages

# --------------------------------------------------------------------------
# Classification (§10.4 layer-1: action classification by tool name, zero LLM)
# --------------------------------------------------------------------------

# Tools whose call is a "query" action. Everything else (ingest/lint/write)
# is treated as a boundary that cuts the current query.
QUERY_TOOL_RE = re.compile(r"^(get_|search_|list_|read_|find_)")
# Tools that are search-type, used for the retrieval hit-rate proxy (dim 6).
SEARCH_TOOL_RE = re.compile(r"^search_")
# Tools that are refine-type (writing/fixing wiki content), used for dim 8.
REFINE_TOOL_RE = re.compile(r"^(create_|update_|append_|prepend_|insert_|edit_|replace_|move_|delete_|add_)")


def is_query_tool(tool: str) -> bool:
    return bool(QUERY_TOOL_RE.match(tool))


def is_search_tool(tool: str) -> bool:
    return bool(SEARCH_TOOL_RE.match(tool))


def is_refine_tool(tool: str) -> bool:
    return bool(REFINE_TOOL_RE.match(tool))


# §10.7 honest boundary: a search returning a very short single text block is a
# high-probability "no results" reply. This is a *proxy* on primaryTextBytes
# (monitor.ts intentionally ships no result text). Surfaced as such in the
# report and tunable via --miss-bytes.
def search_is_miss(event: dict, miss_bytes: int) -> bool:
    if event.get("isError"):
        return True
    if is_search_tool(event.get("tool", "")):
        return event.get("contentItems", 0) == 1 and event.get(
            "primaryTextBytes", 9999
        ) < miss_bytes
    return False


# --------------------------------------------------------------------------
# §10.7 path funnel classification (dim 7) — wiki-domain, lives here not in mcp
# --------------------------------------------------------------------------

def path_class(path: str | None) -> str:
    """Map a vault-relative path to a funnel layer (§10.7 / §10.3 dim 7)."""
    if not path:
        return "unknown"
    p = path.replace("\\", "/")
    if p == "wiki/index.md" or p.endswith("/index.md"):
        return "entry"
    if "wiki/concepts/" in p or "wiki/syntheses/" in p:
        return "hub"
    if "wiki/sources/" in p:
        return "source"
    if "docs/" in p:
        return "raw"
    if p.startswith("wiki/"):
        return "wiki-other"
    return "other"


# --------------------------------------------------------------------------
# §10.4 query grouping — stateless three-signal heuristic
# --------------------------------------------------------------------------

def group_queries(events: list[dict], gap_seconds: int = 600) -> list[list[dict]]:
    """Collapse the raw call stream into query units.

    Heuristics (all zero LLM, per §10.4):
      1. action classification — a non-query tool (ingest/lint/refine) cuts.
      2. time gap — two adjacent calls > gap_seconds apart start a new query.
      3. path coherence — a jump to an unrelated topic (different top folder)
         after a gap suggests a new target.
    """
    queries: list[list[dict]] = []
    current: list[dict] = []

    def top_folder(p: str | None) -> str:
        if not p:
            return ""
        return p.replace("\\", "/").split("/", 1)[0]

    for ev in events:
        tool = ev.get("tool", "")
        # An ingest/lint call is an explicit boundary (layer-1).
        if not is_query_tool(tool):
            if current:
                queries.append(current)
                current = []
            continue

        if current:
            prev = current[-1]
            # Time gap cut.
            if _seconds_between(prev, ev) > gap_seconds:
                queries.append(current)
                current = []
            # Path coherence cut: both have paths, and the top folder jumps
            # (e.g. from a docs/ deep dive to a different wiki branch).
            elif (
                prev.get("path")
                and ev.get("path")
                and top_folder(prev["path"]) != top_folder(ev["path"])
                and _seconds_between(prev, ev) > gap_seconds / 2
            ):
                queries.append(current)
                current = []

        current.append(ev)

    if current:
        queries.append(current)
    return queries


def _seconds_between(a: dict, b: dict) -> float:
    try:
        ta = datetime.fromisoformat(a["ts"].replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b["ts"].replace("Z", "+00:00"))
        return (tb - ta).total_seconds()
    except Exception:
        return 0.0


# --------------------------------------------------------------------------
# §10.3 demand dimensions + §10.4 dim-4 enhancement (associated raw recall)
# --------------------------------------------------------------------------

def compute_dimensions(
    queries: list[list[dict]], miss_bytes: int, src_to_raw: dict[str, list[str]]
) -> dict:
    total_queries = len(queries)
    recall_total = 0  # raw-source reads inside queries
    wiki_consumed = 0  # non-raw reads inside queries
    failures = 0
    search_calls = 0
    search_misses = 0
    path_lengths: list[int] = []
    funnel = defaultdict(int)  # layer -> call count
    failure_by_prefix = defaultdict(int)  # path prefix -> failure count (dim 5)
    missed_queries: list[str] = []  # dim 6 candidate topics
    associated_recall = 0  # dim-4 enhancement: read src-X then its raw
    direct_raw_no_wiki = 0  # low-confidence: read docs without reading wiki first

    for q in queries:
        qlen = 0
        raw_read_in_q = False
        for ev in q:
            tool = ev.get("tool", "")
            p = (ev.get("path") or "").replace("\\", "/")
            pclass = path_class(p)
            funnel[pclass] += 1
            if tool in ("get_note", "read_note", "get_file_contents"):
                # count reads as either raw recall or wiki consumption
                if pclass == "raw":
                    recall_total += 1
                    raw_read_in_q = True
                else:
                    wiki_consumed += 1
            if ev.get("isError"):
                failures += 1
                prefix = p.split("/", 1)[0] if p else "(no-path)"
                failure_by_prefix[prefix] += 1
            if is_search_tool(tool):
                search_calls += 1
                if search_is_miss(ev, miss_bytes):
                    search_misses += 1
                    if ev.get("query"):
                        missed_queries.append(ev["query"])
            qlen += 1
        path_lengths.append(qlen)

        # §10.4 dim-4 enhancement (associated raw recall), zero LLM via the
        # frontmatter `raw` coordinate: within this query, did we read a
        # wiki/sources/src-X page and THEN read one of its declared raw docs?
        saw_source_raw_targets: set[str] = set()
        saw_any_source_page = False
        for ev in q:
            p = (ev.get("path") or "").replace("\\", "/")
            pclass = path_class(p)
            if pclass == "source":
                saw_any_source_page = True
                for src, raws in src_to_raw.items():
                    if src == p:
                        saw_source_raw_targets.update(raws)
            elif pclass == "raw" and saw_source_raw_targets:
                if p in saw_source_raw_targets:
                    associated_recall += 1
                    break
        # Low-confidence branch (§10.4): read a docs/ file with NO source page
        # read first in this query -> user asked for the original directly,
        # NOT counted as a wiki gap.
        if not saw_any_source_page and raw_read_in_q:
            direct_raw_no_wiki += 1

    # §10.3 dim 4: recall dependency = raw reads / (raw reads + wiki consumption)
    denom = recall_total + wiki_consumed
    recall_rate = (recall_total / denom) if denom else 0.0
    # dim 5: failure rate + per-prefix distribution
    total_calls = sum(len(q) for q in queries)
    failure_rate = (failures / total_calls) if total_calls else 0.0
    # dim 6: retrieval hit rate
    hit_rate = (1 - search_misses / search_calls) if search_calls else None
    # dim 7: avg path length
    avg_len = (sum(path_lengths) / len(path_lengths)) if path_lengths else 0.0

    return {
        "total_queries": total_queries,
        "total_calls": total_calls,
        "recall_rate": round(recall_rate, 3),
        "recall_reads": recall_total,
        "wiki_consumed": wiki_consumed,
        "associated_recall": associated_recall,
        "direct_raw_no_wiki": direct_raw_no_wiki,
        "failure_rate": round(failure_rate, 3),
        "failures": failures,
        "failure_by_prefix": dict(failure_by_prefix),
        "search_calls": search_calls,
        "search_misses": search_misses,
        "missed_queries": missed_queries,
        "hit_rate": (round(hit_rate, 3) if hit_rate is not None else None),
        "avg_path_length": round(avg_len, 2),
        "funnel": dict(funnel),
    }


# --------------------------------------------------------------------------
# §10.8 refine rate (dim 8) — needs the full (ungrouped) stream
# --------------------------------------------------------------------------

def compute_refine_rate(events: list[dict]) -> dict:
    """'query -> immediately-followed refine' adjacent action-pair ratio."""
    refine_pairs = 0
    query_count = 0
    for i, ev in enumerate(events):
        if not is_query_tool(ev.get("tool", "")):
            continue
        query_count += 1
        if i + 1 < len(events) and is_refine_tool(events[i + 1].get("tool", "")):
            refine_pairs += 1
    rate = (refine_pairs / query_count) if query_count else None
    return {
        "query_actions": query_count,
        "refine_following": refine_pairs,
        "refine_rate": (round(rate, 3) if rate is not None else None),
    }


# --------------------------------------------------------------------------
# §10.5 per-source aggregation via raw coordinate
# --------------------------------------------------------------------------

def build_src_to_raw(vault: Path) -> dict[str, list[str]]:
    """Map each wiki/sources/src-X.md (vault-relative) to its `raw` doc paths.

    This is the §10.4 dim-4 / §10.5 coordinate join: the frontmatter `raw`
    field on a source page declares which original document(s) it summarizes.
    """
    src_to_raw: dict[str, list[str]] = {}
    sources_dir = vault / "wiki" / "sources"
    if sources_dir.is_dir():
        for f in sources_dir.glob("*.md"):
            try:
                # utf-8-sig strips a leading BOM if the file was written by a
                # Windows editor that prepends one; otherwise parse_frontmatter's
                # startswith('---') guard would silently miss the frontmatter.
                front, _body = parse_frontmatter(
                    f.read_text(encoding="utf-8-sig")
                )
            except Exception:
                continue
            raws = raw_values(front)
            if raws:
                src_to_raw[str(f.relative_to(vault)).replace("\\", "/")] = [
                    r.replace("\\", "/") for r in raws
                ]
    return src_to_raw


def aggregate_by_source(
    queries: list[list[dict]], vault: Path, miss_bytes: int
) -> list[dict]:
    """Roll §10.3 signals up to each source doc using frontmatter `raw`."""
    src_to_raw = build_src_to_raw(vault)

    # raw doc path -> aggregated signals
    agg: dict[str, dict] = defaultdict(
        lambda: {
            "associated_recall": 0,
            "failures": 0,
            "refined": 0,
            "search_empty": 0,
        }
    )

    # Walk every call (not just grouped) to attribute refine/failure/search.
    # We attribute to a source doc when a call's path is that doc's raw file,
    # OR when a call's path is the wiki/sources/src-X page itself.
    def attribute(raw_path: str, key: str, inc: int = 1):
        # direct: the raw path itself
        if raw_path in agg:
            agg[raw_path][key] += inc
        # reverse: which src pages point at this raw path?
        for src, raws in src_to_raw.items():
            if raw_path in raws:
                agg.setdefault(raw_path, agg[raw_path])

    # dim-4 enhancement again, but attributed per source via raw coordinate
    for q in queries:
        saw_src_raw_target: set[str] = set()
        saw_source_page = False
        for ev in q:
            p = (ev.get("path") or "").replace("\\", "/")
            pclass = path_class(p)
            if pclass == "source":
                saw_source_page = True
                # the source page's own raw targets become candidates
                for src, raws in src_to_raw.items():
                    if src == p:
                        saw_src_raw_target.update(raws)
            elif pclass == "raw" and saw_source_page:
                for r in saw_src_raw_target:
                    if p == r:
                        agg[r]["associated_recall"] += 1

    # failures + search-empty attributed directly to raw paths seen in calls
    for q in queries:
        for ev in q:
            p = (ev.get("path") or "").replace("\\", "/")
            if not p.startswith("docs/"):
                continue
            if ev.get("isError"):
                agg[p]["failures"] += 1
            if search_is_miss(ev, miss_bytes) and is_search_tool(
                ev.get("tool", "")
            ):
                agg[p]["search_empty"] += 1

    # refine attribution: a refine tool call whose path is within docs/ or
    # points at a raw doc counts toward that doc.
    for q in queries:
        for ev in q:
            if not is_refine_tool(ev.get("tool", "")):
                continue
            p = (ev.get("path") or "").replace("\\", "/")
            for raw_path in agg:
                if p == raw_path or p.startswith(raw_path.rsplit("/", 1)[0] + "/"):
                    agg[raw_path]["refined"] += 1

    # §10.5 "expression-layer insufficiency score" = summed signals; sort desc.
    rows = []
    for raw_path, sig in agg.items():
        score = (
            sig["associated_recall"] * 3
            + sig["failures"] * 2
            + sig["search_empty"] * 2
            + sig["refined"] * 1
        )
        rows.append(
            {
                "source_doc": raw_path,
                "insufficiency_score": score,
                "associated_recall": sig["associated_recall"],
                "failures": sig["failures"],
                "search_empty": sig["search_empty"],
                "refined": sig["refined"],
            }
        )
    rows.sort(key=lambda r: r["insufficiency_score"], reverse=True)
    return rows


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    # utf-8-sig tolerates a leading BOM so a BOM-prefixed telemetry file still
    # parses (a real-world gotcha when logs are produced on Windows).
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# --------------------------------------------------------------------------
# §10.5 gap-to-action: turn missed queries into "what to ingest" (zero LLM).
# A missed search query is only useful if we tell the operator WHETHER the
# knowledge already exists (stale-search: fix index/aliases) or is truly
# missing (missing: ingest a new topic). We classify by substring-matching
# the normalized query against every wiki page's body+title.
# --------------------------------------------------------------------------

_GAP_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def build_knowledge_gaps(
    vault: Path, missed_queries: list[str], top_n: int = 15
) -> list[dict]:
    """Classify missed search queries as MISSING vs STALE_SEARCH.

    Returns a frequency-sorted list (desc) of:
      {query, count, kind, matched_pages}
    kind == "MISSING"      -> not found anywhere in the wiki -> ingest candidate
    kind == "STALE_SEARCH" -> present in some page body/title -> fix retrieval
    """
    wiki_root = vault / "wiki"
    page_texts: list[str] = []
    if wiki_root.is_dir():
        for p in collect_pages(wiki_root):
            t = p.read_text(encoding="utf-8-sig", errors="replace") or ""
            _, body = parse_frontmatter(t)
            page_texts.append(_normalize(p.stem + " " + body))
    corpus = "\n".join(page_texts)

    freq: dict[str, int] = {}
    for q in missed_queries:
        q = (q or "").strip()
        if q:
            freq[q] = freq.get(q, 0) + 1

    gaps: list[dict] = []
    for q, count in freq.items():
        nq = _normalize(q)
        # a query is "present" if any of its significant tokens AND the query
        # string itself appear in the corpus. Require either the full query
        # string OR >=2 significant tokens to avoid false "present" on a single
        # generic token.
        tokens = [t for t in _GAP_TOKEN_RE.findall(nq)]
        present = nq in corpus or sum(1 for t in tokens if t in corpus) >= 2
        if present:
            matched = [
                p.stem
                for p in (collect_pages(wiki_root) if wiki_root.is_dir() else [])
                if nq in _normalize(
                    p.stem + " "
                    + parse_frontmatter(
                        p.read_text(encoding="utf-8-sig", errors="replace") or ""
                    )[1]
                )
            ]
            gaps.append(
                {
                    "query": q,
                    "count": count,
                    "kind": "STALE_SEARCH",
                    "matched_pages": matched[:3],
                }
            )
        else:
            gaps.append(
                {"query": q, "count": count, "kind": "MISSING", "matched_pages": []}
            )

    gaps.sort(key=lambda g: (g["kind"] != "MISSING", g["count"]), reverse=True)
    return gaps[:top_n]


def print_gaps(gaps: list[dict]) -> None:
    print("=== Knowledge gaps (actionable, §10.5) ===")
    if not gaps:
        print("(no missed queries -> no gap signal)")
        return
    n_missing = sum(1 for g in gaps if g["kind"] == "MISSING")
    print(f"{len(gaps)} missed-query topics ({n_missing} likely MISSING / "
          f"to ingest, rest stale-search to fix retrieval)")
    for g in gaps:
        if g["kind"] == "MISSING":
            print(f"  [INGEST] x{g['count']:>2}  {g['query']}")
        else:
            mp = ", ".join(g["matched_pages"]) or "?"
            print(f"  [FIX-INDEX] x{g['count']:>2}  {g['query']}  ~ {mp}")


def print_report(dims: dict, refine: dict, by_source: list[dict]) -> None:
    print("=== LLM-Wiki demand-side metrics (§10.3) ===")
    print(f"queries={dims['total_queries']}  calls={dims['total_calls']}")
    print(
        f"[4] recall dependency   : {dims['recall_rate']} "
        f"(raw reads={dims['recall_reads']}, wiki consumption={dims['wiki_consumed']}, "
        f"associated-recall signal={dims['associated_recall']}, "
        f"direct-raw-lowconf={dims['direct_raw_no_wiki']})"
    )
    print(
        f"[5] failure/blocked rate: {dims['failure_rate']} ({dims['failures']} failures)"
    )
    if dims["failure_by_prefix"]:
        dist = ", ".join(
            f"{k}={v}" for k, v in sorted(dims["failure_by_prefix"].items())
        )
        print(f"    by path prefix      : {dist}")
    hit = dims["hit_rate"]
    print(
        f"[6] retrieval hit rate  : {hit if hit is not None else 'n/a'} "
        f"(search calls={dims['search_calls']}, misses={dims['search_misses']})"
    )
    if dims["missed_queries"]:
        print(
            "    missed-query clusters (candidate topics to build): "
            + "; ".join(sorted(set(dims["missed_queries"])))
        )
    print(
        f"[7] avg path length     : {dims['avg_path_length']}  funnel={dims['funnel']}"
    )
    print(
        f"[8] refine rate         : {refine['refine_rate']} "
        f"({refine['refine_following']}/{refine['query_actions']} query actions)"
    )
    print()
    print("=== Per-source aggregation (§10.5) — refine priority ===")
    if not by_source:
        print("(no raw-source-attributed signals captured)")
    for r in by_source:
        print(
            f"  {r['insufficiency_score']:>3}  {r['source_doc']}  "
            f"recall={r['associated_recall']} fail={r['failures']} "
            f"search_empty={r['search_empty']} refined={r['refined']}"
        )
    print()
    print(
        "Honest boundary (§10.7): only obsidian-mcp-pro calls are recorded; "
        "UI/manual queries and the retrieval-miss proxy (short search replies) "
        "are approximate. Numbers are behavioral, not a quality score."
    )


def compare_reports(before: dict, after: dict) -> None:
    print("=== Closed-loop compare (§10.6) ===")
    for key in ("recall_rate", "failure_rate", "hit_rate", "avg_path_length"):
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            continue
        delta = round(a - b, 3)
        arrow = "down" if delta < 0 else ("up" if delta > 0 else "flat")
        print(f"  {key}: {b} -> {a} ({arrow})")
    print("  (recall/failure drop after refine = improvement effective)")


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM-Wiki §10 demand-side metrics")
    ap.add_argument("--events", required=True, type=Path, help="telemetry JSONL")
    ap.add_argument("--vault", required=True, type=Path, help="vault root path")
    ap.add_argument(
        "--vault-filter",
        type=str,
        default=None,
        help="(multi-vault) keep only events whose `vault` equals this path; "
        "applied BEFORE query grouping so calls from other vaults are never "
        "merged into this vault's queries. Defaults to --vault when omitted.",
    )
    ap.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="baseline telemetry JSONL for §10.6 comparison",
    )
    ap.add_argument("--report", choices=["full", "compare"], default="full")
    ap.add_argument(
        "--miss-bytes",
        type=int,
        default=80,
        help="search-miss proxy: single block shorter than this = likely empty",
    )
    ap.add_argument("--gap-seconds", type=int, default=600, help="§10.4 time gap")
    args = ap.parse_args()

    if not args.events.is_file():
        print(f"events file not found: {args.events}", file=sys.stderr)
        return 2

    # Multi-vault isolation: default the filter to the same root we aggregate
    # against, and apply it BEFORE grouping so cross-vault calls never merge.
    vault_key = str(args.vault_filter or args.vault).replace("\\", "/")

    def filter_vault(evs: list[dict]) -> list[dict]:
        return [e for e in evs if e.get("vault", "").replace("\\", "/") == vault_key]

    events = filter_vault(load_events(args.events))
    src_to_raw = build_src_to_raw(args.vault)
    queries = group_queries(events, gap_seconds=args.gap_seconds)
    dims = compute_dimensions(queries, args.miss_bytes, src_to_raw)
    refine = compute_refine_rate(events)
    by_source = aggregate_by_source(queries, args.vault, args.miss_bytes)

    if args.report == "compare" and args.baseline and args.baseline.is_file():
        bevents = filter_vault(load_events(args.baseline))
        bqueries = group_queries(bevents, gap_seconds=args.gap_seconds)
        bdims = compute_dimensions(bqueries, args.miss_bytes, src_to_raw)
        compare_reports(bdims, dims)
        return 0

    print_report(dims, refine, by_source)
    gaps = build_knowledge_gaps(args.vault, dims["missed_queries"])
    print()
    print_gaps(gaps)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        import wiki_log
        wiki_log.dump_error('.', e)
        print('ERROR: %s' % e)
        raise SystemExit(1)
