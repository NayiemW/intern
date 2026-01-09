"""SSH executor for running jobs on vortex."""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_CONFIG, VortexConfig


@dataclass
class CommandResult:
    """Result of a single command execution."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    success: bool


@dataclass
class JobResult:
    """Result of a complete job execution."""

    job_name: str
    start_time: datetime
    end_time: datetime
    commands: list[CommandResult]
    artifacts: dict[str, str]
    success: bool
    error: Optional[str] = None


class VortexExecutor:
    """Executes jobs on vortex via SSH."""

    def __init__(self, config: Optional[VortexConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self._redact_patterns = [
            re.compile(p) for p in self.config.redact_patterns
        ]

    def _redact(self, text: str) -> str:
        """Redact sensitive information from output."""
        result = text
        for pattern in self._redact_patterns:
            result = pattern.sub("[REDACTED]", result)
        return result

    def _ssh_command(self, cmd: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
        """Execute a command via SSH and return (exit_code, stdout, stderr)."""
        if cwd:
            full_cmd = f"cd {cwd} && {cmd}"
        else:
            full_cmd = cmd

        ssh_cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            self.config.ssh_host,
            full_cmd
        ]

        start = datetime.now()
        try:
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout per command
            )
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return result.returncode, result.stdout, result.stderr, duration
        except subprocess.TimeoutExpired:
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return 124, "", "Command timed out after 600 seconds", duration
        except Exception as e:
            duration = int((datetime.now() - start).total_seconds() * 1000)
            return 1, "", str(e), duration

    def _fetch_artifact(self, remote_path: str, local_dir: Path) -> Optional[str]:
        """Fetch an artifact from vortex via rsync."""
        local_path = local_dir / Path(remote_path).name

        rsync_cmd = [
            "rsync", "-az",
            f"{self.config.ssh_host}:{remote_path}",
            str(local_path)
        ]

        try:
            result = subprocess.run(rsync_cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0 and local_path.exists():
                return str(local_path)
        except Exception:
            pass
        return None

    def run_job(self, job: dict) -> JobResult:
        """Execute a job specification and return results."""
        job_name = job.get("name", "unnamed-job")
        repo_path = job.get("repo_path", self.config.default_repo_path)
        commands = job.get("commands", [])
        artifacts_spec = job.get("artifacts", [])
        env = job.get("env", {})

        start_time = datetime.now()
        command_results = []
        artifacts = {}
        job_success = True
        job_error = None

        # Build env prefix if needed
        env_prefix = ""
        if env:
            env_parts = [f"export {k}={v}" for k, v in env.items()]
            env_prefix = " && ".join(env_parts) + " && "

        # Execute commands
        for cmd_spec in commands:
            if isinstance(cmd_spec, str):
                cmd = cmd_spec
                allow_fail = False
            else:
                cmd = cmd_spec.get("run", cmd_spec.get("cmd", ""))
                allow_fail = cmd_spec.get("allow_fail", False)

            full_cmd = f"{env_prefix}{cmd}" if env_prefix else cmd
            exit_code, stdout, stderr, duration = self._ssh_command(full_cmd, cwd=repo_path)

            success = exit_code == 0 or allow_fail
            command_results.append(CommandResult(
                command=cmd,
                exit_code=exit_code,
                stdout=self._redact(stdout),
                stderr=self._redact(stderr),
                duration_ms=duration,
                success=success
            ))

            if not success:
                job_success = False
                job_error = f"Command failed: {cmd} (exit code {exit_code})"
                break

        # Fetch artifacts if job succeeded or we have partial results
        if artifacts_spec:
            timestamp = start_time.strftime("%Y%m%d_%H%M%S")
            run_dir = Path(self.config.runs_dir) / timestamp
            run_dir.mkdir(parents=True, exist_ok=True)

            for artifact in artifacts_spec:
                if artifact.startswith("/"):
                    remote_path = artifact
                else:
                    remote_path = f"{repo_path}/{artifact}"

                local_path = self._fetch_artifact(remote_path, run_dir)
                if local_path:
                    artifacts[artifact] = local_path

        end_time = datetime.now()

        return JobResult(
            job_name=job_name,
            start_time=start_time,
            end_time=end_time,
            commands=command_results,
            artifacts=artifacts,
            success=job_success,
            error=job_error
        )


def generate_report(result: JobResult, output_path: Path) -> str:
    """Generate a markdown report for a job run."""
    lines = [
        f"# Vortex Run Report: {result.job_name}",
        "",
        f"**Start:** {result.start_time.isoformat()}",
        f"**End:** {result.end_time.isoformat()}",
        f"**Duration:** {int((result.end_time - result.start_time).total_seconds())}s",
        f"**Status:** {'SUCCESS' if result.success else 'FAILED'}",
        "",
    ]

    if result.error:
        lines.extend([
            "## Error",
            "",
            f"```",
            result.error,
            "```",
            "",
        ])

    lines.extend([
        "## Commands",
        "",
    ])

    for i, cmd in enumerate(result.commands, 1):
        status = "PASS" if cmd.success else "FAIL"
        lines.extend([
            f"### {i}. `{cmd.command}`",
            "",
            f"- **Status:** {status}",
            f"- **Exit Code:** {cmd.exit_code}",
            f"- **Duration:** {cmd.duration_ms}ms",
            "",
        ])

        if cmd.stdout.strip():
            # Truncate long output
            stdout = cmd.stdout.strip()
            if len(stdout) > 5000:
                stdout = stdout[:2500] + "\n\n... [truncated] ...\n\n" + stdout[-2500:]
            lines.extend([
                "**stdout:**",
                "```",
                stdout,
                "```",
                "",
            ])

        if cmd.stderr.strip():
            stderr = cmd.stderr.strip()
            if len(stderr) > 2000:
                stderr = stderr[:1000] + "\n\n... [truncated] ...\n\n" + stderr[-1000:]
            lines.extend([
                "**stderr:**",
                "```",
                stderr,
                "```",
                "",
            ])

    if result.artifacts:
        lines.extend([
            "## Artifacts",
            "",
        ])
        for name, path in result.artifacts.items():
            lines.append(f"- `{name}` -> `{path}`")
        lines.append("")

    lines.extend([
        "---",
        f"*Generated by vortex-runner at {datetime.now().isoformat()}*",
    ])

    report_content = "\n".join(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_content)

    return report_content
