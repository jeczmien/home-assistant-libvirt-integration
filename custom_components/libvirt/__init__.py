from .virsh import ensure_ssh_wrapper, SSH_WRAPPER, run_virsh, is_vm_running, DEFAULT_SSH_KEY, DEFAULT_URI, take_screenshot
import os
import subprocess
import logging


DOMAIN = "libvirt"
_LOGGER = logging.getLogger(__name__)

async def async_setup(hass, config):
    ensure_ssh_wrapper()
    ssh_map = {}
    # Get ssh_host values from switch/sensor platform config
    for platform in ("switch", "sensor"):
        for entry in config.get(platform, []):
            if isinstance(entry, dict) and entry.get("platform") == DOMAIN:
                ssh_host = entry.get("ssh_host")
                ssh_key = entry.get("ssh_key", DEFAULT_SSH_KEY)
                if not ssh_host:
                    continue
                try:
                    output = run_virsh(["list", "--all", "--name"], ssh_host=ssh_host, uri=DEFAULT_URI, ssh_key=ssh_key)
                    vm_names = [line.strip() for line in output.splitlines() if line.strip()]
                    for vm_name in vm_names:
                        ssh_map[vm_name] = {
                            "ssh_host": ssh_host,
                            "ssh_key": ssh_key
                        }
                except Exception as e:
                    _LOGGER.error(f"Failed to get VMs from {ssh_host}: {e}")
    hass.data[DOMAIN] = ssh_map

    def get_ssh_host(vm_name):
        vm_data = hass.data.get(DOMAIN, {}).get(vm_name)
        if not vm_data:
            _LOGGER.error(f"No SSH host configured for VM: {vm_name}")
            raise RuntimeError(f"No SSH host configured for VM: {vm_name}")
        return vm_data["ssh_host"]

    def get_ssh_key(vm_name):
        vm_data = hass.data.get(DOMAIN, {}).get(vm_name)
        if not vm_data:
            _LOGGER.error(f"No SSH key configured for VM: {vm_name}")
            raise RuntimeError(f"No SSH key configured for VM: {vm_name}")
        return vm_data["ssh_key"]

    async def handle_vm_screenshot(call):
        name = call.data["name"]
        try:
            ssh_host = get_ssh_host(name)
            ssh_key = get_ssh_key(name)
        except RuntimeError:
            return

        local_path = f"/config/www/libvirt/{name}.png"
        success = take_screenshot(name, ssh_host, local_path, ssh_key)
        if not success:
            _LOGGER.error(f"Failed to take screenshot for {name}")

    async def handle_start_vm(call):
        name = call.data["name"]
        ssh_host = get_ssh_host(name)
        ssh_key = get_ssh_key(name)
        run_virsh(["start", name], ssh_host=ssh_host, ssh_key=ssh_key)

    async def handle_shutdown_vm(call):
        name = call.data["name"]
        ssh_host = get_ssh_host(name)
        ssh_key = get_ssh_key(name)
        run_virsh(["shutdown", name], ssh_host=ssh_host, ssh_key=ssh_key)

    async def handle_suspend_vm(call):
       name = call.data["name"]
       ssh_host = get_ssh_host(name)
       ssh_key = get_ssh_key(name)
       run_virsh(["suspend", name], ssh_host=ssh_host, ssh_key=ssh_key)
    async def handle_resume_vm(call):
       name = call.data["name"]
       ssh_host = get_ssh_host(name)
       ssh_key = get_ssh_key(name)
       run_virsh(["resume", name], ssh_host=ssh_host, ssh_key=ssh_key)

    async def handle_create_snapshot(call):
        name = call.data["name"]
        ssh_host = get_ssh_host(name)
        ssh_key = get_ssh_key(name)
        snapshot = call.data.get("snapshot", f"{name}_snap")
        run_virsh(["snapshot-create-as", name, snapshot], ssh_host=ssh_host, ssh_key=ssh_key)
    async def handle_revert_snapshot(call):
        name = call.data["name"]
        ssh_host = get_ssh_host(name)
        ssh_key = get_ssh_key(name)
        snapshot = call.data["snapshot"]
        run_virsh(["snapshot-revert", name, snapshot], ssh_host=ssh_host, ssh_key=ssh_key)

    async def handle_delete_snapshot(call):
        name = call.data["name"]
        ssh_host = get_ssh_host(name)
        ssh_key = get_ssh_key(name)
        snapshot = call.data["snapshot"]
        run_virsh(["snapshot-delete", name, snapshot], ssh_host=ssh_host, ssh_key=ssh_key)
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