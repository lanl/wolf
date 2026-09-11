# VM and microVM Universe backend prototypes

The VM/microVM backend prototypes define initial launch contracts for running WOLF Universe runtimes inside stronger isolation boundaries such as QEMU VMs and Firecracker microVMs.

Implemented backend names:

- `vm_qemu`: prototype QEMU process backend.
- `microvm_firecracker`: prototype Firecracker process backend.

These are research/prototype backends. They are registered and test-covered with mocked runtime binaries, but real deployment requires guest images prepared to run WOLF and publish endpoint status back to the host.

## Runtime contract

Both prototype backends write host-side runtime files:

- `params.json`: serialized `BaseUniverseParams`.
- `status.json`: endpoint/status file expected from the guest runtime.
- `stdout.log`: VM/microVM process stdout.
- `stderr.log`: VM/microVM process stderr.

The backend returns a common `UniverseHandle` and polls the status file for endpoint metadata. A real guest image must start:

```bash
python -m framework.universes.run_universe --params-file <params> --status-file <status>
```

or an equivalent guest-side launcher that writes compatible status metadata visible to the host.

## QEMU backend

Backend name: `vm_qemu`.

Common `backend_config` keys:

- `qemu_bin`: QEMU executable, defaulting to `WOLF_QEMU_BIN` or `qemu-system-x86_64`.
- `require_binary`: require QEMU to exist before launch. Defaults to true.
- `disk_image` or `image`: VM disk image path.
- `disk_format`: disk format, defaulting to `qcow2`.
- `kernel`: optional direct kernel path.
- `initrd`: optional initrd path.
- `append`: additional kernel command-line arguments.
- `memory`: memory size, defaulting to `1024M`.
- `cpus`: vCPU count, defaulting to `1`.
- `share_runtime_dir`: add a QEMU `virtfs` share for the runtime directory. Defaults to true.
- `guest_port`: guest port to expose through user networking.
- `host_forward_host`: host bind address for forwarded port. Defaults to `127.0.0.1`.
- `host_forward_port`: host port for forwarded guest service.
- `extra_qemu_args`: additional QEMU CLI arguments.
- `startup_timeout_s` and `poll_interval_s`: status polling controls.

The QEMU prototype fails fast when neither `disk_image`/`image` nor `kernel` is provided, unless `allow_no_image=true` is set for dry/mock launches.

## Firecracker backend

Backend name: `microvm_firecracker`.

Common `backend_config` keys:

- `firecracker_bin`: Firecracker executable, defaulting to `WOLF_FIRECRACKER_BIN` or `firecracker`.
- `require_binary`: require Firecracker to exist before launch. Defaults to true.
- `kernel_image` or `kernel`: guest kernel image path.
- `rootfs` or `rootfs_image`: guest root filesystem image path.
- `rootfs_read_only`: whether the rootfs drive is read-only.
- `boot_args`: kernel boot args. The backend appends `wolf.params` and `wolf.status` hints.
- `vcpu_count` or `cpus`: vCPU count.
- `mem_size_mib`: guest memory in MiB.
- `api_sock`: Firecracker API socket path.
- `network_interfaces`: optional Firecracker network interface config.
- `extra_firecracker_args`: extra CLI arguments.
- `startup_timeout_s` and `poll_interval_s`: status polling controls.

The Firecracker prototype writes a generated `*.firecracker.json` config file and fails fast when kernel/rootfs are missing unless `allow_incomplete_config=true` is set for dry/mock launches.

## Limitations

These backends do not yet build images, install WOLF into guests, configure cloud-init, set up SSH, create TAP networking, manage Firecracker jailer isolation, or reattach to already-running VMs.

Endpoint discovery is status-file based. A production VM/microVM backend will likely need one or more of:

- prepared images with WOLF installed,
- virtfs/9p, vsock, SSH, guest-agent, or cloud-init integration,
- host port-forwarding or TAP routing,
- image lifecycle management,
- explicit security policy for mounts, devices, networking, and secrets,
- robust reattach/cleanup semantics.

## Testing

The unit tests mock QEMU and Firecracker binaries. They cover registry discovery, missing-runtime diagnostics, required-configuration validation, QEMU command generation, Firecracker config generation, status-file endpoint recovery, and legacy-compatible deployment metadata. They do not require QEMU or Firecracker to be installed.