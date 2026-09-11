# Sandbox Universe backends

The sandbox Universe backend prototypes run local Universe runtimes through OS sandbox wrapper tools.

Implemented backend names:

- `sandbox_bubblewrap`: uses `bwrap` / bubblewrap.
- `sandbox_nsjail`: uses `nsjail`.

These are prototype backends. They are registered and test-covered, but real isolation strength depends on host OS support, tool availability, kernel features, mount policy, and network policy.

## Runtime contract

The backend writes runtime files:

- `params.json`: serialized `BaseUniverseParams`.
- `status.json`: endpoint/status file written by the Universe runtime.
- `stdout.log`: wrapped process stdout.
- `stderr.log`: wrapped process stderr.

It then starts `python -m framework.universes.run_universe` through the selected sandbox wrapper. Endpoint metadata is discovered from `status.json` and stored in the returned `UniverseHandle`.

If the requested sandbox executable is missing, launch returns a failed `UniverseHandle` with diagnostic metadata instead of raising an unhandled exception.

## bubblewrap configuration

Common `backend_config` keys for `sandbox_bubblewrap`:

- `bwrap_bin`: bubblewrap executable, defaulting to `WOLF_BWRAP_BIN` or `bwrap`.
- `require_binary`: require the wrapper executable to exist before launch. Defaults to true.
- `python_bin`: Python executable used inside the wrapper.
- `runtime_dir`: params/status/log directory.
- `host` and `port`: Universe bind endpoint.
- `ro_binds`: additional read-only bind mounts. Each entry may be a path string or a `{source, target}` object.
- `rw_binds`: additional read-write bind mounts. Each entry may be a path string or a `{source, target}` object.
- `tmpfs_tmp`: mount a tmpfs at `/tmp`. Defaults to true.
- `unshare_pid`: add `--unshare-pid`.
- `unshare_ipc`: add `--unshare-ipc`.
- `unshare_net`: add `--unshare-net`.
- `startup_timeout_s` and `poll_interval_s`: status polling controls.

The current project directory is mounted read-only so the Universe module can be imported. The runtime directory is mounted read-write so params/status/log files can be exchanged.

## nsjail configuration

Common `backend_config` keys for `sandbox_nsjail`:

- `nsjail_bin`: nsjail executable, defaulting to `WOLF_NSJAIL_BIN` or `nsjail`.
- `require_binary`: require the wrapper executable to exist before launch. Defaults to true.
- `python_bin`: Python executable used inside the wrapper.
- `runtime_dir`: params/status/log directory.
- `mode`: nsjail mode, defaulting to `o`.
- `time_limit`, `max_cpus`, `rlimit_as`: common nsjail resource controls.
- `bindmount_ro`: extra read-only bind mounts.
- `bindmount`: extra read-write bind mounts.

## Notes and limitations

These backends are intentionally conservative prototypes. They do not yet provide a complete security policy language, automatic dependency closure, seccomp profile management, user namespace policy, or cross-platform guarantees.

Network isolation can break endpoint accessibility. If network namespaces are enabled, the Universe may not be reachable through the normal host TCP path without additional routing or proxying.

## Testing

The unit tests mock missing and available sandbox executables, command construction, status-file endpoint discovery, registry discovery, and legacy-compatible handle metadata. They do not require `bwrap` or `nsjail` to be installed.