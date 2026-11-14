"""Pruebas optativas contra una base de datos de pruebas con pgvector."""

import os
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.ingestion import DocumentChunk
from rag.store import initialize, replace_document, search


@unittest.skipUnless(os.getenv("RAG_TEST_DATABASE_URL"), "requiere RAG_TEST_DATABASE_URL")
class StoreIntegrationTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(os.environ["RAG_TEST_DATABASE_URL"], autocommit=True)
        initialize(self.conn)
        self.model = "test-" + str(uuid.uuid4())

    def tearDown(self):
        self.conn.execute("DELETE FROM rag_chunks WHERE model = %s", (self.model,))
        self.conn.execute("DELETE FROM rag_models WHERE model = %s", (self.model,))
        self.conn.close()

    def test_insert_search_replace_and_dimension_guard(self):
        chunks = [DocumentChunk("doc", 0, "uno"), DocumentChunk("doc", 1, "dos")]
        replace_document(self.conn, self.model, "doc", chunks, [[1, 0], [0, 1]])
        results = search(self.conn, self.model, [0, 1])
        self.assertEqual([r.chunk.text for r in results], ["dos", "uno"])
        self.assertAlmostEqual(results[0].score, 1)
        with self.assertRaises(ValueError):
            replace_document(self.conn, self.model, "doc", chunks[:1], [[1, 0, 0]])
        self.assertEqual(len(search(self.conn, self.model, [1, 0])), 2)
        replace_document(self.conn, self.model, "doc", chunks[:1], [[1, 0]])
        self.assertEqual([r.chunk.text for r in search(self.conn, self.model, [1, 0])], ["uno"])
        replace_document(self.conn, self.model, "doc", [], [])
        self.assertEqual(search(self.conn, self.model, [1, 0]), [])

    def test_prefix_isolation_and_concurrent_replacement(self):
        import psycopg
        replace_document(self.conn, self.model, "api:doc", [DocumentChunk("api:doc", 0, "old")], [[1, 0]])
        replace_document(self.conn, self.model, "local", [DocumentChunk("local", 0, "private")], [[1, 0]])
        self.assertEqual([r.chunk.source for r in search(self.conn, self.model, [1, 0], source_prefix="api:")], ["api:doc"])

        def replace(content):
            with psycopg.connect(os.environ["RAG_TEST_DATABASE_URL"], autocommit=True) as conn:
                chunks = [DocumentChunk("api:doc", i, content) for i in range(3)]
                replace_document(conn, self.model, "api:doc", chunks, [[1, 0]] * 3)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(replace, ["first", "second"]))
        results = search(self.conn, self.model, [1, 0], limit=10, source_prefix="api:")
        self.assertEqual(len(results), 3)
        self.assertIn({r.chunk.text for r in results}, [{"first"}, {"second"}])

    def test_database_error_rolls_back_existing_document(self):
        import psycopg
        replace_document(self.conn, self.model, "doc", [DocumentChunk("doc", 0, "anterior")], [[1, 0]])
        invalid = [DocumentChunk("doc", 0, "nuevo"), DocumentChunk("doc", 1, "inválido\x00")]
        with self.assertRaises(psycopg.Error):
            replace_document(self.conn, self.model, "doc", invalid, [[1, 0], [1, 0]])
        self.assertEqual([r.chunk.text for r in search(self.conn, self.model, [1, 0])], ["anterior"])
