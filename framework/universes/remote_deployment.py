"""Remote deployment manager for universes.

This module provides SSH-based remote deployment capability for WOLF universes.
It handles secure connection, file transfer, remote process management, and status monitoring.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False


class RemoteUniverseHandle:
    """Wrapper for remote universe process information.
    
    Attributes:
        ssh_client: Active SSH client connection
        remote_host: Hostname or IP of remote machine
        remote_user: SSH username
        remote_pid: Process ID on remote machine
        remote_work_dir: Working directory on remote machine
        remote_params_file: Path to params file on remote machine
        remote_status_file: Path to status file on remote machine
        local_stdout_file: Local path to stdout log
        local_stderr_file: Local path to stderr log
        actual_port: Actual port the universe is running on
    """
    
    def __init__(self,
                 ssh_client: 'paramiko.SSHClient',
                 remote_host: str,
                 remote_user: str,
                 remote_pid: int,
                 remote_work_dir: str,
                 remote_params_file: str,
                 remote_status_file: str,
                 local_stdout_file: str,
                 local_stderr_file: str,
                 actual_port: Optional[int] = None):
        self.ssh_client = ssh_client
        self.remote_host = remote_host
        self.remote_user = remote_user
        self.remote_pid = remote_pid
        self.remote_work_dir = remote_work_dir
        self.remote_params_file = remote_params_file
        self.remote_status_file = remote_status_file
        self.local_stdout_file = local_stdout_file
        self.local_stderr_file = local_stderr_file
        self.actual_port = actual_port
        
    def poll(self) -> Optional[int]:
        """Check if remote process is still running.
        
        Returns:
            None if running, exit code if terminated
        """
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(
                f"ps -p {self.remote_pid} -o pid="
            )
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status == 0:
                return None  # Process is running
            else:
                return exit_status  # Process has terminated
        except Exception:
            return -1  # Error checking status
    
    def terminate(self) -> None:
        """Send SIGTERM to remote process."""
        try:
            self.ssh_client.exec_command(f"kill {self.remote_pid}")
        except Exception:
            pass
    
    def kill(self) -> None:
        """Send SIGKILL to remote process."""
        try:
            self.ssh_client.exec_command(f"kill -9 {self.remote_pid}")
        except Exception:
            pass
    
    def close(self) -> None:
        """Close SSH connection."""
        try:
            self.ssh_client.close()
        except Exception:
            pass


class RemoteDeploymentManager:
    """Manages SSH-based remote universe deployments."""
    
    @staticmethod
    def check_paramiko() -> None:
        """Check if paramiko is available."""
        if not PARAMIKO_AVAILABLE:
            raise ImportError(
                "paramiko is required for remote deployment. "
                "Install it with: pip install paramiko"
            )
    
    @staticmethod
    def create_ssh_client(host: str,
                         user: str,
                         key_path: str,
                         port: int = 22) -> 'paramiko.SSHClient':
        """Create and connect SSH client.
        
        Args:
            host: Remote hostname or IP
            user: SSH username
            key_path: Path to SSH private key
            port: SSH port (default 22)
            
        Returns:
            Connected SSH client
        """
        RemoteDeploymentManager.check_paramiko()
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh.connect(
                hostname=host,
                port=port,
                username=user,
                key_filename=key_path,
                timeout=10
            )
            return ssh
        except Exception as e:
            raise ConnectionError(f"Failed to connect to {user}@{host}: {e}")
    
    @staticmethod
    def setup_remote_work_dir(ssh_client: 'paramiko.SSHClient',
                             base_dir: str) -> str:
        """Create remote working directory.
        
        Args:
            ssh_client: Connected SSH client
            base_dir: Base directory path on remote machine
            
        Returns:
            Path to created work directory
        """
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        work_dir = f"{base_dir}/wolf_universe_{timestamp}"
        
        stdin, stdout, stderr = ssh_client.exec_command(f"mkdir -p {work_dir}")
        exit_status = stdout.channel.recv_exit_status()
        
        if exit_status != 0:
            raise RuntimeError(f"Failed to create remote work directory: {stderr.read().decode()}")
        
        return work_dir
    
    @staticmethod
    def transfer_file(ssh_client: 'paramiko.SSHClient',
                     local_path: str,
                     remote_path: str) -> None:
        """Transfer file to remote machine via SFTP.
        
        Args:
            ssh_client: Connected SSH client
            local_path: Local file path
            remote_path: Remote destination path
        """
        try:
            sftp = ssh_client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
        except Exception as e:
            raise RuntimeError(f"Failed to transfer {local_path} to {remote_path}: {e}")
    
    @staticmethod
    def fetch_file(ssh_client: 'paramiko.SSHClient',
                  remote_path: str,
                  local_path: str) -> None:
        """Fetch file from remote machine via SFTP.
        
        Args:
            ssh_client: Connected SSH client
            remote_path: Remote file path
            local_path: Local destination path
        """
        try:
            sftp = ssh_client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
        except Exception as e:
            raise RuntimeError(f"Failed to fetch {remote_path} to {local_path}: {e}")
    
    @staticmethod
    def launch_remote_universe(ssh_client: 'paramiko.SSHClient',
                              remote_work_dir: str,
                              remote_params_file: str,
                              remote_status_file: str,
                              remote_python_path: str,
                              cors: Optional[str] = None) -> int:
        """Launch universe process on remote machine.
        
        Args:
            ssh_client: Connected SSH client
            remote_work_dir: Remote working directory
            remote_params_file: Path to params file on remote
            remote_status_file: Path to status file on remote
            remote_python_path: Path to Python interpreter on remote
            cors: CORS origins (optional)
            
        Returns:
            Remote process PID
        """
        # Build command
        cmd_parts = [
            remote_python_path,
            "-m",
            "framework.universes.run_universe",
            "--params-file", remote_params_file,
            "--status-file", remote_status_file,
        ]
        
        if cors:
            cmd_parts.extend(["--cors", cors])
        
        # Launch in background with nohup
        cmd = " ".join(cmd_parts)
        launch_cmd = f"cd {remote_work_dir} && nohup {cmd} > stdout.log 2> stderr.log & echo $!"
        
        stdin, stdout, stderr = ssh_client.exec_command(launch_cmd)
        exit_status = stdout.channel.recv_exit_status()
        
        if exit_status != 0:
            raise RuntimeError(f"Failed to launch remote universe: {stderr.read().decode()}")
        
        pid_str = stdout.read().decode().strip()
        try:
            return int(pid_str)
        except ValueError:
            raise RuntimeError(f"Invalid PID returned from remote launch: {pid_str}")
    
    @staticmethod
    def check_remote_status(ssh_client: 'paramiko.SSHClient',
                           remote_status_file: str,
                           timeout: float = 10.0) -> Dict[str, Any]:
        """Poll remote status file until universe is ready.
        
        Args:
            ssh_client: Connected SSH client
            remote_status_file: Path to status file on remote
            timeout: Maximum time to wait in seconds
            
        Returns:
            Status dictionary from remote universe
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                stdin, stdout, stderr = ssh_client.exec_command(
                    f"cat {remote_status_file}"
                )
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status == 0:
                    content = stdout.read().decode()
                    status = json.loads(content)
                    
                    if status.get("status") == "running":
                        return status
            except (json.JSONDecodeError, KeyError):
                pass
            
            time.sleep(0.5)
        
        raise TimeoutError(f"Remote universe did not start within {timeout} seconds")
    
    @staticmethod
    def deploy_universe_remote(params: 'BaseUniverseParams',
                              ssh_config: Dict[str, str],
                              cors: Optional[str] = None) -> RemoteUniverseHandle:
        """Deploy a universe on a remote machine.
        
        Args:
            params: Universe parameters
            ssh_config: SSH configuration dict with keys:
                - user: SSH username
                - key_path: Path to SSH private key
                - remote_python_path: Path to Python on remote
                - remote_work_dir: Base work directory on remote
            cors: CORS origins (optional)
            
        Returns:
            RemoteUniverseHandle for the deployed universe
        """
        RemoteDeploymentManager.check_paramiko()
        
        # Extract SSH config
        host = params.info.host
        user = ssh_config.get("user")
        key_path = ssh_config.get("key_path")
        remote_python = ssh_config.get("remote_python_path", "python3")
        remote_base_dir = ssh_config.get("remote_work_dir", "/tmp")
        
        if not user or not key_path:
            raise ValueError("ssh_config must contain 'user' and 'key_path'")
        
        # Create SSH connection
        ssh_client = RemoteDeploymentManager.create_ssh_client(host, user, key_path)
        
        try:
            # Setup remote work directory
            remote_work_dir = RemoteDeploymentManager.setup_remote_work_dir(
                ssh_client, remote_base_dir
            )
            
            # Prepare params file locally
            local_temp_dir = Path(tempfile.gettempdir()) / "wolf_remote_deploy"
            local_temp_dir.mkdir(parents=True, exist_ok=True)
            
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            local_params_file = local_temp_dir / f"params_{timestamp}.json"
            
            serializable = params.model_dump(mode="json") if hasattr(params, "model_dump") else params.dict()
            with local_params_file.open("w", encoding="utf-8") as f:
                json.dump(serializable, f, indent=2)
            
            # Transfer params file
            remote_params_file = f"{remote_work_dir}/params.json"
            RemoteDeploymentManager.transfer_file(
                ssh_client, str(local_params_file), remote_params_file
            )
            
            # Define remote status file
            remote_status_file = f"{remote_work_dir}/status.json"
            
            # Launch remote universe
            remote_pid = RemoteDeploymentManager.launch_remote_universe(
                ssh_client,
                remote_work_dir,
                remote_params_file,
                remote_status_file,
                remote_python,
                cors
            )
            
            # Wait for universe to start and get actual port
            time.sleep(2.0)
            status = RemoteDeploymentManager.check_remote_status(
                ssh_client, remote_status_file, timeout=15.0
            )
            
            actual_port = status.get("port")
            
            # Setup local log files
            local_stdout = local_temp_dir / f"stdout_{timestamp}.log"
            local_stderr = local_temp_dir / f"stderr_{timestamp}.log"
            
            # Create handle
            handle = RemoteUniverseHandle(
                ssh_client=ssh_client,
                remote_host=host,
                remote_user=user,
                remote_pid=remote_pid,
                remote_work_dir=remote_work_dir,
                remote_params_file=remote_params_file,
                remote_status_file=remote_status_file,
                local_stdout_file=str(local_stdout),
                local_stderr_file=str(local_stderr),
                actual_port=actual_port
            )
            
            return handle
            
        except Exception as e:
            ssh_client.close()
            raise RuntimeError(f"Remote deployment failed: {e}")
    
    @staticmethod
    def terminate_remote_universe(handle: RemoteUniverseHandle,
                                 force: bool = False,
                                 timeout: float = 10.0) -> None:
        """Terminate a remote universe.
        
        Args:
            handle: Remote universe handle
            force: If True, use SIGKILL instead of SIGTERM
            timeout: Time to wait for graceful termination
        """
        try:
            if force:
                handle.kill()
            else:
                handle.terminate()
                
                # Wait for termination
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if handle.poll() is not None:
                        break
                    time.sleep(0.5)
                else:
                    # Timeout, force kill
                    handle.kill()
        finally:
            handle.close()
    
    @staticmethod
    def fetch_remote_logs(handle: RemoteUniverseHandle) -> Tuple[str, str]:
        """Fetch stdout and stderr logs from remote machine.
        
        Args:
            handle: Remote universe handle
            
        Returns:
            Tuple of (stdout_content, stderr_content)
        """
        remote_stdout = f"{handle.remote_work_dir}/stdout.log"
        remote_stderr = f"{handle.remote_work_dir}/stderr.log"
        
        try:
            RemoteDeploymentManager.fetch_file(
                handle.ssh_client, remote_stdout, handle.local_stdout_file
            )
            RemoteDeploymentManager.fetch_file(
                handle.ssh_client, remote_stderr, handle.local_stderr_file
            )
            
            with open(handle.local_stdout_file, "r") as f:
                stdout_content = f.read()
            
            with open(handle.local_stderr_file, "r") as f:
                stderr_content = f.read()
            
            return stdout_content, stderr_content
        except Exception as e:
            return "", f"Failed to fetch logs: {e}"
