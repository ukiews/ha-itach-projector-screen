"""Regression checks for relay release and estimated positioning."""

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components/itach_projector_screen"))
from controller import ControllerError, LOWER, RAISE, STOP, ScreenController
from position import Move


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.states = {"1:1": "0", "1:2": "0"}
        self.sessions = []
        self.fail_first_engage = False
        self.fail_first_release = False
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.addAsyncCleanup(self.server.wait_closed)
        self.addCleanup(self.server.close)
        port = self.server.sockets[0].getsockname()[1]
        self.controller = ScreenController("127.0.0.1", port)
        patcher = patch.dict("controller.PULSE_SECONDS", {RAISE: .01, LOWER: .01, STOP: .02})
        patcher.start()
        self.addCleanup(patcher.stop)

    async def handle(self, reader, writer):
        commands = []
        self.sessions.append(commands)
        try:
            while True:
                cmd = (await reader.readuntil(b"\r")).decode().strip()
                commands.append(cmd)
                verb, relay, *value = cmd.split(",")
                if verb == "getstate":
                    reply = f"state,{relay},{self.states[relay]}"
                else:
                    self.states[relay] = value[0]
                    if value[0] == "1" and self.fail_first_engage:
                        self.fail_first_engage = False
                        return
                    if value[0] == "0" and self.fail_first_release:
                        self.fail_first_release = False
                        return
                    reply = cmd
                writer.write((reply + "\r\n").encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def test_stop_batches_both_contacts_and_verifies_release(self):
        await self.controller.pulse(STOP)
        self.assertEqual(self.sessions[0], [
            "setstate,1:1,1", "setstate,1:2,1", "setstate,1:1,0", "setstate,1:2,0",
            "getstate,1:1", "getstate,1:2",
        ])

    async def test_disconnect_during_engage_releases_without_replaying(self):
        self.fail_first_engage = True
        with self.assertRaises((asyncio.IncompleteReadError, OSError)):
            await self.controller.pulse(RAISE)
        self.assertEqual(self.states, {"1:1": "0", "1:2": "0"})
        self.assertEqual(self.sessions[1], [
            "setstate,1:1,0", "setstate,1:2,0", "getstate,1:1", "getstate,1:2"
        ])

    async def test_disconnect_during_release_retries_only_release(self):
        self.fail_first_release = True
        await self.controller.pulse(LOWER)
        self.assertEqual(self.states, {"1:1": "0", "1:2": "0"})
        self.assertEqual(sum(c.endswith(",1") and c.startswith("setstate")
                             for session in self.sessions for c in session), 1)

    async def test_movement_refuses_engaged_contact_but_stop_recovers(self):
        self.states["1:2"] = "1"
        with self.assertRaisesRegex(ControllerError, "already engaged"):
            await self.controller.pulse(RAISE)
        self.assertEqual(self.sessions[0], ["getstate,1:1", "getstate,1:2"])
        await self.controller.pulse(STOP)
        self.assertFalse(any(await self.controller.read_contacts()))

    async def test_concurrent_commands_serialize(self):
        await asyncio.gather(self.controller.pulse(RAISE), self.controller.pulse(LOWER))
        self.assertEqual(self.sessions[1][:3], ["getstate,1:1", "getstate,1:2", "setstate,1:2,1"])


class PositionTests(unittest.TestCase):
    def test_partial_move_uses_measured_direction_speed(self):
        lower = Move(100, 60, 10, 20)
        raise_ = Move(60, 90, 20, 10)
        self.assertEqual(lower.seconds, 8)
        self.assertEqual(lower.estimate(14), 80)
        self.assertEqual(raise_.seconds, 3)
        self.assertEqual(raise_.estimate(22), 80)

    def test_full_raise_can_home_from_unknown_or_already_raised(self):
        unknown = Move(None, 100, 0, 12)
        self.assertIsNone(unknown.estimate(6))
        self.assertEqual(unknown.seconds, 13)
        self.assertTrue(Move(100, 100, 0, 12).raising)
