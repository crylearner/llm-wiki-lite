#!/usr/bin/env python3
"""Tests for wiki_metrics.py (LLM-Wiki §10 demand-side metrics).

Stdlib-only (unittest) so it runs anywhere with Python 3.8+, matching the
skill's zero-third-party-dependency rule:

    python -m unittest test_wiki_metrics.py -v

Each test builds a throwaway vault (with real frontmatter `raw`) and a small
telemetry stream, then asserts the §10 dimensions compute as specified. No LLM,
no network, no obsidian-mcp-pro process required.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import wiki_metrics as w


def ev(**kw) -> dict:
    """Build a telemetry event with sensible defaults."""
    base = {
        "ts": "2026-08-22T10:00:00Z",
        "tool": "get_note",
        "vault": "V",
        "isError": False,
        "contentItems": 1,
        "primaryTextBytes": 100,
        "durationMs": 5,
    }
    base.update(kw)
    return base


def make_vault(root: Path, srcs: dict[str, list[str]]) -> None:
    """Create wiki/sources/src-X.md pages with `raw` frontmatter."""
    sources = root / "wiki" / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    for name, raws in srcs.items():
        raw_field = "[" + ", ".join(raws) + "]" if raws else ""
        (sources / name).write_text(
            f"---\nraw: {raw_field}\n---\n# {name}\n", encoding="utf-8"
        )
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)


class PathClassTest(unittest.TestCase):
    def test_layers(self):
        self.assertEqual(w.path_class("wiki/index.md"), "entry")
        self.assertEqual(w.path_class("wiki/concepts/c.md"), "hub")
        self.assertEqual(w.path_class("wiki/syntheses/s.md"), "hub")
        self.assertEqual(w.path_class("wiki/sources/src-1.md"), "source")
        self.assertEqual(w.path_class("docs/a/b.md"), "raw")
        self.assertEqual(w.path_class("wiki/other.md"), "wiki-other")
        self.assertEqual(w.path_class(None), "unknown")


class GroupQueriesTest(unittest.TestCase):
    def test_ingest_cuts_query(self):
        events = [
            ev(tool="search_notes", query="x"),
            ev(tool="get_note", path="wiki/sources/src-1.md"),
            ev(tool="ingest_document"),  # non-query -> boundary
            ev(tool="search_notes", query="y"),
        ]
        qs = w.group_queries(events)
        self.assertEqual(len(qs), 2)

    def test_time_gap_cuts(self):
        e1 = ev(tool="get_note", path="wiki/sources/src-1.md",
                ts="2026-08-22T10:00:00Z")
        e2 = ev(tool="get_note", path="docs/a.md",
                ts="2026-08-22T11:00:00Z")  # 1h later
        qs = w.group_queries([e1, e2], gap_seconds=600)
        self.assertEqual(len(qs), 2)


class DimensionsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        make_vault(self.root, {"src-1.md": ["docs/rec/debug.md"]})

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _run(self, events):
        qs = w.group_queries(events)
        src_to_raw = w.build_src_to_raw(self.root)
        return w.compute_dimensions(qs, 80, src_to_raw), qs

    def test_recall_rate_and_associated_recall(self):
        # read src-1 (source) then its raw doc -> high-confidence associated recall
        events = [
            ev(tool="get_note", path="wiki/sources/src-1.md"),
            ev(tool="get_note", path="docs/rec/debug.md"),
        ]
        dims, _ = self._run(events)
        self.assertEqual(dims["recall_reads"], 1)  # one raw read
        self.assertEqual(dims["wiki_consumed"], 1)  # one source read
        self.assertAlmostEqual(dims["recall_rate"], 0.5)
        self.assertEqual(dims["associated_recall"], 1)

    def test_direct_raw_low_confidence(self):
        # read docs/ directly with NO source page read first -> low confidence
        events = [ev(tool="get_note", path="docs/rec/debug.md")]
        dims, _ = self._run(events)
        self.assertEqual(dims["recall_reads"], 1)
        self.assertEqual(dims["direct_raw_no_wiki"], 1)
        self.assertEqual(dims["associated_recall"], 0)

    def test_failure_rate_and_prefix(self):
        events = [
            ev(tool="get_note", path="docs/broken.md", isError=True),
            ev(tool="get_note", path="docs/broken.md", isError=True),
            ev(tool="get_note", path="wiki/sources/src-1.md"),
        ]
        dims, _ = self._run(events)
        self.assertEqual(dims["failures"], 2)
        self.assertAlmostEqual(dims["failure_rate"], 2 / 3, places=2)
        self.assertEqual(dims["failure_by_prefix"].get("docs"), 2)

    def test_retrieval_hit_rate_and_missed_clusters(self):
        events = [
            # hit: long reply
            ev(tool="search_notes", query="found", primaryTextBytes=300),
            # miss: short reply (proxy)
            ev(tool="search_notes", query="zzz-missing", primaryTextBytes=40),
        ]
        dims, _ = self._run(events)
        self.assertEqual(dims["search_calls"], 2)
        self.assertEqual(dims["search_misses"], 1)
        self.assertAlmostEqual(dims["hit_rate"], 0.5)
        self.assertIn("zzz-missing", dims["missed_queries"])

    def test_path_length_and_funnel(self):
        events = [
            ev(tool="get_note", path="wiki/index.md"),
            ev(tool="get_note", path="wiki/sources/src-1.md"),
            ev(tool="get_note", path="docs/rec/debug.md"),
        ]
        dims, _ = self._run(events)
        self.assertEqual(dims["avg_path_length"], 3.0)
        self.assertEqual(dims["funnel"].get("entry"), 1)
        self.assertEqual(dims["funnel"].get("source"), 1)
        self.assertEqual(dims["funnel"].get("raw"), 1)


class RefineRateTest(unittest.TestCase):
    def test_refine_following_query(self):
        events = [
            ev(tool="search_notes", query="x"),
            ev(tool="update_note", path="docs/rec/debug.md"),  # refine follows
            ev(tool="search_notes", query="y"),  # no refine follows
        ]
        r = w.compute_refine_rate(events)
        self.assertEqual(r["query_actions"], 2)
        self.assertEqual(r["refine_following"], 1)
        self.assertAlmostEqual(r["refine_rate"], 0.5)


class AggregateBySourceTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        make_vault(self.root, {"src-1.md": ["docs/rec/debug.md"]})

    def tearDown(self):
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_join_via_raw_coordinate(self):
        events = [
            ev(tool="get_note", path="wiki/sources/src-1.md"),
            ev(tool="get_note", path="docs/rec/debug.md"),
            ev(tool="get_note", path="docs/other.md", isError=True),
        ]
        qs = w.group_queries(events)
        rows = w.aggregate_by_source(qs, self.root, 80)
        by_doc = {r["source_doc"]: r for r in rows}
        # docs/rec/debug.md joined from src-1's raw coordinate
        self.assertIn("docs/rec/debug.md", by_doc)
        self.assertEqual(by_doc["docs/rec/debug.md"]["associated_recall"], 1)
        # docs/other.md attributed by direct failure
        self.assertIn("docs/other.md", by_doc)
        self.assertEqual(by_doc["docs/other.md"]["failures"], 1)
        # sorted by insufficiency score desc
        scores = [r["insufficiency_score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))


class VaultFilterTest(unittest.TestCase):
    def test_filter_isolates_vaults_before_grouping(self):
        events = [
            ev(vault="C:/A", tool="get_note", path="wiki/sources/src-1.md"),
            ev(vault="C:/A", tool="get_note", path="docs/x.md"),
            ev(vault="C:/B", tool="get_note", path="docs/y.md", isError=True),
        ]
        filtered = [e for e in events if e.get("vault") == "C:/A"]
        qs = w.group_queries(filtered)
        dims = w.compute_dimensions(qs, 80, {})
        self.assertEqual(dims["total_calls"], 2)
        self.assertEqual(dims["failures"], 0)


class LoadEventsRobustnessTest(unittest.TestCase):
    def test_bom_and_blank_lines(self):
        # Open with utf-8-sig so the file is written with a leading BOM, as a
        # Windows editor would produce; load_events must still parse it.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write(json.dumps(ev(query="a")) + "\n")
            f.write("\n")  # blank line ignored
            f.write(json.dumps(ev(query="b")) + "\n")
            path = f.name
        try:
            events = w.load_events(Path(path))
            self.assertEqual(len(events), 2)
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
