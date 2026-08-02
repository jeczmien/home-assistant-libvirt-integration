import os
import subprocess
import logging
import re
import time
import base64
import shutil
import tempfile


__all__ = ["get_vm_info", "get_all_vms", "run_virsh", "get_vm_ip", "get_vm_interfaces", "list_snapshots","get_vm_state","start_vm","shutdown_vm","unpause_vm","update_vm_cpu_load", "collect_connection_data", "run_snapshot_action"]

_LOGGER = logging.getLogger(__name__)
DEFAULT_SSH_HOST = "root@localhost"
DEFAULT_SSH_KEY = "/share/libvirt/ssh_key"
DEFAULT_URI = "qemu:///system"
BATCH_HEADER = "LIBVIRT_BATCH_V1"


def _validate_ssh_key(ssh_key):
    if not os.path.isfile(ssh_key):
        raise FileNotFoundError(f"SSH key does not exist: {ssh_key}")
    if not os.access(ssh_key, os.R_OK):
        raise PermissionError(f"SSH key is not readable: {ssh_key}")


def _parse_ssh_host(ssh_host):
    match = re.fullmatch(r"((?:[^@]+@)?\[[^]]+\]):([0-9]+)", ssh_host)
    if match:
        return match.group(1), match.group(2)

    if ssh_host.count(":") == 1:
        host, port = ssh_host.rsplit(":", 1)
        if port.isdigit():
            return host, port

    return ssh_host, None


def _build_ssh_command(ssh_host, ssh_key):
    _validate_ssh_key(ssh_key)
    host, port = _parse_ssh_host(ssh_host)
    cmd = [
        "/usr/bin/ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "BatchMode=yes",
        "-i",
        ssh_key,
    ]
    if port:
        cmd.extend(["-p", port])
    return cmd, host


def _run_ssh(ssh_host, ssh_key, args, timeout=10):
    cmd, host = _build_ssh_command(ssh_host, ssh_key)
    cmd.append(host)
    cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
    return result.stdout.strip()


def _decode_batch_field(value):
    if not value:
        return ""
    return base64.b64decode(value).decode("utf-8", errors="replace")


def _write_batch_screenshot(local_path, status, encoded_screenshot):
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    fallback = os.path.join(os.path.dirname(__file__), "offline.png")

    if status == "ok" and encoded_screenshot:
        with open(local_path, "wb") as f:
            f.write(base64.b64decode(encoded_screenshot))
        return True

    if status == "offline":
        shutil.copyfile(fallback, local_path)
        return False

    if status.startswith("error:"):
        _LOGGER.error(f"Failed to take screenshot: {status[6:]}")
        if not os.path.exists(local_path):
            shutil.copyfile(fallback, local_path)
        return False

    return False


def _parse_batch_output(output, connection_name, connection, previous_records, include_snapshots, include_screenshots, screenshot_directory):
    lines = output.splitlines()
    if not lines or lines[0] != BATCH_HEADER:
        raise RuntimeError(f"Remote helper does not support {BATCH_HEADER}")

    records = {}
    for line in lines[1:]:
        if not line:
            continue

        parts = line.split("\t")
        if parts[0] == "ERROR":
            message = _decode_batch_field(parts[1]) if len(parts) > 1 else "Unknown batch error"
            raise RuntimeError(message)
        if parts[0] != "VM" or len(parts) != 7:
            raise RuntimeError(f"Invalid batch response line: {line[:100]}")

        vm_name = _decode_batch_field(parts[1])
        info_output = _decode_batch_field(parts[2])
        interfaces_output = _decode_batch_field(parts[3])
        snapshots_output = _decode_batch_field(parts[4])
        screenshot_status = _decode_batch_field(parts[5])
        screenshot_data = "" if parts[6] == "-" else parts[6]
        key = (connection_name, vm_name)
        previous_record = previous_records.get(key, {})

        info = {}
        for info_line in info_output.splitlines():
            if ":" in info_line:
                info_key, info_value = info_line.split(":", 1)
                info[normalize_key(info_key.strip())] = info_value.strip()

        interfaces = []
        header_found = False
        for interface_line in interfaces_output.splitlines():
            if re.match(r"^\s*-+\s*$", interface_line):
                header_found = True
                continue
            if not header_found or not interface_line.strip():
                continue
            interface_parts = interface_line.split()
            if len(interface_parts) < 4:
                continue
            iface, mac, proto, addr = interface_parts[:4]
            interfaces.append({
                "name": iface,
                "mac": mac,
                "protocol": proto,
                "address": addr,
            })

        snapshots = previous_record.get("snapshots", [])
        if include_snapshots:
            snapshots = []
            snapshot_lines = snapshots_output.splitlines()[2:]
            for snapshot_line in snapshot_lines:
                snapshot_parts = snapshot_line.strip().split()
                if len(snapshot_parts) >= 1:
                    snapshots.append({
                        "name": snapshot_parts[0],
                        "created": snapshot_parts[1] if len(snapshot_parts) > 1 else None,
                        "state": snapshot_parts[2] if len(snapshot_parts) > 2 else None
                    })

        if include_screenshots:
            _write_batch_screenshot(
                os.path.join(screenshot_directory, f"{connection_name}_{vm_name}.png"),
                screenshot_status,
                screenshot_data,
            )

        records[key] = {
            "connection": connection,
            "info": info,
            "interfaces": interfaces,
            "ip": _get_ip_from_interfaces(interfaces),
            "snapshots": snapshots,
            "_screenshot_status": screenshot_status,
        }

    return records


