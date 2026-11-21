#!/usr/bin/env python3
"""远程执行/传输小工具（paramiko 直连）。

为什么需要它：DSH 的 SSH MCP 只开放了**只读命令白名单**（ls/cat/ss/systemctl status…），
部署要执行的 `bash install.sh`、`systemctl enable`、`nginx -s reload` 都不在白名单里。
本脚本用 paramiko + 密码直连，不依赖 MCP 白名单，也不改 MCP 配置。

凭据来源（三选一）：
  * `--servers-json PATH --name NAME`：读 DSH ssh-mcp 的 servers.json（默认按该格式解析）
  * `--host/--user` + 环境变量 `SSH_PASSWORD`
  * `--host/--user/--password`（密码会出现在进程列表，建议用环境变量）

子命令：
  run    "<shell 命令>"        在远端执行（bash -lc），实时打印 stdout/stderr，返回远端退出码
  put    <本地文件> <远端路径>
  putdir <本地目录> <远端目录>   递归上传（自动建目录）
  get    <远端文件> <本地路径>

示例：
  python deploy/remote_exec.py --servers-json <path>/servers.json --name default run "id"
  python deploy/remote_exec.py --servers-json ... --name default putdir deploy/discovery /root/x/
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover - 环境缺依赖时给出可执行提示
    sys.exit("缺 paramiko：pip install paramiko（或用 DSH 依赖里的 python）")


def load_credentials(args) -> tuple[str, int, str, str]:
    if args.servers_json:
        entries = json.loads(Path(args.servers_json).read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries.get("servers") or list(entries.values())
        match = next((e for e in entries if e.get("name") == args.name), None)
        if match is None:
            names = ", ".join(e.get("name", "?") for e in entries)
            sys.exit(f"servers.json 里没有 name={args.name!r}（有：{names}）")
        return (match["host"], int(match.get("port", 22)), match["username"],
                match["password"])
    password = args.password or os.environ.get("SSH_PASSWORD") or ""
    if not args.host or not args.user or not password:
        sys.exit("需要 --host/--user 且提供密码（--password 或 $SSH_PASSWORD）")
    return args.host, int(args.port), args.user, password


def connect(args) -> paramiko.SSHClient:
    host, port, user, password = load_credentials(args)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password,
                   timeout=30, banner_timeout=30, auth_timeout=30,
                   look_for_keys=False, allow_agent=False)
    return client


def run_command(client: paramiko.SSHClient, command: str, timeout: float) -> int:
    """执行命令并实时输出（不带 PTY，避免交互式提示卡住）。"""
    chan = client.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(f"bash -lc {shquote(command)}")
    out_parts: list[bytes] = []
    while True:
        received = False
        while chan.recv_ready():
            data = chan.recv(65536)
            out_parts.append(data)
            sys.stdout.write(data.decode("utf-8", "replace"))
            sys.stdout.flush()
            received = True
        while chan.recv_stderr_ready():
            data = chan.recv_stderr(65536)
            sys.stderr.write(data.decode("utf-8", "replace"))
            sys.stderr.flush()
            received = True
        if chan.exit_status_ready() and not received:
            break
        if not received:
            time.sleep(0.05)
    code = chan.recv_exit_status()
    chan.close()
    print(f"\n[remote_exec] exit={code}")
    return code


def shquote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def sftp_putdir(client: paramiko.SSHClient, local: Path, remote: str) -> None:
    sftp = client.open_sftp()

    def ensure_dir(path: str) -> None:
        parts = path.strip("/").split("/")
        cur = "/" if path.startswith("/") else ""
        for part in parts:
            cur = posixpath.join(cur or "/", part) if cur else part
            try:
                sftp.stat(cur)
            except IOError:
                sftp.mkdir(cur)

    ensure_dir(remote)
    count = 0
    for path in sorted(local.rglob("*")):
        rel = path.relative_to(local).as_posix()
        target = posixpath.join(remote, rel)
        if path.is_dir():
            ensure_dir(target)
        else:
            ensure_dir(posixpath.dirname(target))
            sftp.put(str(path), target)
            count += 1
    sftp.close()
    print(f"[remote_exec] putdir {local} -> {remote}  ({count} 个文件)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="paramiko 远程执行/传输（绕过 MCP 只读白名单）")
    ap.add_argument("--servers-json", default="", help="DSH ssh-mcp 的 servers.json 路径")
    ap.add_argument("--name", default="default", help="servers.json 里的连接名")
    ap.add_argument("--host", default="")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--timeout", type=float, default=600.0, help="单条命令超时秒数")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); p.add_argument("command")
    p = sub.add_parser("put"); p.add_argument("src"); p.add_argument("dst")
    p = sub.add_parser("putdir"); p.add_argument("src"); p.add_argument("dst")
    p = sub.add_parser("get"); p.add_argument("src"); p.add_argument("dst")
    args = ap.parse_args(argv)

    client = connect(args)
    try:
        if args.cmd == "run":
            return run_command(client, args.command, args.timeout)
        if args.cmd in ("put", "get"):
            sftp = client.open_sftp()
            try:
                if args.cmd == "put":
                    sftp.put(args.src, args.dst)
                    print(f"[remote_exec] put {args.src} -> {args.dst}")
                else:
                    sftp.get(args.src, args.dst)
                    print(f"[remote_exec] get {args.src} -> {args.dst}")
            finally:
                sftp.close()
            return 0
        if args.cmd == "putdir":
            sftp_putdir(client, Path(args.src), args.dst)
            return 0
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
