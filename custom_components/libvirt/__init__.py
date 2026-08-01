from .virsh import run_virsh, is_vm_running, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI, take_screenshot
import os
import subprocess
import logging


DOMAIN = "libvirt"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    libvirt_config = config.get(DOMAIN, {}) or {}
    ssh_key = libvirt_config.get("ssh_key", DEFAULT_SSH_KEY)
    configured_connections = libvirt_config.get("connections")

    if configured_connections is not None:
        connection_values = configured_connections.values() if isinstance(configured_connections, dict) else configured_connections
        if isinstance(connection_values, str):
            connection_values = [connection_values]
        connections = [
            {
                "ssh_host": connection.get("ssh_host", DEFAULT_SSH_HOST) if isinstance(connection, dict) else connection,
                "ssh_key": ssh_key,
                "uri": connection.get("uri", DEFAULT_URI) if isinstance(connection, dict) else DEFAULT_URI,
            }
            for connection in connection_values
        ]
    else:
        connections = []
        for platform in ("sensor", "switch"):
            for entry in config.get(platform, []):
                if entry.get("platform") != DOMAIN:
                    continue
                connection = {
                    "ssh_host": entry.get("ssh_host", DEFAULT_SSH_HOST),
                    "ssh_key": entry.get("ssh_key", DEFAULT_SSH_KEY),
                    "uri": entry.get("uri", DEFAULT_URI),
                }
                if connection not in connections:
                    connections.append(connection)

    ssh_map = {}
    for connection in connections:
        ssh_host = connection["ssh_host"]
        ssh_key = connection["ssh_key"]
        uri = connection["uri"]
        try:
            output = run_virsh(["list", "--all", "--name"], ssh_host=ssh_host, uri=uri, ssh_key=ssh_key)
            vm_names = [line.strip() for line in output.splitlines() if line.strip()]
            for vm_name in vm_names:
                ssh_map[vm_name] = connection
        except Exception as e:
            _LOGGER.error(f"Failed to get VMs from {ssh_host}: {e}")

    hass.data[DOMAIN] = {
        "connections": connections,
        "vms": ssh_map,
    }

    def get_vm_connection(name):
        connection = hass.data[DOMAIN]["vms"].get(name)
        if not connection:
            _LOGGER.error(f"No SSH host configured for VM: {name}")
        return connection

    async def handle_vm_screenshot(call):
        name = call.data["name"]
        connection = get_vm_connection(name)
        if not connection:
            return

        local_path = f"/config/www/libvirt/{name}.png"
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
        connection = get_vm_connection(name)
        if connection:
            run_virsh(["start", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_shutdown_vm(call):
        name = call.data["name"]
        connection = get_vm_connection(name)
        if connection:
            run_virsh(["shutdown", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_suspend_vm(call):
       name = call.data["name"]
       connection = get_vm_connection(name)
       if connection:
           run_virsh(["suspend", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
    async def handle_resume_vm(call):
       name = call.data["name"]
       connection = get_vm_connection(name)
       if connection:
           run_virsh(["resume", name], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_create_snapshot(call):
        name = call.data["name"]
        snapshot = call.data.get("snapshot", f"{name}_snap")
        connection = get_vm_connection(name)
        if connection:
            run_virsh(["snapshot-create-as", name, snapshot], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])
    async def handle_revert_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        connection = get_vm_connection(name)
        if connection:
            run_virsh(["snapshot-revert", name, snapshot], ssh_host=connection["ssh_host"], uri=connection["uri"], ssh_key=connection["ssh_key"])

    async def handle_delete_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        connection = get_vm_connection(name)
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