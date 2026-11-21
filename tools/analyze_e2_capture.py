"""E2 抓包判定：数据面走 Steam SDR 还是 PlayFab Party（阶段2 双机实测用）。

背景（见 docs/STAGE2-E1-E3-E4-RESULTS §7 / T4 / P0）：
  * **Steam SDR**：UDP 3478/3479 的 STUN Binding（XOR-MAPPED-ADDRESS），以及到 Valve 中继的高端口 UDP；
  * **PlayFab Party**：UDP 高端口上的 DTLS（record content-type 22 handshake / 23 application_data），
    对端是 Azure 中继；与之配套的还有 UDP 3075 的 PlayFab QoS 探测（一次性突发）。
  * 两者可能**同时存在**（游戏双轨：SDR 优先、PlayFab 兜底），所以本工具输出各自占比与判定，不硬判二选一。

用法：
    python tools/analyze_e2_capture.py --pcap capture.pcapng [--json out.json]
    python tools/analyze_e2_capture.py --self-test      # 用合成计数验证判定逻辑
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TSHARK = r"C:\Program Files\Wireshark\tshark.exe"


def classify(counts: dict) -> dict:
    """纯函数：由抓包计数给出传输通道判定（便于单测，不依赖 tshark）。

    counts 键：
      sdr_stun_packets / sdr_stun_bytes  : UDP 3478+3479 包数/字节
      qos_packets                        : UDP 3075 包数
      dtls_records / dtls_bytes          : DTLS record 数/字节（任意端口）
      playfab_tls                        : 出现 974a9.playfabapi.com 的 TLS SNI 次数
      steam_sni                          : 出现 Steam 相关 SNI 的次数（steamcommunity/steamstatic/steamserver）
    """
    sdr = counts.get("sdr_stun_packets", 0)
    dtls = counts.get("dtls_records", 0)
    qos = counts.get("qos_packets", 0)
    channels: list[str] = []
    if sdr > 0:
        channels.append("steam-sdr")
    if dtls > 0:
        channels.append("playfab-party")
    verdict = "+".join(channels) if channels else "unknown"
    confidence = "high" if (sdr >= 10 or dtls >= 10) else ("medium" if channels else "low")
    return {
        "verdict": verdict,
        "channels": channels,
        "confidence": confidence,
        "metrics": {
            "sdr_stun_packets": sdr,
            "sdr_stun_bytes": counts.get("sdr_stun_bytes", 0),
            "qos_packets": qos,
            "dtls_records": dtls,
            "dtls_bytes": counts.get("dtls_bytes", 0),
            "playfab_tls": counts.get("playfab_tls", 0),
            "steam_sni": counts.get("steam_sni", 0),
        },
        "notes": _notes(counts),
    }


def _notes(counts: dict) -> list[str]:
    out = []
    if counts.get("qos_packets", 0) > 0:
        out.append("出现 PlayFab QoS（UDP 3075）：说明已走到 ListPartyQosServers 之后的探测阶段")
    if counts.get("sdr_stun_packets", 0) == 0 and counts.get("dtls_records", 0) == 0:
        out.append("既无 SDR STUN 也无 DTLS：可能没进联机、或数据面被加密/VPN 劫持")
    if counts.get("sdr_stun_packets", 0) and counts.get("dtls_records", 0):
        out.append("双轨并存：符合「SDR 优先、PlayFab 兜底」的预期，需要看字节占比判断实际主力")
    return out


def _tshark_counts(pcap: Path) -> dict:
    def run(fields: str, dfilter: str) -> list[str]:
        cmd = [TSHARK, "-r", str(pcap), "-Y", dfilter, "-T", "fields", "-e", fields]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SystemExit(f"tshark 运行失败：{exc}") from exc
        if out.returncode != 0:
            raise SystemExit(f"tshark 失败：{out.stderr[:300]}")
        return [line for line in out.stdout.splitlines() if line.strip()]

    stun = run("frame.len", "udp.port == 3478 or udp.port == 3479")
    qos = run("frame.len", "udp.port == 3075")
    dtls = run("frame.len", "dtls")
    sni = run("tls.handshake.extensions_server_name", "tls.handshake.extensions_server_name")
    counts = {
        "sdr_stun_packets": len(stun),
        "sdr_stun_bytes": sum(int(x) for x in stun if x.strip().isdigit()),
        "qos_packets": len(qos),
        "dtls_records": len(dtls),
        "dtls_bytes": sum(int(x) for x in dtls if x.strip().isdigit()),
        "playfab_tls": sum(1 for s in sni if "playfabapi" in s.lower()),
        "steam_sni": sum(1 for s in sni if "steam" in s.lower()),
    }
    return counts


def _self_test() -> int:
    cases = [
        ({"sdr_stun_packets": 456, "dtls_records": 0}, "steam-sdr"),
        ({"dtls_records": 2380, "qos_packets": 120}, "playfab-party"),
        ({"sdr_stun_packets": 10, "dtls_records": 5}, "steam-sdr+playfab-party"),
        ({}, "unknown"),
    ]
    fails = 0
    for counts, expected in cases:
        got = classify(counts)["verdict"]
        ok = got == expected
        print(("  [PASS] " if ok else "  [FAIL] ") + f"{counts} -> {got}（期望 {expected}）")
        fails += 0 if ok else 1
    print("RESULT:", "PASS" if fails == 0 else f"FAIL（{fails}）")
    return 0 if fails == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E2 抓包判定：Steam SDR vs PlayFab Party")
    ap.add_argument("--pcap", default="", help="pcap/pcapng 路径")
    ap.add_argument("--json", default="", help="把结果写到 JSON 文件")
    ap.add_argument("--self-test", action="store_true", help="不抓包，只验证判定逻辑")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.pcap:
        ap.error("需要 --pcap 或 --self-test")
    pcap = Path(args.pcap)
    if not pcap.exists():
        raise SystemExit(f"找不到 pcap：{pcap}")
    counts = _tshark_counts(pcap)
    result = classify(counts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
