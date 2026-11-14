import asyncio
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag.api import Settings
from rag.asgi import Application


class ASGITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.settings = Settings("unused", "w" * 32, "r" * 32, "ollama", "embed", "ollama", "llm", "http://localhost:11434", None)
        self.app = Application(self.settings, capacity=1)

    async def asyncTearDown(self):
        self.app.executor.shutdown(wait=True)

    async def request(self, method="POST", path="/v1/answers", token="r" * 32, body=b'{"question":"q"}', extra_headers=()):
        headers = [(b"authorization", ("Bearer " + token).encode()), (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + list(extra_headers)
        scope = {"type": "http", "method": method, "path": path, "query_string": b"", "headers": headers}
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        messages = []
        async def send(event):
            messages.append(event)
        await self.app(scope, receive, send)
        return messages[0]["status"], json.loads(messages[1]["body"])

    async def test_auth_and_size(self):
        self.assertEqual((await self.request(token="invalid"))[0], 401)
        self.assertEqual((await self.request(token="w" * 32))[0], 403)
        self.assertEqual((await self.request(body=b"x" * 32001))[0], 413)
        self.assertEqual((await self.request(extra_headers=[(b"authorization", b"Bearer " + b"r" * 32)]))[0], 401)

    async def test_contract(self):
        db = MagicMock()
        db.__enter__.return_value = db
        with patch("rag.api.ollama.embed", return_value=[[1.0]]), patch("psycopg.connect", return_value=db), patch("rag.api.search", return_value=[]):
            status, body = await self.request()
        self.assertEqual((status, body["status"], body["sources"]), (200, "no_context", []))

    async def test_disconnect_does_not_release_running_work(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("prueba")
            return [[1.0]]
        db = MagicMock()
        db.__enter__.return_value = db
        try:
            with patch("rag.api.ollama.embed", side_effect=slow), patch("psycopg.connect", return_value=db), patch("rag.api.search", return_value=[]):
                task = asyncio.create_task(self.request())
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual((await self.request())[0], 503)
                release.set()
                for _ in range(100):
                    if self.app.slots.acquire(blocking=False):
                        self.app.slots.release()
                        break
                    await asyncio.sleep(.01)
                else:
                    self.fail("la ranura no se liberó al terminar el trabajo")
                self.assertEqual((await self.request())[0], 200)
        finally:
            release.set()
