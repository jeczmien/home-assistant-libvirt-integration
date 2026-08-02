# Home Assistant Libvirt Integration

This is a custom integration for Home Assistant to monitor and control virtual machines using `virsh` over SSH.
SSH is used because the libvirt Python libraries do not compile under HAOS.

![Screenshot of dashboard](images/libvirt.png)

## Features

- View domain info and state
- Control power (start, shutdown, suspend, resume)
- Take screenshots from VMs
- Snapshot support
- Multiple libvirt connections
- Configurable SSH key per connection
- Secure communication via an SSH forced-command helper
- One SSH connection per polling cycle and libvirt connection

## Installation via HACS

1. Add this repository to HACS as a custom integration:
   - URL: `https://github.com/Bram-diederik/home-assistant-libvirt-integration`
2. Restart Home Assistant.
3. Configure the integration through YAML.

## SSH key setup

Create an SSH key without a passphrase and make the private key available to Home Assistant. The default path is:

```text
/share/libvirt/ssh_key
```

A different global path can be configured with `libvirt.ssh_key`, and each connection may override it with its own `ssh_key`.

Install the forced-command helper on every libvirt host:

```bash
sudo install -o root -g root -m 0755 opt/ha_virt_protect.sh /opt/ha_virt_protect.sh
```

Add the public key to the remote user's `~/.ssh/authorized_keys` with the helper as the forced command:

```text
command="/opt/ha_virt_protect.sh",no-agent-forwarding,no-user-rc,no-X11-forwarding,no-port-forwarding ssh-ed25519 AAAA... home-assistant-libvirt
```

The helper allows the existing individual `virsh`, `base64`, and `convert` commands and also supports `libvirt-batch`, which is used for periodic polling.

The helper file must be updated on each libvirt host when upgrading from a version that does not support `libvirt-batch`. An old helper will reject the batch command with `Command not allowed`.

## Configuration

```yaml
libvirt:
  ssh_key: "/config/libvirt-ssh.private"
  connections:
    alfa:
      ssh_host: "user@linux-host"
      uri: "qemu:///system"
      frequency: 60
      snapshot_frequency: 5
      screenshot_frequency: 5

sensor:
  - platform: libvirt
    include_interfaces: true

switch:
  - platform: libvirt
```

Each entry under `connections` has its own polling settings:

- `frequency`: interval between polling cycles in seconds; default `60`
- `snapshot_frequency`: collect snapshots every N cycles; default `5`
- `screenshot_frequency`: collect screenshots every N cycles; default `5`

With the defaults, VM state and interfaces are refreshed every 60 seconds, while snapshots and screenshots are refreshed every five cycles, or every five minutes.

Each polling cycle opens one SSH connection for a configured libvirt connection. The remote helper then executes the required `virsh` commands sequentially and returns the collected data in one response.

The VM cache is keyed by:

```text
(connection_name, vm_name)
```

This allows two libvirt hosts to contain VMs with the same name.

## Screenshot files

Screenshots are stored inside Home Assistant Core under:

```text
/tmp/libvirt/<connection_name>_<vm_name>.png
```

They are exposed through the Home Assistant HTTP server at:

```text
/libvirt/<connection_name>_<vm_name>.png
```

For example, VM `docker` from connection `alfa` is available at:

```text
/libvirt/alfa_docker.png
```

The connection name is included in the filename to prevent collisions when different libvirt hosts have VMs with the same name.

The temporary screenshot created on the libvirt host uses the same `<connection_name>_<vm_name>.png` name under `/tmp`. It is encoded into the batch response and removed before the SSH command finishes.

## Dashboard example

Create a text helper and a select helper for snapshot operations.

```yaml
type: vertical-stack
cards:
  - type: picture
    image: /libvirt/alfa_kali.png
  - type: entities
    entities:
      - entity: sensor.libvirt_kali
      - entity: switch.libvirt_kali
      - type: attribute
        entity: sensor.libvirt_kali
        attribute: ip
        name: IP Address
      - entity: input_select.libvirt_kali
      - type: custom:button-card
        name: Revert Snapshot
        icon: mdi:backup-restore
        tap_action:
          action: call-service
          service: libvirt.revert_snapshot
          service_data:
            name: kali
            connection: alfa
            snapshot: '[[[ return states["input_select.libvirt_kali"].state ]]]'
      - entity: input_text.libvirt_kali
      - type: custom:button-card
        name: Make Snapshot
        icon: mdi:camera
        tap_action:
          action: call-service
          service: libvirt.create_snapshot
          service_data:
            name: kali
            connection: alfa
            snapshot: '[[[ return states["input_text.libvirt_kali"].state ]]]'
```

## Services

The integration provides these services:

- `libvirt.start_vm`
- `libvirt.shutdown_vm`
- `libvirt.suspend_vm`
- `libvirt.resume_vm`
- `libvirt.create_snapshot`
- `libvirt.revert_snapshot`
- `libvirt.delete_snapshot`
- `libvirt.take_screenshot`

For installations with more than one libvirt connection, pass `connection` when VM names are not unique:

```yaml
action: libvirt.take_screenshot
data:
  name: docker
  connection: alfa
```
