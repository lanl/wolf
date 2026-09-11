# Slurm Universe backend

The `slurm` Universe deployment backend starts a Universe runtime as a Slurm batch job using Slurm CLI tools: `sbatch`, `squeue`, and `scancel`.

The backend is dependency-free and does not require a Python Slurm client. Unit tests mock Slurm commands and do not require a local Slurm installation.

## Runtime contract

The backend writes local or shared runtime files:

- `params.json`: serialized `BaseUniverseParams`.
- `status.json`: endpoint and status file written by the Universe runtime.
- `stdout.log`: Slurm job stdout.
- `stderr.log`: Slurm job stderr.
- `*.slurm.sh`: generated Slurm batch script.

The generated batch script runs `python -m framework.universes.run_universe` with params-file, status-file, host, port, and CORS arguments.

The backend submits the script with `sbatch --parsable`, stores the returned job id on the `UniverseHandle`, polls `status.json` for endpoint metadata, and falls back to `squeue` job-state checks while waiting.

## Configuration

Common `backend_config` keys:

- `sbatch_bin`: sbatch executable, defaulting to `WOLF_SBATCH_BIN` or `sbatch`.
- `squeue_bin`: squeue executable, defaulting to `WOLF_SQUEUE_BIN` or `squeue`.
- `scancel_bin`: scancel executable, defaulting to `WOLF_SCANCEL_BIN` or `scancel`.
- `python_bin`: Python executable in the Slurm job, defaulting to `WOLF_SLURM_PYTHON_BIN` or `python`.
- `runtime_dir`: directory for params, status, log, and script files.
- `job_name`: explicit Slurm job name.
- `partition`, `account`, `time`, `nodes`, `ntasks`, `cpus_per_task`, `mem`, `gres`, `constraint`, `qos`, `reservation`, `dependency`: common Slurm options emitted as `#SBATCH` lines.
- `extra_sbatch_args`: extra raw `#SBATCH` option lines.
- `module_lines` or `setup_lines`: shell setup lines before launching the Universe.
- `env`: environment variables exported inside the batch script.
- `host` and `port`: Universe bind host and port.
- `connect_host`: host to publish in the returned endpoint when compute-node hostnames are not directly reachable from WOLF.
- `startup_timeout_s` and `poll_interval_s`: endpoint polling controls.

## Notes and limitations

Slurm deployments often run on compute nodes behind login nodes or firewalls. Endpoint reachability depends on the cluster networking model. Use `connect_host`, SSH tunnels, or site-specific routing when the host written by the Universe status file is not directly reachable.

The backend currently uses status-file endpoint discovery, which is best suited for shared filesystems visible to both WOLF and the Slurm job. Future work could add automatic SSH tunnel setup, `sacct` diagnostics, and reattachment by job id.

## Testing

The unit tests mock `sbatch`, `squeue`, and `scancel`. They cover registry discovery, batch-script generation, endpoint discovery through `status.json`, submit failure handling, status, logs, termination, cleanup, and legacy-compatible handle metadata.