import os
import subprocess
import logging
import re
import time
import base64
import shutil
import uuid


__all__ = ["get_vm_info", "get_all_vms", "run_virsh", "get_vm_ip", "get_vm_interfaces", "list_snapshots","get_vm_state","start_vm","shutdown_vm","unpause_vm","update_vm_cpu_load", "collect_connection_data", "run_snapshot_action"]

_LOGGER = logging.getLogger(__name__)
DEFAULT_SSH_HOST = "root@localhost"
DEFAULT_SSH_KEY = "/share/libvirt/ssh_key"
DEFAULT_URI = "qemu:///system"
SSH_CONTROL_DIR = "/run/libvirt-ssh" if os.access("/run", os.W_OK) else "/tmp/libvirt-ssh"


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


class SSHSession:
    def __init__(self, connection_name, ssh_host, ssh_key):
        self._connection_name = connection_name
        self._ssh_host = ssh_host
        self._ssh_key = ssh_key
        safe_connection_name = re.sub(r"[^A-Za-z0-9_.-]", "_", connection_name)
        self._socket_path = os.path.join(
            SSH_CONTROL_DIR,
            f"{safe_connection_name}-{uuid.uuid4().hex[:12]}.sock",
        )
        self._host = None
        self._opened = False

    def open(self):
        os.makedirs(SSH_CONTROL_DIR, mode=0o700, exist_ok=True)
        os.chmod(SSH_CONTROL_DIR, 0o700)
        cmd, self._host = _build_ssh_command(self._ssh_host, self._ssh_key)
        cmd.extend([
            "-M",
            "-S",
            self._socket_path,
            "-o",
            "ControlPersist=no",
            "-N",
            "-f",
            self._host,
        ])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        self._opened = True
        return self

    def run(self, args, timeout=10):
        if not self._opened:
            raise RuntimeError("SSH session is not open")
        cmd, host = _build_ssh_command(self._ssh_host, self._ssh_key)
        cmd.extend(["-S", self._socket_path, host])
        cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
        return result.stdout.strip()

    def close(self):
        if self._opened:
            cmd, host = _build_ssh_command(self._ssh_host, self._ssh_key)
            cmd.extend(["-S", self._socket_path, "-O", "exit", host])
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            self._opened = False
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def _run_ssh(ssh_host, ssh_key, args):
    cmd, host = _build_ssh_command(ssh_host, ssh_key)
    cmd.append(host)
    cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
    return result.stdout.strip()


def take_screenshot(vm_name, ssh_host, local_path, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY, session=None):
    if session is None:
        with SSHSession("screenshot", ssh_host, ssh_key) as ssh_session:
            return take_screenshot(vm_name, ssh_host, local_path, uri, ssh_key, ssh_session)

    remote_png = f"/tmp/{vm_name}.png"
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    # Step 1: Try taking the screenshot
    try:
        result = run_virsh(["screenshot", vm_name, remote_png, "--screen", "0"], ssh_host=ssh_host, uri=uri, ssh_key=ssh_key, session=session)
        if result is None:
            raise RuntimeError("VM might be offline or screenshot failed.")
    except Exception:
        # Step 1 fallback: offline image
        fallback = os.path.join(os.path.dirname(__file__), "offline.png")
        try:
            shutil.copyfile(fallback, local_path)
        except Exception as e:
            _LOGGER.error(f"Error copying fallback image: {e}")
        return False
    # Step 2: Fetch and decode
    try:
        output = session.run(["base64", remote_png])
    except subprocess.CalledProcessError as e:
        _LOGGER.error(f"Failed to base64 encode screenshot: {e.stderr}")
        return False
    except Exception as e:
        _LOGGER.error(f"Unexpected error: {e}")
        return False
    try:
        with open(local_path, "wb") as f:
            f.write(base64.b64decode(output))
    except Exception as e:
        _LOGGER.error(f"Failed to write screenshot to {local_path}: {e}")
        return False

    return True



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
    if session is not None:
        return session.run(["virsh", "-c", uri] + args)
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
    with SSHSession("snapshot", ssh_host, ssh_key) as session:
        run_virsh(args, ssh_host, uri, ssh_key, session)
        return list_snapshots(vm_name, ssh_host, uri, ssh_key, session)


def _get_ip_from_interfaces(interfaces):
    for iface in interfaces:
        if iface["protocol"] == "ipv4" and not iface["address"].startswith("127."):
            return iface["address"].split("/")[0]
    return None


def collect_connection_data(connection_name, connection, previous_records, include_snapshots, include_screenshots, screenshot_directory):
    records = {}
    with SSHSession(connection_name, connection["ssh_host"], connection["ssh_key"]) as session:
        vm_names = get_all_vms(connection["ssh_host"], connection["uri"], connection["ssh_key"], session)
        for vm_name in vm_names:
            key = (connection_name, vm_name)
            previous_record = previous_records.get(key, {})
            try:
                info = get_vm_info(vm_name, connection["ssh_host"], connection["uri"], connection["ssh_key"], session)
                if info.get("state") == "running":
                    interfaces = get_vm_interfaces(vm_name, connection["ssh_host"], connection["uri"], connection["ssh_key"], session)
                else:
                    interfaces = []

                snapshots = previous_record.get("snapshots", [])
                if include_snapshots:
                    snapshots = list_snapshots(vm_name, connection["ssh_host"], connection["uri"], connection["ssh_key"], session)

                if include_screenshots:
                    take_screenshot(
                        vm_name,
                        connection["ssh_host"],
                        os.path.join(screenshot_directory, f"{vm_name}.png"),
                        connection["uri"],
                        connection["ssh_key"],
                        session,
                    )

                records[key] = {
                    "connection": connection,
                    "info": info,
                    "interfaces": interfaces,
                    "ip": _get_ip_from_interfaces(interfaces),
                    "snapshots": snapshots,
                }
            except Exception as e:
                _LOGGER.warning(f"Failed to update VM {vm_name} on {connection_name}: {e}")
                if previous_record:
                    records[key] = previous_record

    return records