from .virsh import run_virsh, is_vm_running, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI, take_screenshot
import os
import subprocess
import logging
from homeassistant.components.http import StaticPathConfig


DOMAIN = "libvirt"
_LOGGER = logging.getLogger(__name__)

def get_vm_connection(hass, name, connection_name=None):
    if connection_name:
        connection = hass.data[DOMAIN]["vms"].get((connection_name, name))
        if connection:
            return connection
    else:
        matches = [
            connection
            for (mapped_connection_name, vm_name), connection in hass.data[DOMAIN]["vms"].items()
            if vm_name == name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _LOGGER.error(f"VM {name} exists on more than one libvirt connection")
            return None

    matches = []
    for connection in hass.data[DOMAIN]["connections"]:
        if connection_name and connection["name"] != connection_name:
            continue
        try:
            run_virsh(["dominfo", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
            hass.data[DOMAIN]["vms"][(connection["name"], name)] = connection
            matches.append(connection)
        except Exception:
            continue

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _LOGGER.error(f"VM {name} exists on more than one libvirt connection")
        return None

    _LOGGER.error(f"No SSH host configured for VM: {name}")
    return None

async def async_setup(hass, config):
    os.makedirs("/tmp/libvirt", exist_ok=True)
    await hass.http.async_register_static_paths([
        StaticPathConfig("/libvirt", "/tmp/libvirt", False)
    ])

    libvirt_config = config.get(DOMAIN, {}) or {}
    ssh_key = libvirt_config.get("ssh_key", DEFAULT_SSH_KEY)
    configured_connections = libvirt_config.get("connections")

    if configured_connections is not None:
        if isinstance(configured_connections, dict):
            if "ssh_host" in configured_connections:
                connection_values = [("default", configured_connections)]
            else:
                connection_values = configured_connections.items()
        elif isinstance(configured_connections, str):
            connection_values = [("default", configured_connections)]
        else:
            connection_values = [(str(index), connection) for index, connection in enumerate(configured_connections)]

        connections = [
            {
                "name": connection_name,
                "ssh_host": connection.get("ssh_host", DEFAULT_SSH_HOST) if isinstance(connection, dict) else connection,
                "ssh_key": connection.get("ssh_key", ssh_key) if isinstance(connection, dict) else ssh_key,
                "uri": connection.get("uri", DEFAULT_URI) if isinstance(connection, dict) else DEFAULT_URI,
            }
            for connection_name, connection in connection_values
        ]
    else:
        connections = []
        for platform in ("sensor", "switch"):
            for entry in config.get(platform, []):
                if entry.get("platform") != DOMAIN:
                    continue
                connection = {
                    "name": entry.get("ssh_host", DEFAULT_SSH_HOST),
                    "ssh_host": entry.get("ssh_host", DEFAULT_SSH_HOST),
                    "ssh_key": entry.get("ssh_key", DEFAULT_SSH_KEY),
                    "uri": entry.get("uri", DEFAULT_URI),
                }
                if connection not in connections:
                    connections.append(connection)

    vm_map = {}
    for connection in connections:
        try:
            output = run_virsh(["list", "--all", "--name"], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
            vm_names = [line.strip() for line in output.splitlines() if line.strip()]
            for vm_name in vm_names:
                vm_map[(connection["name"], vm_name)] = connection
        except Exception as e:
            _LOGGER.error(f"Failed to get VMs from {connection['ssh_host']}: {e}")

    hass.data[DOMAIN] = {
        "connections": connections,
        "vms": vm_map,
    }

    async def handle_vm_screenshot(call):
        name = call.data["name"]
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if not connection:
            return

        local_path = f"/tmp/libvirt/{name}.png"
        success = take_screenshot(
            name,
            connection["ssh_host"],
            local_path,
            uri=connection["uri"],
            ssh_key=connection["ssh_key"],
        )
        if not success:
            _LOGGER.error(f"Failed to take screenshot for {name}")

    async def handle_start_vm(call):
        name = call.data["name"]
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if connection:
            run_virsh(["start", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_shutdown_vm(call):
        name = call.data["name"]
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if connection:
            run_virsh(["shutdown", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_suspend_vm(call):
       name = call.data["name"]
       connection = get_vm_connection(hass, name, call.data.get("connection"))
       if connection:
           run_virsh(["suspend", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
    async def handle_resume_vm(call):
       name = call.data["name"]
       connection = get_vm_connection(hass, name, call.data.get("connection"))
       if connection:
           run_virsh(["resume", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_create_snapshot(call):
        name = call.data["name"]
        snapshot = call.data.get("snapshot", f"{name}_snap")
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if connection:
            run_virsh(["snapshot-create-as", name, snapshot], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
    async def handle_revert_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if connection:
            run_virsh(["snapshot-revert", name, snapshot], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_delete_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        connection = get_vm_connection(hass, name, call.data.get("connection"))
        if connection:
            run_virsh(["snapshot-delete", name, snapshot], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
    # Register services
    hass.services.async_register(DOMAIN, "start_vm", handle_start_vm)
    hass.services.async_register(DOMAIN, "shutdown_vm", handle_shutdown_vm)
    hass.services.async_register(DOMAIN, "suspend_vm", handle_suspend_vm)
    hass.services.async_register(DOMAIN, "resume_vm", handle_resume_vm)
    hass.services.async_register(DOMAIN, "create_snapshot", handle_create_snapshot)
    hass.services.async_register(DOMAIN, "revert_snapshot", handle_revert_snapshot)
    hass.services.async_register(DOMAIN, "delete_snapshot", handle_delete_snapshot)

    hass.services.async_register(DOMAIN, "take_screenshot", handle_vm_screenshot)

    return True