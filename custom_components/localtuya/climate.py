"""Platform to locally control Tuya-based climate devices.
    # PRESETS and HVAC_MODE Needs to be handle in better way.
"""

import asyncio
import logging
from functools import partial
from .config_flow import _col_to_select
from homeassistant.helpers import selector

import voluptuous as vol
from homeassistant.components.climate import (
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    DOMAIN,
    ClimateEntity,
)
from homeassistant.components.climate.const import (
    HVACMode,
    HVACAction,
    PRESET_AWAY,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    ClimateEntityFeature,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_TEMPERATURE_UNIT,
    PRECISION_HALVES,
    PRECISION_TENTHS,
    PRECISION_WHOLE,
    UnitOfTemperature,
)

from .common import LocalTuyaEntity, async_setup_entry
from .const import (
    CONF_CURRENT_TEMPERATURE_DP,
    CONF_ECO_DP,
    CONF_ECO_VALUE,
    CONF_HEURISTIC_ACTION,
    CONF_HVAC_ACTION_DP,
    CONF_HVAC_ACTION_SET,
    CONF_HVAC_MODE_DP,
    CONF_HVAC_MODE_SET,
    CONF_PRECISION,
    CONF_PRESET_DP,
    CONF_PRESET_SET,
    CONF_TARGET_PRECISION,
    CONF_TARGET_TEMPERATURE_DP,
    CONF_TEMPERATURE_STEP,
    CONF_MIN_TEMP,
    CONF_MAX_TEMP,
    CONF_HVAC_ADD_OFF,
    CONF_FAN_SPEED_DP,
    CONF_FAN_SPEED_LIST,
)

_LOGGER = logging.getLogger(__name__)


HVAC_OFF = {HVACMode.OFF.value: "off"}
RENAME_HVAC_MODE_SETS = {  # Mirgae to 3
    ("manual", "Manual", "hot", "m", "True"): HVACMode.HEAT.value,
    ("auto", "0", "p", "Program"): HVACMode.AUTO.value,
    ("freeze", "cold", "1"): HVACMode.COOL.value,
    ("wet"): HVACMode.DRY.value,
}
RENAME_ACTION_SETS = {  # Mirgae to 3
    ("open", "opened", "heating", "Heat", "True"): HVACAction.HEATING.value,
    ("closed", "close", "no_heating"): HVACAction.IDLE.value,
    ("Warming", "warming", "False"): HVACAction.IDLE.value,
    ("cooling"): HVACAction.COOLING.value,
    ("off"): HVACAction.OFF.value,
}
RENAME_PRESET_SETS = {
    "Holiday": (PRESET_AWAY),
    "Program": (PRESET_HOME),
    "Manual": (PRESET_NONE, "manual"),
    "Auto": "auto",
    "Manual": "manual",
    "Smart": "smart",
    "Comfort": "comfortable",
    "ECO": "eco",
}


HVAC_MODE_SETS = {
    HVACMode.OFF: False,
    HVACMode.AUTO: "auto",
    HVACMode.COOL: "cold",
    HVACMode.HEAT: "hot",
    HVACMode.HEAT_COOL: "heat",
    HVACMode.DRY: "wet",
    HVACMode.FAN_ONLY: "wind",
}

HVAC_ACTION_SETS = {
    HVACAction.HEATING: "opened",
    HVACAction.IDLE: "closed",
}


TEMPERATURE_CELSIUS = "celsius"
TEMPERATURE_FAHRENHEIT = "fahrenheit"
DEFAULT_TEMPERATURE_UNIT = TEMPERATURE_CELSIUS
DEFAULT_PRECISION = PRECISION_TENTHS
DEFAULT_TEMPERATURE_STEP = PRECISION_HALVES
# Empirically tested to work for AVATTO thermostat
MODE_WAIT = 0.1

FAN_SPEEDS_DEFAULT = "auto,low,middle,high"


