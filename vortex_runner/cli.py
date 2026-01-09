#!/usr/bin/env python3
"""CLI for vortex-runner."""

import argparse
import readline
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from . import __version__
from .config import DEFAULT_CONFIG
from .executor import VortexExecutor, generate_report


# ANSI color codes
class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    """Apply color to text."""
    return f"{color}{text}{Colors.RESET}"


def load_job(job_path: str) -> dict:
    """Load a job YAML file."""
    path = Path(job_path)
    if not path.exists():
        raise FileNotFoundError(f"Job file not found: {job_path}")

    with open(path) as f:
        return yaml.safe_load(f)


def cmd_run(args: argparse.Namespace) -> int:
    """Execute a job from a YAML file."""
    try:
        job = load_job(args.job)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except yaml.YAMLError as e:
        print(f"Error parsing YAML: {e}", file=sys.stderr)
        return 1

    # Override repo_path if provided
    if args.repo_path:
        job["repo_path"] = args.repo_path

    executor = VortexExecutor()
    print(f"Running job: {job.get('name', 'unnamed')}")
    print(f"Target: {executor.config.ssh_host}")
    print(f"Repo: {job.get('repo_path', executor.config.default_repo_path)}")
    print("-" * 60)

    result = executor.run_job(job)

    # Generate report
    timestamp = result.start_time.strftime("%Y%m%d_%H%M%S")
    run_dir = Path(executor.config.runs_dir) / timestamp
    report_path = run_dir / "report.md"
    generate_report(result, report_path)

    # Print summary
    print("-" * 60)
    for cmd in result.commands:
        status = "PASS" if cmd.success else "FAIL"
        print(f"[{status}] {cmd.command} ({cmd.duration_ms}ms)")

    print("-" * 60)
    if result.success:
        print(f"SUCCESS - All commands passed")
    else:
        print(f"FAILED - {result.error}")

    print(f"Report: {report_path}")

    return 0 if result.success else 1


def cmd_exec(args: argparse.Namespace) -> int:
    """Execute a single command on vortex."""
    executor = VortexExecutor()
    repo_path = args.repo_path or executor.config.default_repo_path

    print(f"Executing: {args.command}")
    print(f"Target: {executor.config.ssh_host}")
    print(f"Directory: {repo_path}")
    print("-" * 60)

    exit_code, stdout, stderr, duration = executor._ssh_command(args.command, cwd=repo_path)

    if stdout:
        print(executor._redact(stdout))
    if stderr:
        print(executor._redact(stderr), file=sys.stderr)

    print("-" * 60)
    print(f"Exit code: {exit_code} ({duration}ms)")

    return exit_code


def cmd_test(args: argparse.Namespace) -> int:
    """Test connectivity to vortex."""
    executor = VortexExecutor()
    print(f"Testing connection to {executor.config.ssh_host}...")

    exit_code, stdout, stderr, duration = executor._ssh_command("whoami && node -v && npm -v")

    if exit_code == 0:
        lines = stdout.strip().split("\n")
        print(f"  User: {lines[0] if lines else 'unknown'}")
        print(f"  Node: {lines[1] if len(lines) > 1 else 'unknown'}")
        print(f"  npm:  {lines[2] if len(lines) > 2 else 'unknown'}")
        print(f"  Latency: {duration}ms")
        print("Connection OK")
        return 0
    else:
        print(f"Connection FAILED: {stderr}", file=sys.stderr)
        return 1


