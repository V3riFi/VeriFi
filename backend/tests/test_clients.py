"""A stuck browser tab must not hold up anything else. Run from the repo root:
python -m unittest discover -s backend/tests -t ."""

import asyncio
import contextlib
import io
import unittest
from unittest import mock

from backend import clients as module
from backend.clients import Client


class FakeSocket:
    def __init__(self, stuck=False):
        self.sent = []
        self.closed = False
        self.gate = asyncio.Event()
        if not stuck:
            self.gate.set()

    async def send_text(self, text):
        await self.gate.wait()
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed = True


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, ws):
        client = Client(ws, self.dropped.append)
        self.addCleanup(client.stop)
        return client

    async def asyncSetUp(self):
        self.dropped = []

    async def test_a_stuck_tab_does_not_delay_another(self):
        stuck, healthy = FakeSocket(stuck=True), FakeSocket()
        a, b = self.client(stuck), self.client(healthy)
        for i in range(5):
            a.send(f"m{i}")
            b.send(f"m{i}")
        await settle()
        self.assertEqual(healthy.sent, [f"m{i}" for i in range(5)])
        self.assertEqual(stuck.sent, [])

    async def test_slow_tab_skips_old_frames_but_keeps_every_message(self):
        ws = FakeSocket(stuck=True)
        client = self.client(ws)
        client.send("first")
        await settle()
        for name in ("f1", "f2", "f3"):
            client.send(name, frame=True)
        client.send("second")
        ws.gate.set()
        await settle()
        self.assertEqual(ws.sent, ["first", "second", "f3"])

    async def test_a_tab_that_stops_reading_is_disconnected(self):
        ws = FakeSocket(stuck=True)
        with mock.patch.object(module, "SEND_STALL_S", 0.01), contextlib.redirect_stdout(io.StringIO()) as out:
            client = self.client(ws)
            client.send("x")
            await asyncio.sleep(0.05)
        self.assertEqual(self.dropped, [client])
        self.assertTrue(ws.closed)
        self.assertIn("disconnected", out.getvalue())

    async def test_too_much_backlog_disconnects(self):
        ws = FakeSocket(stuck=True)
        with mock.patch.object(module, "MAX_QUEUED", 3), contextlib.redirect_stdout(io.StringIO()):
            client = self.client(ws)
            for i in range(10):
                client.send(str(i))
            await settle()
        self.assertEqual(self.dropped, [client])
        self.assertTrue(ws.closed)

    async def test_sending_after_disconnect_is_ignored(self):
        client = self.client(FakeSocket())
        client.stop()
        client.send("late")
        await settle()
        self.assertEqual(client.ws.sent, [])


if __name__ == "__main__":
    unittest.main()
