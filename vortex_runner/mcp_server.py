#!/usr/bin/env python3
"""MCP Server for The Intern - exposes vortex commands as tools for Claude Code."""

import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .executor import VortexExecutor
from .config import DEFAULT_CONFIG

# Create the MCP server
server = Server("intern")
executor = VortexExecutor()


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="intern_exec",
            description="Execute a shell command on archer-vortex (remote Linux machine). Use for simple commands like npm run build, git status, system checks. Returns stdout, stderr, and exit code.",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute on vortex"
                    }
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="intern_status",
            description="Check connectivity to archer-vortex and the executor service. Use this to verify the remote machine is reachable before running commands.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="intern_run_template",
            description="Run a predefined job template on archer-vortex. Available templates: quality-gates (lint+typecheck+build), system-info (uptime, disk, memory), git-status (git status + recent commits).",
            inputSchema={
                "type": "object",
                "properties": {
                    "template": {
                        "type": "string",
                        "enum": ["quality-gates", "system-info", "git-status"],
                        "description": "The template to run"
                    }
                },
                "required": ["template"]
            }
        ),
        Tool(
            name="intern_read_file",
            description="Read a file from archer-vortex. Use this to read remote files without downloading them.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path or path relative to repo root"
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Maximum lines to read (default: 100)",
                        "default": 100
                    }
                },
                "required": ["path"]
            }
        ),
        Tool(
            name="intern_list_files",
            description="List files in a directory on archer-vortex.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: repo root)",
                        "default": "."
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files",
                        "default": "*"
                    }
                },
                "required": []
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""

    if name == "intern_exec":
        command = arguments.get("command", "")
        if not command:
            return [TextContent(type="text", text="Error: command is required")]

        # Don't require cwd for simple commands (curl, ping, etc.)
        # Only use cwd for npm/git commands that need a project context
        needs_cwd = any(cmd in command.lower() for cmd in ["npm", "git", "node", "npx", "pnpm"])
        cwd = executor.config.default_repo_path if needs_cwd else None

        exit_code, stdout, stderr, duration = executor._ssh_command(
            command,
            cwd=cwd
        )

        result = {
            "exit_code": exit_code,
            "stdout": stdout.strip() if stdout else "",
            "stderr": stderr.strip() if stderr else "",
            "duration_ms": duration,
            "success": exit_code == 0
        }

        # Format output nicely
        output_parts = []
        if result["stdout"]:
            output_parts.append(f"stdout:\n{result['stdout']}")
        if result["stderr"]:
            output_parts.append(f"stderr:\n{result['stderr']}")
        output_parts.append(f"\nExit code: {exit_code} ({duration}ms)")

        return [TextContent(type="text", text="\n".join(output_parts))]

    elif name == "intern_status":
        # Check SSH connectivity
        exit_code, stdout, stderr, duration = executor._ssh_command("echo ok && whoami && node -v")

        if exit_code == 0:
            lines = stdout.strip().split("\n")
            user = lines[1] if len(lines) > 1 else "unknown"
            node = lines[2] if len(lines) > 2 else "unknown"

            # Check executor service
            exec_code, exec_out, _, _ = executor._ssh_command("curl -s http://127.0.0.1:8787/health")
            executor_ok = exec_code == 0 and "ok" in exec_out.lower()

            result = f"""SSH Connection: OK
Host: {executor.config.ssh_host}
User: {user}
Node.js: {node}
Latency: {duration}ms
Executor Service: {"Online" if executor_ok else "Offline"}"""
        else:
            result = f"""SSH Connection: FAILED
Host: {executor.config.ssh_host}
Error: {stderr.strip() if stderr else "Connection failed"}"""

        return [TextContent(type="text", text=result)]

    elif name == "intern_run_template":
        template = arguments.get("template", "")

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

        commands = templates.get(template)
        if not commands:
            return [TextContent(type="text", text=f"Unknown template: {template}. Available: {', '.join(templates.keys())}")]

        results = []
        overall_success = True

        for cmd in commands:
            exit_code, stdout, stderr, duration = executor._ssh_command(
                cmd,
                cwd=executor.config.default_repo_path
            )

            status = "PASS" if exit_code == 0 else "FAIL"
            if exit_code != 0:
                overall_success = False

            results.append(f"[{status}] {cmd} ({duration}ms)")
            if stdout.strip():
                # Truncate long output
                output = stdout.strip()
                if len(output) > 1000:
                    output = output[:500] + "\n...[truncated]...\n" + output[-500:]
                results.append(output)
            if stderr.strip() and exit_code != 0:
                results.append(f"stderr: {stderr.strip()[:500]}")
            results.append("")

        results.append(f"Overall: {'SUCCESS' if overall_success else 'FAILED'}")

        return [TextContent(type="text", text="\n".join(results))]

    elif name == "intern_read_file":
        path = arguments.get("path", "")
        lines = arguments.get("lines", 100)

        if not path:
            return [TextContent(type="text", text="Error: path is required")]

        # Handle relative paths
        if not path.startswith("/"):
            path = f"{executor.config.default_repo_path}/{path}"

        exit_code, stdout, stderr, _ = executor._ssh_command(f"head -n {lines} '{path}'")

        if exit_code == 0:
            return [TextContent(type="text", text=stdout)]
        else:
            return [TextContent(type="text", text=f"Error reading file: {stderr.strip()}")]

    elif name == "intern_list_files":
        path = arguments.get("path", ".")
        pattern = arguments.get("pattern", "*")

        # Handle relative paths
        if not path.startswith("/"):
            path = f"{executor.config.default_repo_path}/{path}"

        exit_code, stdout, stderr, _ = executor._ssh_command(f"ls -la '{path}'/{pattern} 2>/dev/null || ls -la '{path}'")

        if exit_code == 0:
            return [TextContent(type="text", text=stdout)]
        else:
            return [TextContent(type="text", text=f"Error listing files: {stderr.strip()}")]

    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def run_server():
    """Entry point for the MCP server."""
    asyncio.run(main())


if __name__ == "__main__":
    run_server()
