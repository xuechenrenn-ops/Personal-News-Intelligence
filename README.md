# Personal News Intelligence

面向个人的中文新闻情报系统：每日采集、证据核查、双模型筛选、学术会议提醒，并通过 PushPlus 推送微信。

## 当前架构

- **骨科 / 运动医学 / 医疗 AI：** Baichuan-M3-Plus 负责主题筛选和医学证据复核。
- **国内外综合资讯：** DeepSeek-Flash 负责国内政策、民生、科技经济及国际重大事件。
- **来源配置：** `sources.json` 统一维护 RSS 和官方公告列表页；新增来源前先验证可访问性和内容质量。
- **医学证据：** MedicalXpress 用于发现线索；PubMed 既用于主动发现近期论文，也用于核实入选新闻。PubMed 记录不等于论文全文。
- **国内官方信息：** 国家卫健委、国家药监局、国家医保局公告列表页作为补充来源；网页采集结果只作为候选线索，正式政策事实以原文为准。
- **学术会议：** `meeting_calendar.json` 保存已由组织方官网核实的会议日期；`meeting_tracker.py` 在每日简报中展示未来30天会议，并对7天内开幕的会议标注重要提醒。
- **微信推送：** PushPlus。默认 `push_enabled=false`，只生成预览。

## 来源体系

### 医学 RSS
- MedicalXpress Orthopedics
- MedicalXpress Sports Medicine
- MedicalXpress General（仅补充线索）
- npj Digital Medicine
- Nature Medicine

### 国内综合新闻 RSS
- 中国新闻网：即时、社会、财经、生活、国际
- 新华网：国际、财经、科技

### 国际综合新闻 RSS
- BBC World
- Al Jazeera

### 国内官方公告列表页
- 国家卫健委政策文件、新闻动态
- 国家药监局医疗器械公告
- 国家医保局政策法规

### 原始医学证据与临床试验
- PubMed / NCBI E-utilities：主动检索近30天骨科、运动医学和临床 AI 论文。
- 中国临床试验注册中心（ChiCTR）：作为国内试验核查入口；当前尚未接入未经验证的非公开 API。
- ClinicalTrials.gov：后续可作为国际试验注册的独立数据源接入。

**重要：** RSS/API 和网页访问会受到网站策略、网络出口、反爬机制及页面结构变化影响。配置中列出的来源不等于已经证明长期稳定；每次运行都会记录读取结果，失败来源不得被当作“没有相关新闻”。

## 学术会议追踪

会议日历目前只录入已由组织方官方页面确认的日期，包括：
- AAOS Annual Meeting 2027
- AOSSM Annual Meeting 2027
- ESSKA Congress 2028

国内 COA 和中华医学会运动医疗分会列入持续关注范围，但下一届日期尚未核实前不录入日历。发现官方通知后再更新 `meeting_calendar.json`，不根据往年惯例猜测日期。

提醒规则：
- 未来30天：在每日简报显示。
- 未来7天：每日简报标注“重要提醒”，并在真实推送开启时额外发送独立 PushPlus 会议提醒；按会议ID和开幕日期去重，只提醒一次。安全预览模式只显示提醒预览，不发送消息。
- 会议已结束：自动从未来会议列表隐藏。
- 会议报告、会议摘要与同行评议论文分开标注，不把会议摘要当作高等级临床证据。

## 新闻编辑规则

- 普通新闻优先最近24小时；重大事件最多放宽至48小时。
- 医学论文与指南可追踪近30–90天，明确标注发表日期。
- 国内政策、监管公告优先使用官方原文；媒体报道用于发现和解释事件。
- 国内外新闻不设置僵硬比例，优先考虑对中国临床实践和社会现实的影响。
- FDA 获批不等于 NMPA 获批；国外指南和诊疗路径不自动等于国内标准。
- 医学新闻区分人体临床研究、动物研究、体外研究、综述和会议摘要。
- 不编造 DOI、PMID、样本量、效应值、P 值、置信区间或发布日期。
- 目标每天5–6条精选内容；质量不足时允许少于目标数量。

## 跨日去重

- 只在 PushPlus 明确返回成功后，才把实际推送的新闻 URL 写入 `.cache/news_state.json`。
- GitHub Actions 使用 `actions/cache` 在同一分支的后续运行中恢复状态。
- 安全预览模式不会写入已推送记录。
- 缓存可能因 GitHub 保留策略或缓存淘汰而失效；因此它是实用型去重，不是永久数据库。

## GitHub Secrets

在 Settings → Secrets and variables → Actions 中配置：
- `BAICHUAN_API_KEY`
- `DEEPSEEK_API_KEY`
- `PUSHPLUS_TOKEN`

密钥只从 GitHub Secrets 注入环境变量，不写入代码或日志。安全预览模式不要求 PushPlus Token；只有选择真实推送时才要求该 Token。

## 手动测试

### 第一步：单独检查信息源（不调用模型、不发送消息）

GitHub → Actions → Check News Sources → Run workflow。该流程只访问 RSS、官方公告页和会议来源页，输出 HTTP 状态、RSS 条目数、页面链接数和失败原因，不需要任何 API Secret，也不会推送微信。

### 第二步：运行完整简报（保持安全模式）

GitHub → Actions → Daily News Push → Run workflow：

1. 选择 `push_enabled=false`。
2. 查看 RSS 与官方网页采集状态、HTTP 状态码、各来源候选数量。
3. 检查 PubMed 主动发现与原始摘要匹配情况。
4. 检查百川医学筛选与证据复核、DeepSeek 综合新闻是否独立完成。
5. 检查国内官方公告是否保留了原文链接，日期不明时是否明确标注。
6. 检查会议日历日期、倒计时和官方链接。
7. 确认简报质量后，才单独进行真实推送测试。

工作流会先执行 Python 语法检查和 JSON 配置校验。任何一个模型模块失败时，另一个模块仍会尝试运行；两个模块都没有结果时，任务失败且不会推送。

## 自动运行

目前仅开放 `workflow_dispatch` 手动运行，尚未配置 schedule。待来源可靠性、新闻质量和安全预览连续测试通过后，再单独确认每日北京时间18:00定时运行。
