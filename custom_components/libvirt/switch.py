import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import Entity
from . import DOMAIN, get_vm_connection
from .virsh import get_vm_info, is_vm_running, run_virsh,get_vm_state,unpause_vm,start_vm, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    switches = []

    for (connection_name, name), connection in hass.data[DOMAIN]["vms"].items():
        info = await hass.async_add_executor_job(get_vm_info, name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
        switches.append(LibvirtVMSwitch(name, connection_name, info.get("uuid"), hass))

    for switch in switches:
       await switch.async_update()
    async_add_entities(switches)
class LibvirtVMSwitch(SwitchEntity):
    def __init__(self, name, connection_name,uuid,hass):
        self._name = name
        self._connection_name = connection_name
        self._uuid = uuid
        self.hass = hass
        self._state = False

    @property
    def should_poll(self):
        return True


    @property
    def name(self):
        return f"libvirt_{self._name.lower()}"

    @property
    def unique_id(self):
        return self._uuid


    @property
    def is_on(self):
        return self._state
    async def async_update(self):
        connection = get_vm_connection(self.hass, self._name, self._connection_name)
        if not connection:
            self._state = False
            return
        self._state = await self.hass.async_add_executor_job(
            is_vm_running, self._name, connection["ssh_host"], connection["uri"], connection["ssh_key"]
        )
    async def async_turn_on(self, **kwargs):
        connection = get_vm_connection(self.hass, self._name, self._connection_name)
        if not connection:
            return
        state = get_vm_state(self._name, connection["ssh_host"],connection["uri"],connection["ssh_key"])
        if state == "paused":
            unpause_vm(self._name, connection["ssh_host"],connection["uri"],connection["ssh_key"])
        elif state == "shut off":
            start_vm(self._name, connection["ssh_host"],connection["uri"],connection["ssh_key"])
        self._state = True
        self.async_write_ha_state()
    async def async_turn_off(self, **kwargs):
        connection = get_vm_connection(self.hass, self._name, self._connection_name)
        if not connection:
            return
        await self.hass.async_add_executor_job(
            run_virsh, ["shutdown", self._name], connection["ssh_host"], connection["uri"], connection["ssh_key"]
        )