"""UI setup and reconfiguration for iTach Projector Screen."""

from ipaddress import IPv4Address
import logging

import probatio

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, selector

from .const import (
    CONF_DAILY_TIME,
    CONF_ENABLE_ENTITY,
    CONF_LOWER_SECONDS,
    CONF_RAISE_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    MAX_TRAVEL_SECONDS,
    MIN_TRAVEL_SECONDS,
)
from .controller import ScreenController, WIRE_ERRORS
from .cover import CalibrationError

LOGGER = logging.getLogger(__name__)
SECONDS = probatio.All(
    probatio.Coerce(float), probatio.Range(min=MIN_TRAVEL_SECONDS, max=MAX_TRAVEL_SECONDS)
)


class ScreenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a single IP2CC and its screen."""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Open the guided screen calibration."""
        return ScreenCalibrationFlow()

    async def async_step_user(self, user_input=None):
        """Add a controller."""
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(self, user_input=None):
        """Change the address or optional timing settings."""
        return await self._async_form("reconfigure", user_input)

    async def _async_form(self, step, user_input):
        entry = self._get_reconfigure_entry() if step == "reconfigure" else None
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "invalid_name"
            try:
                host = str(IPv4Address(user_input[CONF_HOST].strip()))
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            has_raise = CONF_RAISE_SECONDS in user_input
            has_lower = CONF_LOWER_SECONDS in user_input
            if has_raise != has_lower or (user_input.get(CONF_DAILY_TIME) and not has_raise):
                errors["base"] = "travel_times_required"
            if not errors:
                port = user_input[CONF_PORT]
                unique_id = f"{host}:{port}"
                if any(other.unique_id == unique_id for other in self._async_current_entries() if other != entry):
                    return self.async_abort(reason="already_configured")
                try:
                    await ScreenController(host, port).read_contacts()
                except WIRE_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    data = {**user_input, CONF_HOST: host, CONF_NAME: name}
                    if entry is not None:
                        return self.async_update_reload_and_abort(
                            entry, unique_id=unique_id, data=data, title=name
                        )
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title=name, data=data)
        schema = probatio.Schema({
            probatio.Required(CONF_NAME, default=DEFAULT_NAME): str,
            probatio.Required(CONF_HOST): str,
            probatio.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
            probatio.Optional(CONF_RAISE_SECONDS): SECONDS,
            probatio.Optional(CONF_LOWER_SECONDS): SECONDS,
            probatio.Optional(CONF_DAILY_TIME): selector.TimeSelector(),
            probatio.Optional(CONF_ENABLE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="input_boolean")
            ),
        })
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input if user_input is not None else (entry.data if entry else {})
            ),
            errors=errors,
        )


class ScreenCalibrationFlow(config_entries.OptionsFlow):
    """Measure travel between manually confirmed endpoints."""

    _screen = None
    _measured: tuple[float, float] | None = None

    async def async_step_init(self, user_input=None):
        """Explain the motion before starting it."""
        runtime = getattr(self.config_entry, "runtime_data", None)
        self._screen = runtime.cover if runtime is not None else None
        if self._screen is None or self._screen.hass is None:
            self._screen = None
            return self.async_abort(reason="calibration_unavailable")
        return self.async_show_menu(step_id="init", menu_options=["start", "cancel"])

    async def async_step_start(self, user_input=None):
        """Raise the screen to the starting endpoint."""
        try:
            await self._screen.async_start_calibration(self.flow_id)
        except HomeAssistantError as err:
            return self._abort_motion_error(err)
        return self.async_show_menu(step_id="start", menu_options=["initial_top", "cancel"])

    async def async_step_initial_top(self, user_input=None):
        """Confirm the top and begin the measured descent."""
        return await self._async_advance("initial_top", "initial_top", "bottom")

    async def async_step_bottom(self, user_input=None):
        """Confirm the bottom and begin the measured ascent."""
        return await self._async_advance("lowering", "bottom", "final_top")

    async def async_step_final_top(self, user_input=None):
        """Confirm the final top and show the measured times."""
        try:
            self._measured = await self._screen.async_mark_endpoint(self.flow_id, "raising")
        except HomeAssistantError as err:
            return self._abort_motion_error(err)
        return await self.async_step_save()

    async def _async_advance(self, stage, step_id, next_step):
        try:
            await self._screen.async_mark_endpoint(self.flow_id, stage)
        except HomeAssistantError as err:
            return self._abort_motion_error(err)
        return self.async_show_menu(step_id=step_id, menu_options=[next_step, "cancel"])

    async def async_step_save(self, user_input=None):
        """Save both times and set the current position to the observed top."""
        if user_input is not None:
            try:
                await self._screen.async_save_calibration(
                    self.flow_id, user_input[CONF_RAISE_SECONDS], user_input[CONF_LOWER_SECONDS]
                )
            except HomeAssistantError as err:
                return self._abort_motion_error(err)
            return self.async_create_entry(title="", data=dict(self.config_entry.options))
        assert self._measured is not None
        raise_seconds, lower_seconds = self._measured
        return self.async_show_form(
            step_id="save",
            data_schema=probatio.Schema({
                probatio.Required(CONF_RAISE_SECONDS, default=raise_seconds): SECONDS,
                probatio.Required(CONF_LOWER_SECONDS, default=lower_seconds): SECONDS,
            }),
        )

    async def async_step_cancel(self, user_input=None):
        """Stop and exit the wizard."""
        try:
            await self._screen.async_cancel_calibration(self.flow_id)
        except HomeAssistantError as err:
            return self._abort_motion_error(err)
        return self.async_abort(reason="calibration_cancelled")

    @callback
    def async_remove(self):
        """Stop if Home Assistant closes the dialog before calibration finishes."""
        if self._screen is not None:
            self.hass.async_create_task(self._async_stop_after_close(), "Stop projector calibration")

    async def _async_stop_after_close(self):
        try:
            await self._screen.async_cancel_calibration(self.flow_id)
        except HomeAssistantError:
            LOGGER.exception("Could not stop after the calibration dialog closed")

    @callback
    def _abort_motion_error(self, err):
        if isinstance(err, CalibrationError):
            return self.async_abort(reason=err.reason)
        LOGGER.warning("Projector screen calibration failed: %s", err)
        return self.async_abort(reason="calibration_failed")
