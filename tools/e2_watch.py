#!/usr/bin/env python3
"""E2 后端日志跟随器：经 SSH 持续 tail 远端 mock.log，打印新增行并落地本地证据文件。

用于 E2 实测期间记录「真实客户端」全部请求（含 HDR 形状行），事后切片分析。
不修改远端任何内容，只读。

用法：
    python tools/e2_watch.py --servers-json <servers.json> --via default \
        --remote /opt/hd2-discovery/state/logs/mock.log \
        --local state/evidence/e2prep/e2-mock-watch.log
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import paramiko
except ImportError:  # pragma: no cover
    sys.exit("缺 paramiko：pip install paramiko")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跟随远端日志（只读）")
    ap.add_argument("--servers-json", required=True)
    ap.add_argument("--via", default="default")
    ap.add_argument("--remote", required=True)
    ap.add_argument("--local", required=True)
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args(argv)

    entries = json.loads(Path(args.servers_json).read_text(encoding="utf-8"))
    cred = next((e for e in entries if e.get("name") == args.via), None)
    if cred is None:
        sys.exit(f"servers.json 里没有 {args.via!r}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(cred["host"], port=int(cred.get("port", 22)),
                   username=cred["username"], password=cred["password"],
                   timeout=20, look_for_keys=False, allow_agent=False)
    client.get_transport().set_keepalive(15)
    sftp = client.open_sftp()

    local_path = Path(args.local)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    fh = local_path.open("a", encoding="utf-8")
    offset = sftp.stat(args.remote).st_size
    print(f"[watch] 起点 {offset} B -> {local_path}", flush=True)

    try:
        while True:
            try:
                size = sftp.stat(args.remote).st_size
                if size < offset:          # 文件被轮转/清空
                    offset = 0
                if size > offset:
                    with sftp.open(args.remote, "rb") as rf:
                        rf.seek(offset)
                        data = rf.read(size - offset)
                    offset = size
                    text = data.decode("utf-8", "replace")
                    fh.write(text)
                    fh.flush()
                    sys.stdout.write(text)
                    sys.stdout.flush()
            except (OSError, EOFError) as exc:
                print(f"[watch] 读取失败（重连）: {exc!r}", flush=True)
                try:
                    sftp.close(); client.close()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(3)
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(cred["host"], port=int(cred.get("port", 22)),
                               username=cred["username"], password=cred["password"],
                               timeout=20, look_for_keys=False, allow_agent=False)
                client.get_transport().set_keepalive(15)
                sftp = client.open_sftp()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("[watch] 停止", flush=True)
    finally:
        try:
            fh.close(); sftp.close(); client.close()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
