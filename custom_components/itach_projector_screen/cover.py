"""A cover with confirmed relay pulses and optional timed positioning."""

import asyncio
from datetime import time, timedelta
from functools import partial
import logging
from time import monotonic

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
from homeassistant.const import CONF_NAME, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_change, async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from . import ScreenEntry
from .const import CONF_DAILY_TIME, CONF_ENABLE_ENTITY, CONF_LOWER_SECONDS, CONF_RAISE_SECONDS, DOMAIN
from .controller import LOWER, RAISE, STOP, WIRE_ERRORS
from .position import Move

LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ScreenEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Add one projector screen for the controller."""
    async_add_entities([ProjectorScreen(entry)])


class ProjectorScreen(CoverEntity, RestoreEntity):
    """Operate the screen and keep its position explicitly estimated."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_icon = "mdi:projector-screen-outline"
    _attr_should_poll = True
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP

    def __init__(self, entry: ScreenEntry):
        self.controller = entry.runtime_data
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Global Caché",
            model="iTach IP2CC",
            name=entry.data[CONF_NAME],
        )
        self._enable_entity = entry.data.get(CONF_ENABLE_ENTITY)
        self._raise_seconds = entry.data.get(CONF_RAISE_SECONDS)
        self._lower_seconds = entry.data.get(CONF_LOWER_SECONDS)
        self._daily_time = entry.data.get(CONF_DAILY_TIME)
        if self._raise_seconds is not None and self._lower_seconds is not None:
            self._attr_supported_features |= CoverEntityFeature.SET_POSITION
        self._position: float | None = None
        self._move: Move | None = None
        self._stop_pending = False
        self._last_known_raised = False
        self._command_lock = asyncio.Lock()
        self._cancel_finish = None
        self._cancel_tick = None
        self._removing = False

    async def async_added_to_hass(self) -> None:
        """Recover a pending Stop, then register the optional calibration time."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._stop_pending = last.attributes.get("stop_pending") is True
            self._last_known_raised = last.attributes.get("last_known_raised") is True
        if self._daily_time:
            when = time.fromisoformat(self._daily_time)
            self.async_on_remove(async_track_time_change(
                self.hass, self._async_daily_calibration,
                hour=when.hour, minute=when.minute, second=when.second,
            ))

    async def async_will_remove_from_hass(self) -> None:
        """Stop a timed move before its Stop timer is removed."""
        self._removing = True
        async with self._command_lock:
            if self._move is not None:
                try:
                    await self._async_stop()
                except HomeAssistantError:
                    LOGGER.exception("Could not stop the timed move while unloading the screen")
        await super().async_will_remove_from_hass()

    async def async_update(self) -> None:
        """Check connectivity using relay reads only."""
        try:
            contacts = await self.controller.read_contacts()
        except WIRE_ERRORS:
            self._attr_available = False
        else:
            self._attr_available = True
            if any(contacts) and self._move is None:
                self._position = None
                self._last_known_raised = False

    @property
    def current_cover_position(self) -> int | None:
        """Return the estimated position or unknown."""
        position = self._estimate()
        return None if position is None else round(position)

    @property
    def is_closed(self) -> bool | None:
        """Use the position estimate when calibrated."""
        position = self._estimate()
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool:
        """Report a timed raise in progress."""
        return self._move is not None and self._move.raising

    @property
    def is_closing(self) -> bool:
        """Report a timed lower in progress."""
        return self._move is not None and not self._move.raising

    @property
    def extra_state_attributes(self) -> dict:
        """Expose a pending Stop and identify the source of position."""
        return {
            "stop_pending": self._stop_pending,
            "last_known_raised": self._last_known_raised,
            "position_is_estimated": self._raise_seconds is not None,
        }

    def _estimate(self) -> float | None:
        return self._position if self._move is None else self._move.estimate(monotonic())

    def _motion_enabled(self) -> bool:
        return self._enable_entity is None or self.hass.states.is_state(self._enable_entity, STATE_ON)

    async def async_open_cover(self, **kwargs) -> None:
        """Raise the screen."""
        await self._async_request(100)

    async def async_close_cover(self, **kwargs) -> None:
        """Lower the screen."""
        await self._async_request(0)

    async def async_stop_cover(self, **kwargs) -> None:
        """Stop the screen, even when motion is disabled."""
        await self._async_request(None)

    async def async_set_cover_position(self, **kwargs) -> None:
        """Move to an estimated position."""
        await self._async_request(kwargs[ATTR_POSITION])

    async def _async_request(self, target: int | None) -> None:
        # An automation reload can cancel the caller. The relay pulse still needs to finish.
        task = self.hass.async_create_task(self._async_command(target), "iTach projector screen command")
        await asyncio.shield(task)

    async def _async_command(self, target: int | None, *, daily: bool = False) -> None:
        async with self._command_lock:
            if self._removing:
                return
            if daily and (not self.available or not self._motion_enabled()
                          or self._move is not None or not self._last_known_raised or self._stop_pending):
                return
            if target is None:
                await self._async_stop()
                return
            if not self._motion_enabled():
                raise ServiceValidationError("Screen motion is disabled")
            if self._move is not None:
                await self._async_stop()
                if not self._motion_enabled():
                    raise ServiceValidationError("Screen motion is disabled")
            if target not in (0, 100) and (self._position is None or self._raise_seconds is None):
                raise ServiceValidationError("Raise the screen fully to calibrate its position first")
            if target not in (0, 100) and target == self._position:
                return

            raising = target == 100 or (self._position is not None and target > self._position)
            if target == 0:
                raising = False
            seconds = self._raise_seconds if raising else self._lower_seconds
            self._stop_pending = True
            self._last_known_raised = False
            self.async_write_ha_state()
            started_at = await self._async_pulse(RAISE if raising else LOWER)
            if seconds is None:
                return

            move = self._move = Move(self._position, target, started_at, seconds)
            self._cancel_finish = async_call_later(
                self.hass, max(0, move.seconds - (monotonic() - started_at)),
                partial(self._async_finish, move),
            )
            self._cancel_tick = async_track_time_interval(self.hass, self._tick, timedelta(seconds=1))
            self.async_write_ha_state()

    @callback
    def _tick(self, _now) -> None:
        self.async_write_ha_state()

    async def _async_finish(self, move: Move, _now) -> None:
        async with self._command_lock:
            if move is not self._move:
                return
            try:
                await self._async_stop(position=float(move.target))
            except HomeAssistantError:
                LOGGER.exception("Could not stop the projector screen at its timed position")

    async def _async_daily_calibration(self, _now) -> None:
        try:
            await self._async_command(100, daily=True)
        except HomeAssistantError:
            LOGGER.exception("Could not recalibrate the projector screen")

    async def _async_stop(self, *, position: float | None = None) -> None:
        move = self._move
        if self._cancel_finish is not None:
            self._cancel_finish()
            self._cancel_finish = None
        if self._cancel_tick is not None:
            self._cancel_tick()
            self._cancel_tick = None
        self._move = None
        self._stop_pending = True
        self.async_write_ha_state()
        stopped_at = await self._async_pulse(STOP)
        if position is not None:
            self._position = position
        elif move is not None:
            self._position = move.estimate(stopped_at)
        if self._position is not None:
            self._last_known_raised = self._position == 100
        self._stop_pending = False
        self.async_write_ha_state()

    async def _async_pulse(self, contacts: tuple[int, ...]) -> float:
        try:
            return await self.controller.pulse(contacts)
        except WIRE_ERRORS as err:
            self._position = None
            self._last_known_raised = False
            self.async_write_ha_state()
            raise HomeAssistantError(f"iTach screen command failed: {err}") from err
