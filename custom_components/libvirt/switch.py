import logging
from homeassistant.core import callback
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from . import DOMAIN, SIGNAL_UPDATE, async_get_vm_record
from .virsh import run_virsh,get_vm_state,unpause_vm,start_vm

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    switches = []

    for (connection_name, name), record in hass.data[DOMAIN]["vms"].items():
        switches.append(LibvirtVMSwitch(name, connection_name, record["info"].get("uuid"), hass))

    async_add_entities(switches)
class LibvirtVMSwitch(SwitchEntity):
    def __init__(self, name, connection_name,uuid,hass):
        self._name = name
        self._connection_name = connection_name
        self._uuid = uuid
        self.hass = hass
        self._state = False
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
    def is_on(self):
        return self._state

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
        self._state = bool(record and record["info"].get("state") == "running")

    async def async_turn_on(self, **kwargs):
        record = await async_get_vm_record(self.hass, self._name, self._connection_name)
        if not record:
            return
        connection = record["connection"]
        state = record["info"].get("state")
        if state == "paused":
            await self.hass.async_add_executor_job(unpause_vm, self._name, connection["ssh_host"],connection["uri"],connection["ssh_key"])
        elif state == "shut off":
            await self.hass.async_add_executor_job(start_vm, self._name, connection["ssh_host"],connection["uri"],connection["ssh_key"])
        record["info"]["state"] = "running"
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
    async def async_turn_off(self, **kwargs):
        record = await async_get_vm_record(self.hass, self._name, self._connection_name)
        if not record:
            return
        connection = record["connection"]
        await self.hass.async_add_executor_job(
            run_virsh, ["shutdown", self._name], connection["ssh_host"], connection["uri"], connection["ssh_key"]
        )
        record["info"]["state"] = "shut off"
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)