import time
import logging
from datetime import timedelta
from homeassistant.helpers.entity import Entity
from . import DOMAIN, get_vm_connection
from .virsh import get_vm_info, get_vm_ip, get_vm_interfaces, list_snapshots, normalize_key, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(seconds=30)
async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    include_interfaces = config.get("include_interfaces", False)
    sensors = []

    for (connection_name, vm_name), connection in hass.data[DOMAIN]["vms"].items():
        info = await hass.async_add_executor_job(get_vm_info, vm_name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
        sensors.append(LibvirtVMSensor(vm_name, connection_name, include_interfaces, info.get("uuid"), hass))

    async_add_entities(sensors, True)
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

    def update(self):
        try:
            connection = get_vm_connection(self.hass, self._name, self._connection_name)
            if not connection:
                raise RuntimeError(f"No SSH host configured for VM: {self._name}")
            info = get_vm_info(self._name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            ip = get_vm_ip(self._name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            if (self._include_interfaces):
               interfaces = get_vm_interfaces(self._name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            else:
               interfaces = []
            snapshots = list_snapshots(self._name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            self._state = info.get("state", "unknown")
            self._attributes = {
                **info,
                "ip": ip,
                "interfaces": interfaces,
                "snapshots": snapshots,
                "ssh_host":  connection["ssh_host"],
                "uri" : connection["uri"],
            }

        except Exception as e:
            _LOGGER.warning(f"Failed to update VM {self._name}: {e}")
            self._state = "unavailable"
            self._attributes = {"error": str(e)}