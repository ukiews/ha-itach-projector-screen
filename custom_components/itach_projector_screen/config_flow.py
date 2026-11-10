"""UI setup and reconfiguration for iTach Projector Screen."""

from ipaddress import IPv4Address

import probatio

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers import config_validation as cv, selector

from .const import (
    CONF_DAILY_TIME,
    CONF_ENABLE_ENTITY,
    CONF_LOWER_SECONDS,
    CONF_RAISE_SECONDS,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .controller import ScreenController, WIRE_ERRORS

SECONDS = probatio.All(probatio.Coerce(float), probatio.Range(min=1, max=600))


class ScreenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a single IP2CC and its screen."""

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
