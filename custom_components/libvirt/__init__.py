from .virsh import run_virsh, is_vm_running, DEFAULT_SSH_HOST, DEFAULT_SSH_KEY, DEFAULT_URI, take_screenshot, get_vm_info, collect_connection_data, run_snapshot_action
import os
import subprocess
import logging
import asyncio
from functools import partial
from datetime import timedelta
from homeassistant.components.http import StaticPathConfig
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval


DOMAIN = "libvirt"
SIGNAL_UPDATE = "libvirt_update"
DEFAULT_FREQUENCY = 60
DEFAULT_SNAPSHOT_FREQUENCY = 5
DEFAULT_SCREENSHOT_FREQUENCY = 5
_LOGGER = logging.getLogger(__name__)


def _get_positive_int(value, default):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def get_vm_record(hass, name, connection_name=None):
    if connection_name:
        return hass.data[DOMAIN]["vms"].get((connection_name, name))

    matches = [
        record
        for (mapped_connection_name, vm_name), record in hass.data[DOMAIN]["vms"].items()
        if vm_name == name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _LOGGER.error(f"VM {name} exists on more than one libvirt connection")
    return None


def _find_vm_records(connections, name, connection_name=None):
    matches = []
    for connection in connections:
        if connection_name and connection["name"] != connection_name:
            continue
        try:
            info = get_vm_info(name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            matches.append((
                (connection["name"], name),
                {
                    "connection": connection,
                    "info": info,
                    "interfaces": [],
                    "ip": None,
                    "snapshots": [],
                },
            ))
        except Exception:
            continue
    return matches


async def async_get_vm_record(hass, name, connection_name=None):
    record = get_vm_record(hass, name, connection_name)
    if record:
        return record

    matches = await hass.async_add_executor_job(
        _find_vm_records,
        hass.data[DOMAIN]["connections"],
        name,
        connection_name,
    )

    for key, found_record in matches:
        hass.data[DOMAIN]["vms"][key] = found_record

    if len(matches) == 1:
        return matches[0][1]
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
                "name": str(connection_name),
                "ssh_host": connection.get("ssh_host", DEFAULT_SSH_HOST) if isinstance(connection, dict) else connection,
                "ssh_key": connection.get("ssh_key", ssh_key) if isinstance(connection, dict) else ssh_key,
                "uri": connection.get("uri", DEFAULT_URI) if isinstance(connection, dict) else DEFAULT_URI,
                "frequency": _get_positive_int(connection.get("frequency") if isinstance(connection, dict) else None, DEFAULT_FREQUENCY),
                "snapshot_frequency": _get_positive_int(connection.get("snapshot_frequency") if isinstance(connection, dict) else None, DEFAULT_SNAPSHOT_FREQUENCY),
                "screenshot_frequency": _get_positive_int(connection.get("screenshot_frequency") if isinstance(connection, dict) else None, DEFAULT_SCREENSHOT_FREQUENCY),
                "cycle": 0,
                "lock": asyncio.Lock(),
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
                    "frequency": _get_positive_int(entry.get("frequency"), DEFAULT_FREQUENCY),
                    "snapshot_frequency": _get_positive_int(entry.get("snapshot_frequency"), DEFAULT_SNAPSHOT_FREQUENCY),
                    "screenshot_frequency": _get_positive_int(entry.get("screenshot_frequency"), DEFAULT_SCREENSHOT_FREQUENCY),
                    "cycle": 0,
                    "lock": asyncio.Lock(),
                }
                if connection not in connections:
                    connections.append(connection)

    hass.data[DOMAIN] = {
        "connections": connections,
        "vms": {},
    }

    async def async_refresh(connection, now=None, initial=False):
        data = hass.data[DOMAIN]
        if connection["lock"].locked():
            return

        async with connection["lock"]:
            cycle = connection["cycle"] if initial else connection["cycle"] + 1
            include_snapshots = initial or cycle % connection["snapshot_frequency"] == 0
            include_screenshots = not initial and cycle % connection["screenshot_frequency"] == 0

            previous_records = {
                key: record
                for key, record in data["vms"].items()
                if key[0] == connection["name"]
            }

            try:
                records = await hass.async_add_executor_job(
                    collect_connection_data,
                    connection["name"],
                    connection,
                    previous_records,
                    include_snapshots,
                    include_screenshots,
                    "/tmp/libvirt",
                )
            except Exception as e:
                _LOGGER.error(f"Failed to get VMs from {connection['ssh_host']}: {e}")
                records = previous_records

            for key in list(data["vms"]):
                if key[0] == connection["name"]:
                    del data["vms"][key]

            data["vms"].update(records)

            if not initial:
                connection["cycle"] = cycle

            async_dispatcher_send(hass, SIGNAL_UPDATE)

    hass.data[DOMAIN]["async_refresh"] = async_refresh

    for connection in connections:
        await async_refresh(connection, initial=True)

    hass.data[DOMAIN]["remove_intervals"] = [
        async_track_time_interval(
            hass,
            partial(async_refresh, connection),
            timedelta(seconds=connection["frequency"]),
        )
        for connection in connections
    ]

    async def handle_vm_screenshot(call):
        name = call.data["name"]
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if not record:
            return

        connection = record["connection"]
        local_path = f"/tmp/libvirt/{name}.png"
        success = await hass.async_add_executor_job(
            take_screenshot,
            name,
            connection["ssh_host"],
            local_path,
            connection["uri"],
            connection["ssh_key"],
        )
        if not success:
            _LOGGER.error(f"Failed to take screenshot for {name}")

    async def handle_start_vm(call):
        name = call.data["name"]
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if record:
            connection = record["connection"]
            await hass.async_add_executor_job(run_virsh, ["start", name], connection["ssh_host"], connection["uri"], connection["ssh_key"])
            record["info"]["state"] = "running"
            async_dispatcher_send(hass, SIGNAL_UPDATE)

    async def handle_shutdown_vm(call):
        name = call.data["name"]
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if record:
            connection = record["connection"]
            await hass.async_add_executor_job(run_virsh, ["shutdown", name], connection["ssh_host"], connection["uri"], connection["ssh_key"])
            record["info"]["state"] = "shut off"
            async_dispatcher_send(hass, SIGNAL_UPDATE)

    async def handle_suspend_vm(call):
       name = call.data["name"]
       record = await async_get_vm_record(hass, name, call.data.get("connection"))
       if record:
           connection = record["connection"]
           await hass.async_add_executor_job(run_virsh, ["suspend", name], connection["ssh_host"], connection["uri"], connection["ssh_key"])
           record["info"]["state"] = "paused"
           async_dispatcher_send(hass, SIGNAL_UPDATE)
    async def handle_resume_vm(call):
       name = call.data["name"]
       record = await async_get_vm_record(hass, name, call.data.get("connection"))
       if record:
           connection = record["connection"]
           await hass.async_add_executor_job(run_virsh, ["resume", name], connection["ssh_host"], connection["uri"], connection["ssh_key"])
           record["info"]["state"] = "running"
           async_dispatcher_send(hass, SIGNAL_UPDATE)

    async def handle_create_snapshot(call):
        name = call.data["name"]
        snapshot = call.data.get("snapshot", f"{name}_snap")
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if record:
            connection = record["connection"]
            record["snapshots"] = await hass.async_add_executor_job(run_snapshot_action, ["snapshot-create-as", name, snapshot], name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            async_dispatcher_send(hass, SIGNAL_UPDATE)
    async def handle_revert_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if record:
            connection = record["connection"]
            record["snapshots"] = await hass.async_add_executor_job(run_snapshot_action, ["snapshot-revert", name, snapshot], name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            async_dispatcher_send(hass, SIGNAL_UPDATE)

    async def handle_delete_snapshot(call):
        name = call.data["name"]
        snapshot = call.data["snapshot"]
        record = await async_get_vm_record(hass, name, call.data.get("connection"))
        if record:
            connection = record["connection"]
            record["snapshots"] = await hass.async_add_executor_job(run_snapshot_action, ["snapshot-delete", name, snapshot], name, connection["ssh_host"], connection["uri"], connection["ssh_key"])
            async_dispatcher_send(hass, SIGNAL_UPDATE)
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