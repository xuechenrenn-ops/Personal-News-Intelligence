# PNI Cloudflare 调度

## 探针

Cloudflare Workers Free 部署 `worker.mjs`。Wrangler 配置启用了日志和两条 UTC Cron，禁用了预览地址和公网路由。用控制台部署时，按配置文件设置这些项目。`30 22 * * *` 对应北京时间次日 06:30，`30 11 * * *` 对应当天 19:30。请在目标时刻至少 15 分钟前完成部署。

在 GitHub 创建细粒度 Token，仓库访问只选 `xuechenrenn-ops/Personal-News-Intelligence`，只设 `Actions: Read and write`（自动附带 Metadata 读取）。使用到期时间，到期前轮换。Token 只写入 Cloudflare Secret `GITHUB_DISPATCH_TOKEN`。

`PNI_DISPATCH_TARGET` 留空时，Worker 只调用 `validate.yml` 在 `ops/pni-cloudflare-probe` 分支上的只读版本。探针分支不跑新闻、不向微信发送、不读正式台账。保留晨晚各一次真实 Cron 的 Worker 日志和 GitHub 运行链接，核对计划时间、日期、刊期、接受的 run ID 与首步时间。两期均须在计划后 10 分钟内进入探针。

API 每次请求最多 8 秒，临时网络错误或可重试 HTTP 错误最多请求 3 次。常规退避 5 秒、10 秒，尊重不超过 15 秒的 Retry-After。鉴权和参数错误直接结束。日志包含刊期、尝试次数及受理运行 ID；没有响应正文或凭证。

## 待确认的生产切换

1. 探针通过并获用户确认后，选在两期之间且无在途作业时合入通过 Ubuntu 门禁的候选版本。
2. 在现有 Worker 设置变量 `PNI_DISPATCH_TARGET=production`，在 GitHub 仓库设置变量 `PNI_SCHEDULE_SOURCE=cloudflare`。后一开关启用显式刊期入口，并让原生 cron 的生产作业跳过；旧 cron 保留供回退。切换前核对生效配置，并预留 Cron 配置传播时间。
3. 生产保持同一串行并发组、刊期占位和费用结算。进入程序后按原流程核验及推送。接受但投递状态未知时保留正式记录，不因调度重试重发。
4. 连续验收七天 14 期。目标为计划后 10 分钟内进入生产，每期在完成后立即提交推送，核对微信实收和正式台账。零漏发、零重复、零跨日错发。

回滚时先将 Worker 恢复到探针模式，再清除 GitHub 的 `PNI_SCHEDULE_SOURCE` 恢复原生生产调度。切换或回退前核对当期状态及在途任务，保留生产状态分支。恢复默认后，Cloudflare 生产请求会被拒绝，已通过台账占位的刊期继续防重。
