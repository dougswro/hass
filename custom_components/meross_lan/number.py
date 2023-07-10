from __future__ import annotations

import typing

from homeassistant.components import number
from homeassistant.const import PERCENTAGE, TEMP_CELSIUS

from . import meross_entity as me
from .helpers import SmartPollingStrategy
from .merossclient import const as mc, get_namespacekey  # mEROSS cONST

if typing.TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .meross_device import MerossDevice
    from .meross_device_hub import MerossSubDevice


try:
    NumberDeviceClass = number.NumberDeviceClass  # type: ignore
except Exception:
    from .helpers import StrEnum

    class NumberDeviceClass(StrEnum):
        HUMIDITY = "humidity"
        TEMPERATURE = "temperature"


try:
    NUMBERMODE_AUTO = number.NumberMode.AUTO
    NUMBERMODE_BOX = number.NumberMode.BOX
    NUMBERMODE_SLIDER = number.NumberMode.SLIDER
except Exception:
    NUMBERMODE_AUTO = "auto"
    NUMBERMODE_BOX = "box"
    NUMBERMODE_SLIDER = "slider"


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_devices
):
    me.platform_setup_entry(hass, config_entry, async_add_devices, number.DOMAIN)


DEVICECLASS_TO_UNIT_MAP = {
    NumberDeviceClass.HUMIDITY: PERCENTAGE,
    NumberDeviceClass.TEMPERATURE: TEMP_CELSIUS,
}


class MLConfigNumber(me.MerossEntity, number.NumberEntity):
    PLATFORM = number.DOMAIN
    DeviceClass = NumberDeviceClass

    manager: MerossDevice
    _attr_native_max_value: float
    _attr_native_min_value: float
    _attr_native_step: float
    _attr_native_unit_of_measurement: str | None

    # customize the request payload for different
    # devices api. see 'async_set_native_value' to see how
    namespace: str
    key_namespace: str
    key_channel: str = mc.KEY_CHANNEL
    key_value: str

    __slots__ = (
        "_attr_native_max_value",
        "_attr_native_min_value",
        "_attr_native_step",
        "_attr_native_unit_of_measurement",
    )

    @property
    def entity_category(self):
        return me.EntityCategory.CONFIG

    @property
    def mode(self) -> number.NumberMode:
        """Return the mode of the entity."""
        return NUMBERMODE_BOX  # type: ignore

    @property
    def native_max_value(self):
        return self._attr_native_max_value

    @property
    def native_min_value(self):
        return self._attr_native_min_value

    @property
    def native_step(self):
        return self._attr_native_step

    @property
    def native_unit_of_measurement(self):
        return self._attr_native_unit_of_measurement

    @property
    def native_value(self):
        return self._attr_state

    def update_native_value(self, value):
        self.update_state(value / self.ml_multiplier)

    async def async_set_native_value(self, value: float):
        device_value = int(value * self.ml_multiplier)

        def _ack_callback(acknowledge: bool, header: dict, payload: dict):
            if acknowledge:
                self.update_native_value(device_value)

        await self.manager.async_request(
            self.namespace,
            mc.METHOD_SET,
            {
                self.key_namespace: [
                    {self.key_channel: self.channel, self.key_value: device_value}
                ]
            },
            _ack_callback,
        )

    @property
    def ml_multiplier(self):
        return 1


class MLHubAdjustNumber(MLConfigNumber):
    key_channel = mc.KEY_ID

    def __init__(
        self,
        manager: "MerossSubDevice",
        key: str,
        namespace: str,
        device_class: NumberDeviceClass,
        min_value: float,
        max_value: float,
        step: float,
    ):
        self.namespace = namespace
        self.key_namespace = get_namespacekey(namespace)
        self.key_value = key
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = DEVICECLASS_TO_UNIT_MAP.get(
            device_class
        )
        self._attr_name = f"Adjust {device_class}"
        super().__init__(
            manager,
            manager.id,
            f"config_{self.key_namespace}_{key}",
            device_class,
        )

    @property
    def ml_multiplier(self):
        return 100


class MLScreenBrightnessNumber(MLConfigNumber):
    manager: ScreenBrightnessMixin

    _attr_icon = "mdi:brightness-percent"

    def __init__(self, manager: ScreenBrightnessMixin, channel: object, key: str):
        self.key_value = key
        self._attr_name = f"Screen brightness ({key})"
        super().__init__(manager, channel, f"screenbrightness_{key}")

    @property
    def native_max_value(self):
        return 100

    @property
    def native_min_value(self):
        return 0

    @property
    def native_step(self):
        return 12.5

    @property
    def native_unit_of_measurement(self):
        return PERCENTAGE

    async def async_set_native_value(self, value: float):
        brightness = {
            mc.KEY_CHANNEL: self.channel,
            mc.KEY_OPERATION: self.manager._number_brightness_operation.native_value,
            mc.KEY_STANDBY: self.manager._number_brightness_standby.native_value,
        }
        brightness[self.key_value] = value

        def _ack_callback(acknowledge: bool, header: dict, payload: dict):
            if acknowledge:
                self.update_native_value(value)

        await self.manager.async_request(
            mc.NS_APPLIANCE_CONTROL_SCREEN_BRIGHTNESS,
            mc.METHOD_SET,
            {mc.KEY_BRIGHTNESS: [brightness]},
            _ack_callback,
        )


class ScreenBrightnessMixin(
    MerossDevice if typing.TYPE_CHECKING else object
):  # pylint: disable=used-before-assignment
    def __init__(self, descriptor, entry):
        super().__init__(descriptor, entry)

        with self.exception_warning("ScreenBrightnessMixin init"):
            # the 'ScreenBrightnessMixin' actually doesnt have a clue of how many  entities
            # are controllable since the digest payload doesnt carry anything (like MerossShutter)
            # So we're not implementing _init_xxx and _parse_xxx methods here and
            # we'll just add a couple of number entities to control 'active' and 'standby' brightness
            # on channel 0 which will likely be the only one available
            self._number_brightness_operation = MLScreenBrightnessNumber(
                self, 0, mc.KEY_OPERATION
            )
            self._number_brightness_standby = MLScreenBrightnessNumber(
                self, 0, mc.KEY_STANDBY
            )
            self.polling_dictionary[
                mc.NS_APPLIANCE_CONTROL_SCREEN_BRIGHTNESS
            ] = SmartPollingStrategy(mc.NS_APPLIANCE_CONTROL_SCREEN_BRIGHTNESS)

    def _handle_Appliance_Control_Screen_Brightness(self, header: dict, payload: dict):
        for p_channel in payload[mc.KEY_BRIGHTNESS]:
            if p_channel.get(mc.KEY_CHANNEL) == 0:
                self._number_brightness_operation.update_native_value(
                    p_channel[mc.KEY_OPERATION]
                )
                self._number_brightness_standby.update_native_value(
                    p_channel[mc.KEY_STANDBY]
                )
                break
