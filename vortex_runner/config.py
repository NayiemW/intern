"""Configuration defaults for vortex-runner."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VortexConfig:
    """Configuration for vortex executor.

    All settings can be overridden via environment variables:
    - VORTEX_SSH_HOST: SSH host alias (from ~/.ssh/config)
    - VORTEX_SSH_USER: SSH username
    - VORTEX_REPO_PATH: Default working directory on remote
    - VORTEX_RUNS_DIR: Local directory for run logs
    """

    ssh_host: str = field(default_factory=lambda: os.environ.get(
        "VORTEX_SSH_HOST", "vortex"
    ))
    ssh_user: str = field(default_factory=lambda: os.environ.get(
        "VORTEX_SSH_USER", "intern"
    ))
    default_repo_path: str = field(default_factory=lambda: os.environ.get(
        "VORTEX_REPO_PATH", "/opt/vortex/workspaces/default"
    ))
    runs_dir: str = field(default_factory=lambda: os.environ.get(
        "VORTEX_RUNS_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runs")
    ))

    # Patterns to redact from output (secrets, tokens, keys)
    redact_patterns: list = field(default_factory=lambda: [
        r"(?i)(api[_-]?key|apikey|secret|token|password|passwd|pwd|auth)[=:\s]+['\"]?[\w\-\.]+",
        r"(?i)bearer\s+[\w\-\.]+",
        r"sk-[a-zA-Z0-9]{20,}",
        r"ghp_[a-zA-Z0-9]{36}",
        r"npm_[a-zA-Z0-9]{36}",
    ])


DEFAULT_CONFIG = VortexConfig()
