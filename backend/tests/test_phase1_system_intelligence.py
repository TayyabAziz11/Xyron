"""
Tests for Phase 1 — System Intelligence (semantic filesystem index).

Covers:
  - content_extractor: text extraction across supported formats
  - semantic_index: FAISS embedding add/search/remove round-trip
  - fs_index: incremental upsert/rename/remove + content-worthy scoping
    + composite ranked search (semantic + recency + frequency + folder)
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_BACKEND = str(Path(__file__).parent.parent)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


class TestContentExtractor(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api.services import content_extractor
        cls.mod = content_extractor
        cls.tmp = Path(tempfile.mkdtemp(prefix="xyron_phase1_extract_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_txt_extraction(self):
        p = self.tmp / "note.txt"
        p.write_text("hello world this is a test note")
        text = self.mod.extract_text(p)
        self.assertIn("hello world", text)

    def test_unsupported_extension_returns_none(self):
        p = self.tmp / "video.mp4"
        p.write_bytes(b"\x00\x01\x02")
        self.assertIsNone(self.mod.extract_text(p))

    def test_oversized_file_skipped(self):
        p = self.tmp / "big.txt"
        with open(p, "wb") as f:
            f.seek(self.mod.MAX_EXTRACT_BYTES + 1024)
            f.write(b"\0")
        self.assertIsNone(self.mod.extract_text(p))

    def test_is_supported(self):
        self.assertTrue(self.mod.is_supported(Path("a.pdf")))
        self.assertTrue(self.mod.is_supported(Path("a.docx")))
        self.assertTrue(self.mod.is_supported(Path("a.py")))
        self.assertFalse(self.mod.is_supported(Path("a.exe")))

    def test_corrupt_pdf_does_not_raise(self):
        p = self.tmp / "broken.pdf"
        p.write_bytes(b"not a real pdf")
        self.assertIsNone(self.mod.extract_text(p))


class TestSemanticIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from api.services.semantic_index import semantic_index
        cls.idx = semantic_index
        cls.ids = [-900001, -900002, -900003]  # unlikely to collide with real rows

    def tearDown(self):
        for i in self.ids:
            self.idx.remove(i)

    def test_add_and_search_ranks_relevant_text_first(self):
        added = self.idx.add_batch([
            (self.ids[0], "invoice payment due office supplies march"),
            (self.ids[1], "quarterly tax return filing 2026"),
            (self.ids[2], "company logo brand assets export png"),
        ])
        self.assertEqual(added, 3)

        hits = dict(self.idx.search("open my tax file", k=10))
        self.assertIn(self.ids[1], hits)
        best_id = max(hits, key=hits.get)
        self.assertEqual(best_id, self.ids[1])

    def test_remove_drops_vector_from_results(self):
        self.idx.add(self.ids[0], "unique searchable marker phrase zzzqqq")
        hits_before = dict(self.idx.search("unique searchable marker phrase zzzqqq", k=5))
        self.assertIn(self.ids[0], hits_before)

        self.idx.remove(self.ids[0])
        hits_after = dict(self.idx.search("unique searchable marker phrase zzzqqq", k=5))
        self.assertNotIn(self.ids[0], hits_after)


class TestFSIndexIncremental(unittest.TestCase):
    """Exercises upsert/rename/remove against the real fs_index singleton,
    scoped to a throwaway file under a SEMANTIC_ROOT so content indexing
    actually engages."""

    @classmethod
    def setUpClass(cls):
        from api.services.fs_index import fs_index, SEMANTIC_ROOTS
        cls.fs_index = fs_index
        # Xyron repo root is always in SEMANTIC_ROOTS (see fs_index._discover_semantic_roots)
        cls.root = SEMANTIC_ROOTS[-1]
        cls.tmp_dir = cls.root / "_tmp_phase1_test_scratch"
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_is_content_worthy_true_under_semantic_root(self):
        from api.services.fs_index import is_content_worthy
        self.assertTrue(is_content_worthy(self.tmp_dir / "anything.txt"))

    def test_is_content_worthy_false_outside_semantic_root(self):
        from api.services.fs_index import is_content_worthy
        self.assertFalse(is_content_worthy(Path("/mnt/e/some_unrelated_place/file.txt")))

    def test_upsert_extracts_content_and_is_semantically_searchable(self):
        p = self.tmp_dir / "invoice_test.txt"
        p.write_text("Invoice number 4471 amount due 842 dollars payment overdue")

        entry_id = self.fs_index.upsert_single(p)
        self.assertIsNotNone(entry_id)

        import sqlite3
        conn = sqlite3.connect(str(self.fs_index._db_path))
        row = conn.execute(
            "SELECT has_content, keywords FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 1)
        self.assertIn("Invoice", row[1])

        hits = self.fs_index.search_semantic_ranked("outstanding invoice payment", limit=5)
        found_paths = [str(pp) for _, pp, _ in hits]
        self.assertIn(str(p), found_paths)

        self.fs_index.remove_path(p)

    def test_rename_path_preserves_row_and_updates_path(self):
        p = self.tmp_dir / "before_rename.txt"
        p.write_text("plain content")
        entry_id = self.fs_index.upsert_single(p)

        new_p = self.tmp_dir / "after_rename.txt"
        p.rename(new_p)
        self.fs_index.rename_path(p, new_p)

        import sqlite3
        conn = sqlite3.connect(str(self.fs_index._db_path))
        row = conn.execute("SELECT id, path FROM entries WHERE id = ?", (entry_id,)).fetchone()
        stale = conn.execute("SELECT id FROM entries WHERE path = ?", (str(p),)).fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[1], str(new_p))
        self.assertIsNone(stale)

        self.fs_index.remove_path(new_p)

    def test_remove_path_deletes_directory_and_descendants(self):
        sub = self.tmp_dir / "nested_dir"
        sub.mkdir(exist_ok=True)
        f1 = sub / "child.txt"
        f1.write_text("child content")

        self.fs_index.upsert_single(sub)
        self.fs_index.upsert_single(f1)

        self.fs_index.remove_path(sub)

        import sqlite3
        conn = sqlite3.connect(str(self.fs_index._db_path))
        remaining = conn.execute(
            "SELECT COUNT(*) FROM entries WHERE path = ? OR path LIKE ?",
            (str(sub), f"{sub}/%"),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(remaining, 0)
        shutil.rmtree(sub, ignore_errors=True)

    def test_mark_opened_increments_frequency(self):
        p = self.tmp_dir / "frequency_test.txt"
        p.write_text("content for frequency test")
        entry_id = self.fs_index.upsert_single(p)

        self.fs_index.mark_opened(p)
        self.fs_index.mark_opened(p)

        import sqlite3
        conn = sqlite3.connect(str(self.fs_index._db_path))
        row = conn.execute(
            "SELECT open_count, last_opened FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 2)
        self.assertGreater(row[1], time.time() - 10)

        self.fs_index.remove_path(p)


if __name__ == "__main__":
    unittest.main()