def flow_schema(dps):
    """Return schema used in config flow."""
    return {
        vol.Optional(CONF_TARGET_TEMPERATURE_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(CONF_CURRENT_TEMPERATURE_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(CONF_TEMPERATURE_STEP): _col_to_select(
            [PRECISION_WHOLE, PRECISION_HALVES, PRECISION_TENTHS]
        ),
        vol.Optional(CONF_MIN_TEMP, default=DEFAULT_MIN_TEMP): vol.Coerce(float),
        vol.Optional(CONF_MAX_TEMP, default=DEFAULT_MAX_TEMP): vol.Coerce(float),
        vol.Optional(CONF_PRECISION, default=str(DEFAULT_PRECISION)): _col_to_select(
            [PRECISION_WHOLE, PRECISION_HALVES, PRECISION_TENTHS]
        ),
        vol.Optional(
            CONF_TARGET_PRECISION, default=str(DEFAULT_PRECISION)
        ): _col_to_select([PRECISION_WHOLE, PRECISION_HALVES, PRECISION_TENTHS]),
        vol.Optional(CONF_HVAC_MODE_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(
            CONF_HVAC_MODE_SET, default=HVAC_MODE_SETS
        ): selector.ObjectSelector(),
        vol.Optional(CONF_HVAC_ACTION_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(
            CONF_HVAC_ACTION_SET, default=HVAC_ACTION_SETS
        ): selector.ObjectSelector(),
        vol.Optional(CONF_ECO_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(CONF_ECO_VALUE): str,
        vol.Optional(CONF_PRESET_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(CONF_PRESET_SET, default={}): selector.ObjectSelector(),
        vol.Optional(CONF_FAN_SPEED_DP): _col_to_select(dps, is_dps=True),
        vol.Optional(CONF_FAN_SPEED_LIST, default=FAN_SPEEDS_DEFAULT): str,
        vol.Optional(CONF_TEMPERATURE_UNIT): _col_to_select(
            [TEMPERATURE_CELSIUS, TEMPERATURE_FAHRENHEIT]
        ),
        vol.Optional(CONF_HEURISTIC_ACTION): bool,
    }


class LocaltuyaClimate(LocalTuyaEntity, ClimateEntity):
    """Tuya climate device."""

    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self,
        device,
        config_entry,
        switchid,
        **kwargs,
    ):
        """Initialize a new LocaltuyaClimate."""
        super().__init__(device, config_entry, switchid, _LOGGER, **kwargs)
        self._state = None
        self._target_temperature = None
        self._current_temperature = None
        self._hvac_mode = None
        self._preset_mode = None
        self._hvac_action = None
        self._precision = float(self._config.get(CONF_PRECISION, DEFAULT_PRECISION))
        self._precision_target = float(
            self._config.get(CONF_TARGET_PRECISION, DEFAULT_PRECISION)
        )

        # HVAC Modes
        self._hvac_mode_dp = self._config.get(CONF_HVAC_MODE_DP)
        if modes_set := self._config.get(CONF_HVAC_MODE_SET, {}):
            # HA HVAC Modes are all lower case.
            modes_set = {k.lower(): v for k, v in modes_set.copy().items()}
        self._hvac_mode_set = modes_set

        # Presets
        self._preset_dp = self._config.get(CONF_PRESET_DP)
        self._preset_set: dict = self._config.get(CONF_PRESET_SET, {})

        # Sort Modes If the HVAC isn't supported by HA then we add it as preset.
        if self._preset_dp == self._hvac_mode_dp or not self._preset_dp:
            for k, v in self._hvac_mode_set.copy().items():
                if k not in HVACMode:
                    self._preset_dp = self._hvac_mode_dp
                    self._preset_set[k] = self._hvac_mode_set.pop(k)

        self._preset_name_to_value = {v: k for k, v in self._preset_set.items()}

        # HVAC Actions
        self._conf_hvac_action_dp = self._config.get(CONF_HVAC_ACTION_DP)
        if actions_set := self._config.get(CONF_HVAC_ACTION_SET, {}):
            actions_set = {k.lower(): v for k, v in actions_set.copy().items()}
        self._conf_hvac_action_set = actions_set

        # Fan
        self._fan_speed_dp = self._config.get(CONF_FAN_SPEED_DP)
        if fan_speeds := self._config.get(CONF_FAN_SPEED_LIST, []):
            fan_speeds = [v.lstrip() for v in fan_speeds.split(",")]
        self._fan_supported_speeds = fan_speeds
        self._has_fan_mode = self._fan_speed_dp and self._fan_supported_speeds

        # Eco!?
        self._eco_dp = self._config.get(CONF_ECO_DP)
        self._eco_value = self._config.get(CONF_ECO_VALUE, "ECO")
        self._has_presets = self._eco_dp or (self._preset_dp and self._preset_set)

    @property
    def supported_features(self):
        """Flag supported features."""
        supported_features = ClimateEntityFeature(0)
        if self.has_config(CONF_TARGET_TEMPERATURE_DP):
            supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._has_presets:
            supported_features |= ClimateEntityFeature.PRESET_MODE
        if self._has_fan_mode:
            supported_features |= ClimateEntityFeature.FAN_MODE

        try:  # requires HA >= 2024.2.1
            supported_features |= ClimateEntityFeature.TURN_OFF
            supported_features |= ClimateEntityFeature.TURN_ON
        except AttributeError:
            ...

        return supported_features

    @property
    def precision(self):
        """Return the precision of the system."""
        return self._precision

    @property
    def temperature_unit(self):
        """Return the unit of measurement used by the platform."""
        # Unit may rely on self.hass.config.units.temperature_unit [System Unit]
        if self._config.get(CONF_TEMPERATURE_UNIT) == TEMPERATURE_FAHRENHEIT:
            return UnitOfTemperature.FAHRENHEIT
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self):
        """Return the minimum temperature."""
        # DEFAULT_MIN_TEMP is in C
        return self._config.get(CONF_MIN_TEMP, DEFAULT_MIN_TEMP)

    @property
    def max_temp(self):
        """Return the maximum temperature."""
        # DEFAULT_MAX_TEMP is in C
        return self._config.get(CONF_MAX_TEMP, DEFAULT_MAX_TEMP)

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle."""
        return self._hvac_mode

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        if not self.has_config(CONF_HVAC_MODE_DP):
            return None

        modes = list(self._hvac_mode_set)

        if self._config.get(CONF_HVAC_ADD_OFF, True) and HVACMode.OFF not in modes:
            modes.append(HVACMode.OFF)
        return modes

    @property
    def hvac_action(self):
        """Return the current running hvac operation if supported."""
        if not self._conf_hvac_action_dp:
            if self._hvac_mode == HVACMode.COOL:
                self._hvac_action = HVACAction.COOLING
            if self._hvac_mode == HVACMode.HEAT:
                self._hvac_action = HVACAction.HEATING
            if self._hvac_mode == HVACMode.OFF:
                self._hvac_action = HVACAction.IDLE
            if self._hvac_mode == HVACMode.DRY:
                self._hvac_action = HVACAction.DRYING

        # This exists from upstream, not sure the use case of this.
        if self._config.get(CONF_HEURISTIC_ACTION, False):
            if self._hvac_mode == HVACMode.HEAT:
                if self._current_temperature < (
                    self._target_temperature - self._precision
                ):
                    self._hvac_action = HVACMode.HEAT
                if self._current_temperature == (
                    self._target_temperature - self._precision
                ):
                    if self._hvac_action == HVACMode.HEAT:
                        self._hvac_action = HVACMode.HEAT
                    if self._hvac_action == HVACAction.IDLE:
                        self._hvac_action = HVACAction.IDLE
                if (
                    self._current_temperature + self._precision
                ) > self._target_temperature:
                    self._hvac_action = HVACAction.IDLE
            return self._hvac_action
        return self._hvac_action

    @property
    def preset_mode(self):
        """Return current preset."""
        mode = self.dp_value(CONF_HVAC_MODE_DP)
        if mode in list(self._hvac_mode_set.values()):
            return None

        return self._preset_mode

    @property
    def preset_modes(self):
        """Return the list of available presets modes."""
        if not self._has_presets:
            return None

        presets = list(self._preset_set.values())
        if self._eco_dp:
            presets.append(PRESET_ECO)
        return presets

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        target_step = self._config.get(CONF_TEMPERATURE_STEP, DEFAULT_TEMPERATURE_STEP)
        return float(target_step)

    @property
    def fan_mode(self):
        """Return the fan setting."""
        if not (fan_value := self.dp_value(self._fan_speed_dp)):
            return None
        return fan_value

    @property
    def fan_modes(self) -> list:
        """Return the list of available fan modes."""
        return self._fan_supported_speeds

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        if ATTR_TEMPERATURE in kwargs and self.has_config(CONF_TARGET_TEMPERATURE_DP):
            temperature = round(kwargs[ATTR_TEMPERATURE] / self._precision_target)
            await self._device.set_dp(
                temperature, self._config[CONF_TARGET_TEMPERATURE_DP]
            )

    async def async_set_fan_mode(self, fan_mode):
        """Set new target fan mode."""
        if not self._state:
            await self._device.set_dp(True, self._dp_id)

        await self._device.set_dp(fan_mode, self._fan_speed_dp)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target operation mode."""
        new_states = {self._dp_id: hvac_mode != HVACMode.OFF}
        if hvac_mode in self._hvac_mode_set:
            new_states[self._hvac_mode_dp] = self._hvac_mode_set[hvac_mode]

        await self._device.set_dps(new_states)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self._device.set_dp(True, self._dp_id)

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self._device.set_dp(False, self._dp_id)

    async def async_set_preset_mode(self, preset_mode):
        """Set new target preset mode."""
        if preset_mode == PRESET_ECO:
            await self._device.set_dp(self._eco_value, self._eco_dp)
            return

        preset_value = self._preset_name_to_value.get(preset_mode)
        await self._device.set_dp(preset_value, self._preset_dp)

    def status_updated(self):
        """Device status was updated."""
        self._state = self.dp_value(self._dp_id)

        # Update target temperature
        if self.has_config(CONF_TARGET_TEMPERATURE_DP):
            self._target_temperature = (
                self.dp_value(CONF_TARGET_TEMPERATURE_DP) * self._precision_target
            )

        # Update current temperature
        if self.has_config(CONF_CURRENT_TEMPERATURE_DP):
            self._current_temperature = (
                self.dp_value(CONF_CURRENT_TEMPERATURE_DP) * self._precision
            )

        # Update preset states
        if self._has_presets:
            if self.dp_value(CONF_ECO_DP) == self._eco_value:
                self._preset_mode = PRESET_ECO
            else:
                for preset_value, preset_name in self._preset_set.items():
                    if self.dp_value(CONF_PRESET_DP) == preset_value:
                        self._preset_mode = preset_name
                        break
                else:
                    self._preset_mode = PRESET_NONE

        # Update the HVAC Mode
        if self.has_config(CONF_HVAC_MODE_DP):
            if not self._state:
                self._hvac_mode = HVACMode.OFF
            else:
                for ha_hvac, tuya_value in self._hvac_mode_set.items():
                    if self.dp_value(CONF_HVAC_MODE_DP) == tuya_value:
                        self._hvac_mode = ha_hvac
                        break
                else:
                    # in case hvac mode and preset share the same dp
                    self._hvac_mode = HVACMode.AUTO

        # Update the current action
        for ha_action, tuya_value in self._conf_hvac_action_set.items():
            if self.dp_value(CONF_HVAC_ACTION_DP) == tuya_value:
                self._hvac_action = ha_action


async_setup_entry = partial(async_setup_entry, DOMAIN, LocaltuyaClimate, flow_schema)
