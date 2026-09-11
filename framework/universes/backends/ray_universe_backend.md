# Ray Universe backend

The `ray` Universe deployment backend starts a Universe runtime as a Ray actor using the optional `ray` Python package.

Ray is imported lazily. The backend remains discoverable when Ray is not installed; launch returns a failed handle with diagnostics if Ray cannot be imported or initialized.

## Runtime contract

The backend writes local runtime files:
- `params.json`
- `status.json`
- `stdout.log`
- `stderr.log`

It launches a detached Ray actor. The actor calls `framework.universes.base_universe.run_app(...)` with validated `BaseUniverseParams`, host, port, CORS list, and status-file path.

Endpoint metadata is discovered from `status.json` and stored on the returned `UniverseHandle`.

## Configuration

Common `backend_config` keys:
- `address`: Ray cluster address, or `RAY_ADDRESS`.
- `namespace`: Ray namespace, defaulting to `WOLF_RAY_NAMESPACE` or `wolf`.
- `runtime_env`: optional Ray runtime environment dictionary.
- `actor_name`: explicit actor name.
- `actor_options`: extra options passed to `ray.remote(...)`.
- `num_cpus` and `num_gpus`: convenience actor resource options.
- `lifetime`: actor lifetime, defaulting to `detached`.
- `host` and `port`: Universe bind endpoint.
- `runtime_dir`: params/status/log directory.
- `startup_timeout_s` and `poll_interval_s`: status polling controls.

## Testing

Unit tests mock Ray and do not require Ray to be installed. They cover registry discovery, missing-Ray diagnostics, actor option construction, endpoint recovery, actor status/log access, and termination with `ray.kill(...)`.

A real Ray integration is not enabled by default. Install Ray and test explicitly before production use.