def _collect_connection_data_batch(connection_name, connection, previous_records, include_snapshots, include_screenshots, screenshot_directory, vm_name=None):
    args = [
        "libvirt-batch",
        connection["uri"],
        connection_name,
        "1" if include_snapshots else "0",
        "1" if include_screenshots else "0",
    ]
    if vm_name:
        args.append(vm_name)

    cmd, host = _build_ssh_command(connection["ssh_host"], connection["ssh_key"])
    cmd.append(host)
    cmd.extend(args)

    records = {}
    with tempfile.TemporaryFile(mode="w+t") as stderr_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
            bufsize=1,
        )
        try:
            header = process.stdout.readline().rstrip("\r\n")
            if header != BATCH_HEADER:
                raise RuntimeError(f"Remote helper does not support {BATCH_HEADER}")

            for line in process.stdout:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                records.update(_parse_batch_output(
                    f"{BATCH_HEADER}\n{line}",
                    connection_name,
                    connection,
                    previous_records,
                    include_snapshots,
                    include_screenshots,
                    screenshot_directory,
                ))

            returncode = process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read()
            if returncode != 0:
                raise subprocess.CalledProcessError(
                    returncode,
                    process.args,
                    stderr=stderr,
                )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

    return records


def take_screenshot(vm_name, ssh_host, local_path, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, connection_name="default"):
    connection = {
        "name": connection_name,
        "ssh_host": ssh_host,
        "ssh_key": ssh_key,
        "uri": uri,
    }
    records = _collect_connection_data_batch(
        connection_name,
        connection,
        {},
        False,
        True,
        os.path.dirname(local_path),
        vm_name,
    )
    record = records.get((connection_name, vm_name))
    return bool(record and record.get("_screenshot_status") == "ok" and os.path.exists(local_path))



def is_vm_running(vm_name, ssh_host=None, uri=None, ssh_key=DEFAULT_SSH_KEY, session=None):
    output = run_virsh(["dominfo", vm_name], ssh_host=ssh_host, uri=uri, ssh_key=ssh_key, session=session)
    for line in output.splitlines():
        if line.startswith("State:"):
            return "running" in line.lower()
    return False
def get_vm_state(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY, session=None):
    output = run_virsh(["domstate", vm_name], ssh_host, uri, ssh_key, session)
    return output.strip()

def start_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY, session=None):
    run_virsh(["start", vm_name], ssh_host, uri, ssh_key, session)

def shutdown_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY, session=None):
    run_virsh(["shutdown", vm_name], ssh_host, uri, ssh_key, session)

def unpause_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY, session=None):
    run_virsh(["resume", vm_name], ssh_host, uri, ssh_key, session)


def normalize_key(key):
    return key.lower().replace(" ", "_")

def run_virsh(args, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    return _run_ssh(ssh_host, ssh_key, ["virsh", "-c", uri] + args)

def get_all_vms(ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    output = run_virsh(["list", "--all", "--name"], ssh_host, uri, ssh_key, session)
    return [line.strip() for line in output.splitlines() if line.strip()]
def get_vm_info(name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    output = run_virsh(["dominfo", name], ssh_host, uri, ssh_key, session)
    data = {}
    for line in output.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            data[normalize_key(key.strip())] = val.strip()
    return data

def get_vm_interfaces(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    try:
        output = run_virsh(["domifaddr", vm_name, "--source", "agent"], ssh_host, uri, ssh_key, session)
    except subprocess.CalledProcessError as e:
        # Exit code 1 means VM is likely off — this is expected, so ignore it
        if e.returncode == 1:
            return []
        else:
            raise  # re-raise unexpected errors
    interfaces = []
    if not output:
        return interfaces

    lines = output.splitlines()
    header_found = False
    for line in lines:
        if re.match(r"^\s*-+\s*$", line):
            header_found = True
            continue
        if not header_found or not line.strip():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, mac, proto, addr = parts[:4]
        interfaces.append({
            "name": iface,
            "mac": mac,
            "protocol": proto,
            "address": addr,
        })

    return interfaces
def get_vm_ip(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    interfaces = get_vm_interfaces(vm_name, ssh_host, uri, ssh_key, session)
    for iface in interfaces:
        if iface["protocol"] == "ipv4" and not iface["address"].startswith("127."):
            return iface["address"].split("/")[0]
    return None
def list_snapshots(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    try:
        output = run_virsh(["snapshot-list", "--domain", vm_name], ssh_host, uri, ssh_key, session)
        lines = output.splitlines()[2:]  # Skip headers
        snapshots = []
        for line in lines:
            parts = line.strip().split()
            if len(parts) >= 1:
                snapshots.append({
                    "name": parts[0],
                    "created": parts[1] if len(parts) > 1 else None,
                    "state": parts[2] if len(parts) > 2 else None
                })
        return snapshots
    except Exception as e:
        print(f"⚠ Failed to list snapshots for {vm_name}: {e}")
        return []


def run_snapshot_action(args, vm_name, ssh_host, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    run_virsh(args, ssh_host, uri, ssh_key)
    return list_snapshots(vm_name, ssh_host, uri, ssh_key)


def _get_ip_from_interfaces(interfaces):
    for iface in interfaces:
        if iface["protocol"] == "ipv4" and not iface["address"].startswith("127."):
            return iface["address"].split("/")[0]
    return None


def collect_connection_data(connection_name, connection, previous_records, include_snapshots, include_screenshots, screenshot_directory):
    return _collect_connection_data_batch(
        connection_name,
        connection,
        previous_records,
        include_snapshots,
        include_screenshots,
        screenshot_directory,
    )