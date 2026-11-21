"""T8-P0：用已有 T4 抓包做「数据面」明文统计（不重跑游戏、不需要管理员）。

回答三件事（对应 T8 计划 §2 路径 D）：
1. **STUN（UDP 3478/3479）**：多少探测、对端是谁、映射到的公网地址（IP 脱敏）、节奏；
2. **PlayFab QoS（UDP 3075）**：每个 Azure 区多少包、载荷大小与固定头、是否有 RTT 配对；
3. **DTLS（高端口 UDP）**：记录数、content_type、是否有握手包（能否被 keylog 解密的前置判断）。

用法：
    python tools\\analyze_pcap_dataplane.py --pcap D:\\mod\\tmp\\t4\\acp-20261121-0144.pcapng
输出：终端报告 + `state/evidence/t8/pcap-dataplane.txt`
"""

from __future__ import annotations

import argparse
import collections
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

TSHARK_CANDIDATES = [
    r"C:\Program Files\Wireshark\tshark.exe",
    r"C:\Program Files (x86)\Wireshark\tshark.exe",
    "tshark",
]


def find_tshark() -> str:
    for c in TSHARK_CANDIDATES:
        if Path(c).exists():
            return c
    return "tshark"


def run(tshark: str, pcap: Path, args: list[str]) -> list[str]:
    p = subprocess.run([tshark, "-r", str(pcap)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return [ln for ln in p.stdout.splitlines() if ln.strip()]


def mask_ip(ip: str) -> str:
    """公网 IP 脱敏：保留前两段。"""
    try:
        a = ipaddress.ip_address(ip)
        if a.is_private or a.is_loopback or a.is_link_local:
            return ip
        parts = ip.split(".")
        return f"{parts[0]}.{parts[1]}.x.x" if a.version == 4 else f"{ip[:12]}…"
    except ValueError:
        return ip


def stun_section(tshark: str, pcap: Path) -> list[str]:
    out = ["## 1. STUN（UDP 3478/3479）"]
    rows = run(tshark, pcap, ["-Y", "udp.port==3478 or udp.port==3479",
                              "-T", "fields", "-e", "ip.src", "-e", "ip.dst",
                              "-e", "udp.srcport", "-e", "udp.dstport",
                              "-e", "udp.length", "-e", "udp.payload"])
    if not rows:
        return out + ["（无 STUN 流量）"]
    peers = collections.Counter()
    sizes = collections.Counter()
    mapped = []
    for r in rows:
        f = r.split("\t")
        while len(f) < 6:
            f.append("")
        src, dst, sp, dp, ulen, payload = f[:6]
        peers[f"{mask_ip(dst)}:{dp}"] += 1
        sizes[ulen] += 1
        # STUN Binding Success Response(0x0101) 里的 XOR-MAPPED-ADDRESS(0x0020)
        if payload.startswith("0101"):
            m = re.search(r"0020(.{4})0001(....)(........)", payload)
            if m:
                mapped.append(payload[:32])
    out.append(f"  包数: {len(rows)}；对端: {dict(peers)}")
    out.append(f"  UDP 长度分布: {dict(sizes)}")
    out.append("  样例（前 32 hex，含 txn id）:")
    for s in mapped[:3]:
        out.append(f"    {s}")
    out.append("  判读: 3478/3479 = STUN Binding；响应含 XOR-MAPPED-ADDRESS ⇒ 公网映射发现（IP 已脱敏）")
    return out


def qos_section(tshark: str, pcap: Path) -> list[str]:
    out = ["", "## 2. PlayFab QoS（UDP 3075）"]
    rows = run(tshark, pcap, ["-Y", "udp.port==3075", "-T", "fields",
                              "-e", "ip.dst", "-e", "udp.length", "-e", "udp.payload"])
    if not rows:
        return out + ["（无 QoS 流量）"]
    per_dst = collections.Counter()
    sizes = collections.Counter()
    heads = collections.Counter()
    for r in rows:
        f = r.split("\t")
        while len(f) < 3:
            f.append("")
        dst, ulen, payload = f[:3]
        per_dst[mask_ip(dst)] += 1
        sizes[ulen] += 1
        if payload:
            heads[payload[:8]] += 1
    out.append(f"  包数: {len(rows)}；目标数: {len(per_dst)}")
    out.append(f"  UDP 长度分布: {dict(sizes)}")
    out.append(f"  载荷前 8 hex 分布: {dict(heads)}")
    out.append(f"  按目标分布（前 8）: {dict(list(per_dst.most_common(8)))}")
    out.append("  判读: 每区固定长度探测包；头部固定 ⇒ 可由 RTT 配对推断区域选择（内容本身不可读，无需解）")
    return out


def dtls_section(tshark: str, pcap: Path) -> list[str]:
    out = ["", "## 3. 高端口 UDP（DTLS 数据面）"]
    rows = run(tshark, pcap,
               ["-Y", "udp && udp.dstport > 1024 && udp.dstport != 3075 && "
                       "udp.dstport != 3478 && udp.dstport != 3479",
                "-T", "fields", "-e", "ip.dst", "-e", "udp.dstport", "-e", "udp.length"])
    if not rows:
        return out + ["（无高端口 UDP）"]
    ports = collections.Counter()
    sizes = collections.Counter()
    for r in rows:
        f = r.split("\t")
        while len(f) < 3:
            f.append("")
        _dst, dp, ulen = f[:3]
        ports[dp] += 1
        sizes[int(ulen or 0) // 100 * 100] += 1
    out.append(f"  包数: {len(rows)}；端口分布: {dict(ports.most_common(6))}")
    out.append(f"  长度分桶(百字节): {dict(sorted(sizes.items()))}")
    # 尝试按 DTLS 解析，看是否有握手（有握手 = keylog 解密才有内容可解）
    hs = run(tshark, pcap, ["-d", "udp.port==45368,dtls", "-Y", "dtls",
                            "-T", "fields", "-e", "dtls.record.content_type"])
    ct = collections.Counter(x for x in hs if x)
    out.append(f"  45368 按 DTLS 解析: records={len(hs)} content_type 分布={dict(ct)}")
    out.append("  判读: content_type 23=application_data ⇒ 已加密；解密只能靠 keylog/明文 hook，网络层不可解")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T8-P0 数据面明文统计")
    p.add_argument("--pcap", type=Path, default=Path(r"D:\mod\tmp\t4\acp-20261121-0144.pcapng"))
    p.add_argument("--out", type=Path, default=REPO / "state/evidence/t8/pcap-dataplane.txt")
    args = p.parse_args(argv)
    if not args.pcap.exists():
        raise SystemExit(f"找不到抓包: {args.pcap}")
    tshark = find_tshark()
    lines = [f"# T8-P0 数据面统计：{args.pcap.name}（{args.pcap.stat().st_size/1e6:.1f} MB）",
             f"# tshark = {tshark}", ""]
    lines += stun_section(tshark, args.pcap)
    lines += qos_section(tshark, args.pcap)
    lines += dtls_section(tshark, args.pcap)
    report = "\n".join(lines)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report + "\n", encoding="utf-8")
    print(report)
    print(f"\n[saved] {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
