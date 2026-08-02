import time
import logging
from datetime import timedelta
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from . import DOMAIN, SIGNAL_UPDATE

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    include_interfaces = config.get("include_interfaces", False)
    sensors = []

    for (connection_name, vm_name), record in hass.data[DOMAIN]["vms"].items():
        sensors.append(LibvirtVMSensor(vm_name, connection_name, include_interfaces, record["info"].get("uuid"), hass))

    async_add_entities(sensors)
class LibvirtVMSensor(Entity):
    def __init__(self, name, connection_name,include_interfaces,uuid,hass):
        self._name = name
        self._connection_name = connection_name
        self._state = None
        self._attributes = {}
        self._include_interfaces = include_interfaces
        self._uuid = uuid
        self.hass = hass
        self._last_cpu_time = None
        self._last_timestamp = None
        self._update_from_cache()

    @property
    def should_poll(self):
        return False

    @property
    def name(self):
        return f"libvirt_{self._name.lower()}"

    @property
    def unique_id(self):
        return self._uuid

    @property
    def state(self):
        return self._state
    @property
    def extra_state_attributes(self):
        return self._attributes

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE,
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self):
        self._update_from_cache()
        self.async_write_ha_state()

    def _update_from_cache(self):
        record = self.hass.data[DOMAIN]["vms"].get((self._connection_name, self._name))
        if not record:
            self._state = "unavailable"
            self._attributes = {"error": f"No cached data for VM: {self._name}"}
            return

        info = record["info"]
        interfaces = record["interfaces"] if self._include_interfaces else []
        connection = record["connection"]
        self._state = info.get("state", "unknown")
        self._attributes = {
            **info,
            "ip": ", ".join(
                interface["address"].split("/")[0]
                for interface in record["interfaces"]
                if interface.get("protocol") == "ipv4"
                and not interface.get("address", "").startswith("127.")
            ),
            "interfaces": interfaces,
            "snapshots": record["snapshots"],
            "ssh_host":  connection["ssh_host"],
            "uri" : connection["uri"],
        }