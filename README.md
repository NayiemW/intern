# The Intern

A CLI tool for running tasks on remote machines via SSH. Features an interactive chat mode with optional LLM support via Ollama.

## Features

- **Interactive Chat Mode**: Claude-like REPL interface for running commands
- **Job Templates**: Run predefined job sequences from YAML files
- **LLM Integration**: Natural language support via Ollama (optional)
- **Secret Redaction**: Automatically redacts sensitive output (API keys, tokens, etc.)
- **Run Reports**: Generates markdown reports for job runs

## Installation

```bash
# Clone the repository
git clone https://github.com/NayiemW/intern.git
cd intern

# Install in development mode
pip install -e .
```

## Prerequisites

1. **SSH Access**: Configure SSH access to your remote machine in `~/.ssh/config`:

```
Host vortex
    HostName your-server-ip
    User your-username
    IdentityFile ~/.ssh/your-key
```

2. **Environment Variables** (optional):

```bash
export VORTEX_SSH_HOST="vortex"          # SSH host from ~/.ssh/config
export VORTEX_SSH_USER="intern"          # SSH username
export VORTEX_REPO_PATH="/path/to/repo"  # Default working directory
export VORTEX_RUNS_DIR="./runs"          # Local directory for logs
```

## Usage

### Interactive Mode

Start the interactive chat:

```bash
intern
```

Or explicitly:

```bash
intern chat
```

This opens an interactive prompt where you can:

- Run commands: `exec npm run build`
- Run templates: `run quality-gates`
- Quick commands: `build`, `lint`, `typecheck`
- Chat naturally (if Ollama is available on remote)

### Run a Job Template

```bash
intern run examples/quality.yaml
```

### Execute a Single Command

```bash
intern exec "npm run build"
intern exec "git status"
```

### Test Connection

```bash
intern test
```

## Job Templates

Create YAML files to define job sequences:

```yaml
name: quality-gates
description: Run linting, type checking, and build
commands:
  - npm run lint
  - npm run typecheck
  - npm run build
```

With optional failure handling:

```yaml
name: deploy
commands:
  - run: npm ci
  - run: npm run build
  - run: npm test
    allow_fail: true
  - run: npm run deploy
```

## Commands in Interactive Mode

| Command | Description |
|---------|-------------|
| `help` | Show available commands |
| `status` | Check executor service status |
| `run <template>` | Run a job template |
| `exec <command>` | Execute a shell command |
| `build` | Run `npm run build` |
| `lint` | Run `npm run lint` |
| `typecheck` | Run `npm run typecheck` |
| `logs` | Show recent job run logs |
| `clear` | Clear screen |
| `exit` | Exit the chat |

## LLM Support

If Ollama is running on the remote machine, the intern can understand natural language:

```
intern> Can you run the tests for me?
Thinking...

Sure! I'll run the tests for you. Use `exec npm test` or just type `test`.
```

## Security

- All output is scanned for sensitive patterns (API keys, tokens, passwords)
- Matched patterns are replaced with `[REDACTED]`
- SSH connections use `BatchMode=yes` for non-interactive operation

## Requirements

- Python 3.10+
- PyYAML
- SSH access to remote machine
- rsync (for artifact fetching)

## License

MIT
