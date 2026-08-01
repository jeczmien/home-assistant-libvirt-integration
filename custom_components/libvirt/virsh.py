import os
import subprocess
import logging
import re
import time
import base64
import shutil


__all__ = ["get_vm_info", "get_all_vms", "run_virsh", "get_vm_ip", "get_vm_interfaces", "list_snapshots","get_vm_state","start_vm","shutdown_vm","unpause_vm","update_vm_cpu_load"]

_LOGGER = logging.getLogger(__name__)
DEFAULT_SSH_HOST = "root@localhost"
DEFAULT_SSH_KEY = "/share/libvirt/ssh_key"
DEFAULT_URI = "qemu:///system"


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


def _run_ssh(ssh_host, ssh_key, args):
    _validate_ssh_key(ssh_key)
    host, port = _parse_ssh_host(ssh_host)
    cmd = [
        "/usr/bin/ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-i",
        ssh_key,
    ]
    if port:
        cmd.extend(["-p", port])
    cmd.append(host)
    cmd.extend(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args, output=result.stdout, stderr=result.stderr)
    return result.stdout.strip()


def take_screenshot(vm_name, ssh_host, local_path, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    remote_ppm = f"/tmp/{vm_name}.ppm"
    remote_png = f"/tmp/{vm_name}.png"
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    # Step 1: Try taking the screenshot
    try:
        result = run_virsh(["screenshot", vm_name, remote_ppm, "--screen", "0"], ssh_host=ssh_host, uri=uri, ssh_key=ssh_key)
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
    # Step 2: Convert PPM to PNG
    try:
        _run_ssh(ssh_host, ssh_key, ["convert", remote_ppm, remote_png])
    except subprocess.CalledProcessError as e:
        _LOGGER.error(f"Failed to convert screenshot to PNG: {e.stderr}")
        return False
    except Exception as e:
        _LOGGER.error(f"Unexpected error during conversion: {e}")
        return False
    # Step 3: Fetch and decode
    try:
        output = _run_ssh(ssh_host, ssh_key, ["base64", remote_png])
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



def is_vm_running(vm_name, ssh_host=None, uri=None, ssh_key=DEFAULT_SSH_KEY):
    output = run_virsh(["dominfo", vm_name], ssh_host=ssh_host, uri=uri, ssh_key=ssh_key)
    for line in output.splitlines():
        if line.startswith("State:"):
            return "running" in line.lower()
    return False
def get_vm_state(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY):
    output = run_virsh(["domstate", vm_name], ssh_host, uri, ssh_key)
    return output.strip()

def start_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY):
    run_virsh(["start", vm_name], ssh_host, uri, ssh_key)

def shutdown_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY):
    run_virsh(["shutdown", vm_name], ssh_host, uri, ssh_key)

def unpause_vm(vm_name, ssh_host, uri, ssh_key=DEFAULT_SSH_KEY):
    run_virsh(["resume", vm_name], ssh_host, uri, ssh_key)


def normalize_key(key):
    return key.lower().replace(" ", "_")

def run_virsh(args, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    return _run_ssh(ssh_host, ssh_key, ["virsh", "-c", uri] + args)

def get_all_vms(ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    output = run_virsh(["list", "--all", "--name"], ssh_host, uri, ssh_key)
    return [line.strip() for line in output.splitlines() if line.strip()]
def get_vm_info(name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    output = run_virsh(["dominfo", name], ssh_host, uri, ssh_key)
    data = {}
    for line in output.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            data[normalize_key(key.strip())] = val.strip()
    return data

def get_vm_interfaces(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    try:
        output = run_virsh(["domifaddr", vm_name, "--source", "agent"], ssh_host, uri, ssh_key)
    except subprocess.CalledProcessError as e:
        # Exit code 1 means VM is likely off — this is expected, so ignore it
        if e.returncode == 1:
            return []
        else:
            raise  # re-raise unexpected errors
    except subprocess.CalledProcessError:
        # VM probably not running or no agent — ignore
        return []
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
def get_vm_ip(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    interfaces = get_vm_interfaces(vm_name, ssh_host, uri, ssh_key)
    for iface in interfaces:
        if iface["protocol"] == "ipv4" and not iface["address"].startswith("127."):
            return iface["address"].split("/")[0]
    return None
def list_snapshots(vm_name, ssh_host=DEFAULT_SSH_HOST, uri=DEFAULT_URI, ssh_key=DEFAULT_SSH_KEY):
    try:
        output = run_virsh(["snapshot-list", "--domain", vm_name], ssh_host, uri, ssh_key)
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