import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import Entity
from . import DOMAIN
from .virsh import get_all_vms, get_vm_info, is_vm_running, run_virsh,get_vm_state,unpause_vm,start_vm, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    switches = []

    for connection in hass.data[DOMAIN]["connections"]:
        ssh_host = connection["ssh_host"]
        ssh_key = connection["ssh_key"]
        uri = connection["uri"]

        try:
            domains = await hass.async_add_executor_job(get_all_vms, ssh_host, uri, ssh_key)
        except Exception as e:
            _LOGGER.error("Failed to get VMs from %s: %s", ssh_host, e)
            return

        for name in domains:
            info = await hass.async_add_executor_job(get_vm_info, name, ssh_host, uri, ssh_key)
            switches.append(LibvirtVMSwitch(name, ssh_host, uri, info.get("uuid"), ssh_key, hass))

    for switch in switches:
       await switch.async_update()
    async_add_entities(switches)
class LibvirtVMSwitch(SwitchEntity):
    def __init__(self, name, ssh_host, uri,uuid,ssh_key, hass):
        self._name = name
        self._ssh_host = ssh_host
        self._uri = uri
        self._uuid = uuid
        self._ssh_key = ssh_key
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
        self._state = await self.hass.async_add_executor_job(
            is_vm_running, self._name, self._ssh_host, self._uri, self._ssh_key
        )
    async def async_turn_on(self, **kwargs):
        state = get_vm_state(self._name, self._ssh_host,self._uri,self._ssh_key)
        if state == "paused":
            unpause_vm(self._name, self._ssh_host,self._uri,self._ssh_key)
        elif state == "shut off":
            start_vm(self._name, self._ssh_host,self._uri,self._ssh_key)
        self._state = True
        self.async_write_ha_state()
    async def async_turn_off(self, **kwargs):
        await self.hass.async_add_executor_job(
            run_virsh, ["shutdown", self._name], self._ssh_host, self._uri, self._ssh_key
        )