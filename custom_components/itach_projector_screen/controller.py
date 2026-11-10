"""The Global Caché relay protocol used by the two-contact screen controller."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic

RAISE = (1,)
LOWER = (2,)
STOP = (1, 2)
PULSE_SECONDS = {RAISE: 0.1, LOWER: 0.1, STOP: 0.5}
MOTION_SETTLE_SECONDS = 0.75


class ControllerError(OSError):
    """The controller did not confirm the requested operation."""


class Connection:
    """Exchange CR-terminated lines on a single connection."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer

    async def exchange(self, commands: list[str]) -> list[str]:
        # Send both STOP contacts in one write so there is no round trip between them.
        self.writer.write(("\r".join(commands) + "\r").encode("ascii"))
        replies = []
        async with asyncio.timeout(2):
            await self.writer.drain()
            for _ in commands:
                while not (line := (await self.reader.readuntil(b"\r")).strip()):
                    continue
                replies.append(line.decode("ascii", errors="replace"))
        return replies

    async def set_contacts(self, contacts: tuple[int, ...], closed: bool) -> None:
        commands = [f"setstate,1:{contact},{int(closed)}" for contact in contacts]
        if await self.exchange(commands) != commands:
            raise ControllerError("Unexpected relay acknowledgement")

    async def read_contacts(self) -> tuple[bool, bool]:
        replies = await self.exchange(["getstate,1:1", "getstate,1:2"])
        for contact, reply in enumerate(replies, start=1):
            if reply not in (f"state,1:{contact},0", f"state,1:{contact},1"):
                raise ControllerError("Unexpected relay status")
        return replies[0].endswith(",1"), replies[1].endswith(",1")

    async def release(self) -> None:
        await self.set_contacts(STOP, False)
        if any(await self.read_contacts()):
            raise ControllerError("Both relay contacts must be released")


WIRE_ERRORS = (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError)


class ScreenController:
    """Serialize reads and complete pulses, including recovery releases."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.lock = asyncio.Lock()
        self._next_motion_at = 0.0

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[Connection]:
        async with asyncio.timeout(4):
            reader, writer = await asyncio.open_connection(self.host, self.port, limit=1024)
        try:
            yield Connection(reader, writer)
        finally:
            writer.close()

    async def read_contacts(self) -> tuple[bool, bool]:
        async with self.lock:
            async with self._connect() as connection:
                return await connection.read_contacts()

    async def pulse(self, contacts: tuple[int, ...]) -> float:
        async with self.lock:
            if contacts != STOP:
                # The screen receiver needs a quiet interval after released contacts.
                await asyncio.sleep(max(0.0, self._next_motion_at - monotonic()))
            async with self._connect() as connection:
                if contacts != STOP and any(await connection.read_contacts()):
                    raise ControllerError("A relay is already engaged. Send Stop before moving.")
                try:
                    started_at = monotonic()
                    await connection.set_contacts(contacts, True)
                    await asyncio.sleep(PULSE_SECONDS[contacts])
                finally:
                    try:
                        await connection.release()
                    except WIRE_ERRORS:
                        # Recover only by releasing contacts. Never replay a motion command.
                        async with self._connect() as recovery:
                            await recovery.release()
                    self._next_motion_at = monotonic() + MOTION_SETTLE_SECONDS
                return started_at
