"""T7：阶段1 必要性差分探针 —— 逐组"断开/伪接管"旁路，看游戏还能不能到主界面。

用途（阶段1 的核心问题）：PlayFab / EOS+EAC / Sony 这些**非官方 API** 的连接，
在不可达时会不会阻断单人流程？如果不会，阶段1 就可以不接管它们。

一次运行做完整闭环（可重复、可自动判定）：

1. 起 `tools/mitm_proxy.py`（带 blackhole / fake-200 模式），
2. 备份并临时把 HKCU IE 代理指到它（跑完**必恢复**），
3. 启动游戏，轮询 mock 日志，看是否出现**主界面标志性轮询**
   （`/api/Stats/war/*`、`/api/FriendsV2/Request`、`/api/Episode/*`），
4. 关闭游戏、恢复代理、停代理，
5. 输出 JSON 结论到 `state/evidence/t7/<name>.json`。

判据：`reached_main_menu=true` ⇒ 该组旁路**不阻断**启动到主界面（阶段1 可不接管）。

用法：
    python tools\\t7_probe.py --name playfab-blackhole ^
        --blackhole 974a9.playfabapi.com --timeout 180
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

IE_KEY = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
MAIN_MENU_MARKERS = ("/api/Stats/war/", "/api/FriendsV2/Request", "/api/Episode/")
STEAM_URL = "steam://rungameid/553850"


def _ps(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                          capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace")


def get_ie_proxy() -> dict:
    out = _ps(f"(Get-ItemProperty '{IE_KEY}') | Select-Object ProxyEnable,ProxyServer,"
              f"AutoConfigURL | ConvertTo-Json -Compress")
    try:
        return json.loads(out.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {}


def set_ie_proxy(server: str) -> None:
    _ps(f"Set-ItemProperty '{IE_KEY}' -Name ProxyEnable -Value 1; "
        f"Set-ItemProperty '{IE_KEY}' -Name ProxyServer -Value '{server}'")


def restore_ie_proxy(old: dict) -> None:
    enable = int(old.get("ProxyEnable") or 0)
    server = old.get("ProxyServer") or ""
    _ps(f"Set-ItemProperty '{IE_KEY}' -Name ProxyEnable -Value {enable}; "
        f"Set-ItemProperty '{IE_KEY}' -Name ProxyServer -Value '{server}'")


def game_running() -> bool:
    out = _ps("(Get-Process -Name helldivers2 -ErrorAction SilentlyContinue | Measure-Object).Count")
    return out.stdout.strip() not in ("", "0")


def kill_game(graceful_seconds: int = 8) -> None:
    _ps("Get-Process -Name helldivers2 -ErrorAction SilentlyContinue | "
        "ForEach-Object { $_.CloseMainWindow() | Out-Null }")
    for _ in range(graceful_seconds):
        if not game_running():
            return
        time.sleep(1)
    _ps("Get-Process -Name helldivers2 -ErrorAction SilentlyContinue | Stop-Process -Force")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="T7 必要性差分探针")
    p.add_argument("--name", required=True, help="本次运行名字（输出文件名）")
    p.add_argument("--proxy-port", type=int, default=18092)
    p.add_argument("--timeout", type=int, default=180, help="等待主界面的秒数")
    p.add_argument("--stability", type=int, default=25,
                   help="到主界面后再观察多少秒（抓 EAC 等异步失败）")
    p.add_argument("--intercept", default=(
        "974a9.playfabapi.com,modules-cdn.eac-prod.on.epicgames.com,api.epicgames.dev,"
        "datarouter.ol.epicgames.com,testament.api.wwsga.me,clientconfig.api.wwsga.me,"
        "crs.api.wwsga.me,pss-cloud.net,api.live.prod.thehelldiversgame.com"))
    p.add_argument("--blackhole", default="", help="逗号分隔：模拟不可达的域名")
    p.add_argument("--fake", default="", help="逗号分隔：一律回 200 {} 的域名")
    p.add_argument("--cert-dir", type=Path, default=REPO / "state/certs")
    p.add_argument("--mock-log", type=Path, default=REPO / "state/logs/mock.log")
    p.add_argument("--out-dir", type=Path, default=REPO / "state/evidence/t7")
    args = p.parse_args(argv)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.out_dir / f"mitm-{args.name}.jsonl"
    for f in (log_path,):
        if f.exists():
            f.unlink()

    if game_running():
        print("[t7] 先关闭已在运行的游戏…")
        kill_game()
    baseline = args.mock_log.stat().st_size if args.mock_log.exists() else 0

    cmd = [sys.executable, str(REPO / "tools/mitm_proxy.py"),
           "--port", str(args.proxy_port), "--intercept", args.intercept,
           "--mock-hosts", "api.live.prod.thehelldiversgame.com",
           "--mock-ca", str(args.cert_dir / "ca.pem"),
           "--cert-dir", str(args.cert_dir), "--log", str(log_path)]
    if args.blackhole:
        cmd += ["--blackhole-hosts", args.blackhole]
    if args.fake:
        cmd += ["--fake-hosts", args.fake]
    proxy = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             cwd=str(REPO))
    time.sleep(2.5)
    old = get_ie_proxy()
    result = {"name": args.name, "blackhole": args.blackhole, "fake": args.fake,
              "reached_main_menu": False, "stable": None, "elapsed_s": None, "login_seen": False,
              "new_lines": 0, "non_200": 0, "error": None, "old_proxy": old}
    try:
        set_ie_proxy(f"127.0.0.1:{args.proxy_port}")
        print(f"[t7] {args.name}: 代理已指向 127.0.0.1:{args.proxy_port}，启动游戏…")
        subprocess.Popen(["cmd", "/c", "start", STEAM_URL], shell=False)
        t0 = time.time()
        deadline = t0 + args.timeout
        while time.time() < deadline:
            time.sleep(4)
            if not args.mock_log.exists():
                continue
            with open(args.mock_log, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(baseline)
                chunk = fh.read()
            if not chunk:
                continue
            if "Account/Login" in chunk:
                result["login_seen"] = True
            if any(marker in chunk for marker in MAIN_MENU_MARKERS):
                result["reached_main_menu"] = True
                result["elapsed_s"] = round(time.time() - t0, 1)
                break
        else:
            result["error"] = "timeout: 未出现主界面轮询"

        # ---- 稳定性窗口：抓异步失败（如 EAC 网络检查失败后弹框并退出游戏）----
        if result["reached_main_menu"]:
            t1 = time.time()
            stable = True
            exit_after = None
            while time.time() - t1 < args.stability:
                time.sleep(2)
                if not game_running():
                    stable = False
                    exit_after = round(time.time() - t1, 1)
                    break
            result["stable"] = stable
            result["exit_after_s"] = exit_after
            titles = _ps("Get-Process | Where-Object { $_.MainWindowTitle } | "
                         "Select-Object -ExpandProperty MainWindowTitle -Unique | Out-String")
            out = (getattr(titles, "stdout", None) or "")
            result["window_titles"] = [t.strip() for t in out.splitlines() if t.strip()]
            eac = _ps("Get-Process -ErrorAction SilentlyContinue | Where-Object { "
                      "$_.ProcessName -match '^(start_protected_game|EasyAntiCheat)' } | "
                      "Select-Object -ExpandProperty ProcessName -Unique | Out-String")
            result["eac_processes"] = [p.strip() for p in (getattr(eac, "stdout", None) or "").splitlines() if p.strip()]
    finally:
        kill_game()
        restore_ie_proxy(old)
        proxy.terminate()
        try:
            proxy.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proxy.kill()

    # 统计本次窗口的 mock 请求（官方 API 是否仍健康）
    if args.mock_log.exists():
        with open(args.mock_log, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(baseline)
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        result["new_lines"] = len(lines)
        result["non_200"] = sum(1 for ln in lines if "-> " in ln and "-> 200" not in ln
                                and "-> 202" not in ln)
    # 判定必须在 new_lines 统计之后：EAC-on 启动时 EasyAntiCheat_EOS 进程本来就在，
    # 不能拿进程当"被拦"判据；用「主界面轮询行数 + 25 s 稳定窗口」区分：
    #   R1(PlayFab 黑洞)=35 行稳定 / R2(EOS 黑洞)=7 行且弹 EAC 错误框后退出 / R3=41 / R4=39。
    steady = result["new_lines"] >= 15
    result["steady_polling"] = steady
    if result.get("stable") and steady:
        result["verdict"] = "stable"
    elif not steady:
        result["verdict"] = "eac_blocked"
    else:
        result["verdict"] = "exited"
    if log_path.exists():
        recs = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
        result["mitm_events"] = {r.get("event"): sum(1 for x in recs if x.get("event") == r.get("event"))
                                 for r in recs if "event" in r}
    (args.out_dir / f"{args.name}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[t7] 结论: {json.dumps(result, ensure_ascii=False)}")
    ok = result.get("verdict") == "stable"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
