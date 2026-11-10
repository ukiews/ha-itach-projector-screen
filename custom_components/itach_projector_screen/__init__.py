"""Expose a Global Caché iTach IP2CC as a projector screen cover."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .controller import ScreenController, WIRE_ERRORS

if TYPE_CHECKING:
    from .cover import ProjectorScreen


@dataclass(slots=True)
class ScreenRuntime:
    """The controller and its registered cover."""

    controller: ScreenController
    cover: "ProjectorScreen | None" = None


type ScreenEntry = ConfigEntry[ScreenRuntime]
PLATFORMS = [Platform.COVER]


async def async_setup_entry(hass: HomeAssistant, entry: ScreenEntry) -> bool:
    """Connect and set up the cover."""
    controller = ScreenController(entry.data[CONF_HOST], entry.data[CONF_PORT])
    try:
        await controller.read_contacts()
    except WIRE_ERRORS as err:
        raise ConfigEntryNotReady("Could not read the iTach relay contacts") from err
    entry.runtime_data = ScreenRuntime(controller)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ScreenEntry) -> bool:
    """Unload the cover and its timers."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