class InternChat:
    """Interactive chat interface for The Intern."""

    COMMANDS = {
        "help": "Show available commands",
        "status": "Check executor service status",
        "run <template>": "Run a job template (quality-gates, system-info, git-status)",
        "exec <command>": "Execute an allowlisted command",
        "build": "Run npm build",
        "lint": "Run npm lint",
        "typecheck": "Run npm typecheck",
        "logs": "Show recent job run logs",
        "clear": "Clear screen",
        "exit": "Exit the chat",
    }

    QUICK_COMMANDS = {
        "build": "npm run build",
        "lint": "npm run lint",
        "typecheck": "npm run typecheck",
        "test": "npm test",
        "dev": "npm run dev",
        "format": "npm run format",
    }

    SYSTEM_PROMPT = """You are The Intern, a helpful AI assistant for the archer-vortex system.
You help users run tasks, execute commands, and manage the system.

You have access to these commands:
- run <template>: Run job templates (quality-gates, system-info, git-status)
- exec <command>: Execute shell commands
- build, lint, typecheck: Quick npm commands
- status: Check system status
- logs: View recent job logs

When users ask you to do something, suggest the appropriate command.
Keep responses brief and helpful. You're running on a Linux Mint machine with an AMD GPU."""

    def __init__(self):
        self.executor = VortexExecutor()
        self.history = []
        self.chat_history = []
        self.ollama_available = False
        self.ollama_model = "llama3.2"

    def check_ollama(self) -> bool:
        """Check if Ollama is available."""
        import json
        exit_code, stdout, _, _ = self.executor._ssh_command(
            "curl -s http://localhost:11434/api/tags"
        )
        if exit_code == 0:
            try:
                data = json.loads(stdout)
                models = data.get("models", [])
                if models:
                    self.ollama_model = models[0].get("name", "llama3.2")
                    return True
            except:
                pass
        return False

    def chat_with_ollama(self, message: str) -> str:
        """Send a message to Ollama and get a response."""
        import json

        # Add user message to history
        self.chat_history.append({"role": "user", "content": message})

        # Build messages with system prompt
        messages = [{"role": "system", "content": self.SYSTEM_PROMPT}] + self.chat_history[-10:]  # Keep last 10 messages

        payload = json.dumps({
            "model": self.ollama_model,
            "messages": messages,
            "stream": False
        })

        # Escape for shell
        payload_escaped = payload.replace("'", "'\\''")

        exit_code, stdout, stderr, _ = self.executor._ssh_command(
            f"curl -s http://localhost:11434/api/chat -d '{payload_escaped}'"
        )

        if exit_code == 0:
            try:
                data = json.loads(stdout)
                response = data.get("message", {}).get("content", "")
                if response:
                    self.chat_history.append({"role": "assistant", "content": response})
                    return response
            except:
                pass

        return "Sorry, I couldn't process that. Try using 'help' for available commands."

    def print_welcome(self):
        """Print welcome message."""
        print()
        print(colored("╭────────────────────────────────────────╮", Colors.BLUE))
        print(colored("│", Colors.BLUE) + colored("       The Intern ", Colors.BOLD) + colored("@ archer-vortex", Colors.CYAN) + colored("      │", Colors.BLUE))
        print(colored("╰────────────────────────────────────────╯", Colors.BLUE))
        print()
        print(colored("Your local AI assistant for running tasks on archer-vortex.", Colors.DIM))
        print(colored(f"Connected to: {self.executor.config.ssh_host}", Colors.DIM))
        print()
        print("Type " + colored("help", Colors.YELLOW) + " for available commands, or " + colored("exit", Colors.YELLOW) + " to quit.")
        print()

    def print_help(self):
        """Print help message."""
        print()
        print(colored("Available Commands:", Colors.BOLD))
        print()
        for cmd, desc in self.COMMANDS.items():
            print(f"  {colored(cmd, Colors.CYAN):<30} {desc}")
        print()
        print(colored("Quick Commands:", Colors.BOLD))
        print()
        for cmd, full in self.QUICK_COMMANDS.items():
            print(f"  {colored(cmd, Colors.GREEN):<30} → {full}")
        print()

    def check_status(self):
        """Check executor service status."""
        print(colored("Checking executor status...", Colors.DIM))
        exit_code, stdout, stderr, duration = self.executor._ssh_command(
            "curl -s http://127.0.0.1:8787/health"
        )
        if exit_code == 0 and "ok" in stdout.lower():
            print(colored("✓ Executor service is online", Colors.GREEN))
            print(colored(f"  Response: {stdout.strip()}", Colors.DIM))
        else:
            print(colored("✗ Executor service is offline", Colors.RED))

    def run_template(self, template_name: str):
        """Run a job template."""
        print(colored(f"Running template: {template_name}", Colors.CYAN))
        print()

        # Map template names to YAML files or predefined commands
        templates = {
            "quality-gates": [
                "npm run lint",
                "npm run typecheck",
                "npm run build",
            ],
            "system-info": [
                "uptime",
                "df -h /",
                "free -m",
                "cat /proc/loadavg",
            ],
            "git-status": [
                "git status",
                "git log --oneline -5",
            ],
        }

        commands = templates.get(template_name)
        if not commands:
            print(colored(f"Unknown template: {template_name}", Colors.RED))
            print(f"Available: {', '.join(templates.keys())}")
            return

        for cmd in commands:
            self._run_command(cmd)
            print()

    def run_exec(self, command: str):
        """Execute a command."""
        self._run_command(command)

    def _run_command(self, command: str):
        """Run a command and display output."""
        print(colored(f"> {command}", Colors.YELLOW))
        exit_code, stdout, stderr, duration = self.executor._ssh_command(
            command,
            cwd=self.executor.config.default_repo_path
        )

        if stdout.strip():
            print(stdout.rstrip())
        if stderr.strip():
            print(colored(stderr.rstrip(), Colors.RED))

        status_color = Colors.GREEN if exit_code == 0 else Colors.RED
        status_text = "✓" if exit_code == 0 else "✗"
        print(colored(f"{status_text} Exit: {exit_code} ({duration}ms)", status_color))

    def show_logs(self):
        """Show recent job logs."""
        print(colored("Recent job runs:", Colors.CYAN))
        exit_code, stdout, stderr, _ = self.executor._ssh_command(
            "ls -lt /opt/vortex/workspaces/template-intern/intern-memory/runs/*.log 2>/dev/null | head -10"
        )
        if exit_code == 0 and stdout.strip():
            for line in stdout.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 9:
                    filename = parts[-1].split("/")[-1]
                    date_str = " ".join(parts[5:8])
                    print(f"  {colored(filename, Colors.GREEN)} - {date_str}")
        else:
            print(colored("  No logs found", Colors.DIM))

    def process_input(self, user_input: str) -> bool:
        """Process user input. Returns False if should exit."""
        stripped = user_input.strip()
        if not stripped:
            return True

        self.history.append(stripped)
        lower = stripped.lower()

        if lower in ("exit", "quit", "q"):
            print(colored("Goodbye!", Colors.CYAN))
            return False

        if lower == "help":
            self.print_help()
            return True

        if lower == "status":
            self.check_status()
            return True

        if lower == "clear":
            print("\033[2J\033[H", end="")
            self.print_welcome()
            return True

        if lower == "logs":
            self.show_logs()
            return True

        if lower.startswith("run "):
            template = stripped[4:].strip()
            self.run_template(template)
            return True

        if lower.startswith("exec "):
            command = stripped[5:].strip()
            self.run_exec(command)
            return True

        # Quick commands
        if lower in self.QUICK_COMMANDS:
            self._run_command(self.QUICK_COMMANDS[lower])
            return True

        # Natural language - use Ollama if available
        if self.ollama_available:
            print(colored("Thinking...", Colors.DIM))
            response = self.chat_with_ollama(stripped)
            print()
            print(response)
            print()
        else:
            print(colored(f"Unknown command: {stripped}", Colors.YELLOW))
            print(colored("Tip: Use 'exec <command>' to run shell commands, or 'help' for options.", Colors.DIM))
        return True

    def run(self):
        """Run the interactive chat loop."""
        self.print_welcome()

        # Check connection
        print(colored("Checking connection...", Colors.DIM))
        exit_code, _, _, _ = self.executor._ssh_command("echo ok")
        if exit_code != 0:
            print(colored("✗ Cannot connect to archer-vortex", Colors.RED))
            print(colored("  Make sure SSH is configured correctly.", Colors.DIM))
            return 1

        print(colored("✓ Connected", Colors.GREEN))

        # Check Ollama
        print(colored("Checking Ollama...", Colors.DIM))
        self.ollama_available = self.check_ollama()
        if self.ollama_available:
            print(colored(f"✓ Ollama ready ({self.ollama_model})", Colors.GREEN))
        else:
            print(colored("✗ Ollama not available (command-only mode)", Colors.YELLOW))
        print()

        try:
            while True:
                try:
                    prompt = colored("intern> ", Colors.BLUE)
                    user_input = input(prompt)
                    if not self.process_input(user_input):
                        break
                except EOFError:
                    print()
                    break
        except KeyboardInterrupt:
            print()
            print(colored("Interrupted", Colors.YELLOW))

        return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """Start interactive chat mode."""
    chat = InternChat()
    return chat.run()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="vortex-run",
        description="SSH-based executor for archer-vortex"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a job from YAML file")
    run_parser.add_argument("job", help="Path to job YAML file")
    run_parser.add_argument("--repo-path", "-r", help="Override repo path")

    # exec command
    exec_parser = subparsers.add_parser("exec", help="Execute a single command")
    exec_parser.add_argument("command", help="Command to execute")
    exec_parser.add_argument("--repo-path", "-r", help="Working directory on vortex")

    # test command
    test_parser = subparsers.add_parser("test", help="Test connectivity")

    # chat command (interactive mode)
    chat_parser = subparsers.add_parser("chat", help="Interactive chat mode (like 'claude')")

    args = parser.parse_args()

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "exec":
        return cmd_exec(args)
    elif args.command == "test":
        return cmd_test(args)
    elif args.command == "chat":
        return cmd_chat(args)
    else:
        # No command given - default to chat mode
        return cmd_chat(argparse.Namespace())


if __name__ == "__main__":
    sys.exit(main())
