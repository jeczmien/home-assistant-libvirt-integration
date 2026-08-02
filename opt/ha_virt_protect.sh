#!/bin/bash

set -u
export LC_ALL=C

cmd="${SSH_ORIGINAL_COMMAND:-}"
current_screenshot_file=""

cleanup() {
  if [[ -n "$current_screenshot_file" ]]; then
    rm -f -- "$current_screenshot_file"
  fi
}

trap cleanup EXIT HUP INT TERM

encode() {
  printf '%s' "$1" | base64 | tr -d '\n'
}

valid_name() {
  [[ "$1" =~ ^[a-zA-Z0-9._-]+$ ]]
}

run_batch() {
  local uri="$1"
  local connection_name="$2"
  local include_snapshots="$3"
  local include_screenshots="$4"
  local requested_vm="${5:-}"
  local vm_output
  local vm

  if [[ "$uri" != "qemu:///system" ]]; then
    echo "Invalid libvirt URI" >&2
    exit 2
  fi
  if ! valid_name "$connection_name"; then
    echo "Invalid connection name" >&2
    exit 2
  fi
  if [[ "$include_snapshots" != "0" && "$include_snapshots" != "1" ]]; then
    echo "Invalid snapshot flag" >&2
    exit 2
  fi
  if [[ "$include_screenshots" != "0" && "$include_screenshots" != "1" ]]; then
    echo "Invalid screenshot flag" >&2
    exit 2
  fi
  if [[ -n "$requested_vm" ]] && ! valid_name "$requested_vm"; then
    echo "Invalid VM name" >&2
    exit 2
  fi

  if [[ -n "$requested_vm" ]]; then
    vm_output="$requested_vm"
  else
    if ! vm_output=$(virsh -c "$uri" list --all --name 2>&1); then
      printf 'LIBVIRT_BATCH_V1\nERROR\t%s\n' "$(encode "$vm_output")"
      exit 0
    fi
  fi

  printf 'LIBVIRT_BATCH_V1\n'

  while IFS= read -r vm; do
    [[ -z "$vm" ]] && continue
    valid_name "$vm" || continue

    local info=""
    local interfaces=""
    local snapshots=""
    local state=""
    local screenshot_status="skipped"
    local screenshot_data=""
    local screenshot_file="/tmp/${connection_name}_${vm}.png"
    local screenshot_error=""

    if ! info=$(virsh -c "$uri" dominfo "$vm" 2>&1); then
      printf 'ERROR\t%s\n' "$(encode "Failed to read dominfo for $vm: $info")"
      continue
    fi

    state=$(printf '%s\n' "$info" | awk -F: '/^State:/ {sub(/^[[:space:]]+/, "", $2); print $2; exit}')

    if [[ "$state" == "running" ]]; then
      interfaces=$(virsh -c "$uri" domifaddr "$vm" --source agent 2>/dev/null || true)
    fi

    if [[ "$include_snapshots" == "1" ]]; then
      snapshots=$(virsh -c "$uri" snapshot-list --domain "$vm" 2>/dev/null || true)
    fi

    if [[ "$include_screenshots" == "1" ]]; then
      if [[ "$state" == "running" ]]; then
        current_screenshot_file="$screenshot_file"
        rm -f -- "$screenshot_file"
        if screenshot_error=$(timeout 15s virsh -c "$uri" screenshot "$vm" "$screenshot_file" --screen 0 2>&1); then
          if [[ -s "$screenshot_file" ]]; then
            screenshot_status="ok"
            screenshot_data=$(base64 "$screenshot_file" | tr -d '\n')
          else
            screenshot_status="error:Screenshot file is empty"
          fi
        else
          screenshot_status="error:$screenshot_error"
        fi
        rm -f -- "$screenshot_file"
        current_screenshot_file=""
      else
        screenshot_status="offline"
      fi
    fi

    printf 'VM\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$(encode "$vm")" \
      "$(encode "$info")" \
      "$(encode "$interfaces")" \
      "$(encode "$snapshots")" \
      "$(encode "$screenshot_status")" \
      "${screenshot_data:--}"
  done <<< "$vm_output"
}

if [[ "$cmd" =~ ^libvirt-batch\ qemu:///system\ [a-zA-Z0-9._-]+\ [01]\ [01]($|\ [a-zA-Z0-9._-]+$) ]]; then
  read -r -a args <<< "$cmd"
  run_batch "${args[1]}" "${args[2]}" "${args[3]}" "${args[4]}" "${args[5]:-}"
# Allow specific virsh commands with qemu:///system only
elif [[ "$cmd" =~ ^virsh\ -c\ qemu:///system\ (list\ --all\ --name|dominfo\ [a-zA-Z0-9._-]+|domstate\ [a-zA-Z0-9._-]+|start\ [a-zA-Z0-9._-]+|shutdown\ [a-zA-Z0-9._-]+|suspend\ [a-zA-Z0-9._-]+|resume\ [a-zA-Z0-9._-]+|snapshot-create-as\ [a-zA-Z0-9._-]+\ [a-zA-Z0-9._-]+|snapshot-revert\ [a-zA-Z0-9._-]+\ [a-zA-Z0-9._-]+|snapshot-delete\ [a-zA-Z0-9._-]+\ [a-zA-Z0-9._-]+|snapshot-list\ --domain\ [a-zA-Z0-9._-]+|domifaddr\ [a-zA-Z0-9._-]+\ --source\ agent|screenshot\ [a-zA-Z0-9._-]+\ /tmp/[a-zA-Z0-9._-]+\.(ppm|png)\ --screen\ 0)$ ]]; then
  read -r -a args <<< "$cmd"
  "${args[@]}"
# Allow only base64 on files in /tmp ending in .png
elif [[ "$cmd" =~ ^base64\ /tmp/[a-zA-Z0-9._-]+\.png$ ]]; then
  read -r -a args <<< "$cmd"
  "${args[@]}"

# Allow only convert from .ppm to .png in /tmp
elif [[ "$cmd" =~ ^convert\ /tmp/[a-zA-Z0-9._-]+\.ppm\ /tmp/[a-zA-Z0-9._-]+\.png$ ]]; then
  read -r -a args <<< "$cmd"
  "${args[@]}"

# Deny everything else
else
  echo "Command not allowed"
fi