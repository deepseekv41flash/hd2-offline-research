"""HD2 离线单机 / 自建联机的本地 mock 服务端（研究线，独立于产品仓库）。

模块分层（全部可单测，不依赖游戏）:

  capture   — 读产品 capture 目录（index.txt + 响应体）
  rules     — 路径归一 / 匹配索引（exact → base → fallback）
  timeshift — 抓包时间 → 当前时间的字段平移（战争时钟 / Unix / 倒计时）
  state     — 有状态端点（登录 / 大厅 / 作战 / 任务链）
  handler   — (method, path, body) → Response 的纯逻辑路由（无 socket）
  certs     — 研究线自签 CA + 叶子 + CRL（Schannel 可接受的配方）
  server    — 把 handler 挂到 TLS 443（LAN IP）+ CRL 服务

设计纪律:
  * 只读产品仓库 / 用户运行态；数据与状态都在研究线自己的目录；
  * 抓包含敏感字段（connectionString / playerName / accountId），落盘前必须 scrub；
  * 任何「客户端会不会接受」的判断都标三态，未实机验证的不写成结论。
"""

__version__ = "0.1.0"
