# PNI 功能清单与验收标准（交接资料）

用途：防止后续迭代再次遗漏已实现的检索、筛选、生成、核验与补选能力。
版本基线：`upgrade/news-pni-v1.7`，本文件随代码同步维护。
任何改动检索来源、核验规则、补选机制或调度配置前，先读本文件。

---

## 一、检索与采集能力（必须全部在线）

| # | 能力 | 实现位置 | 触发条件 | 验收标准 |
|---|---|---|---|---|
| 1 | PubMed | `news_push.fetch_pubmed_candidates` → `channels.collect` | 每次晨刊采集 | 工件 `channels` 含 `pubmed`，命中数 > 0 |
| 2 | Europe PMC（5 路查询） | `channels.europe` + `EU_QUERIES` | 晨刊采集（cooldown 保护） | 工件含 `europe:*` |
| 3 | Crossref 独立题名检索 | `channels.crossref_discover` / `crossref_card` | 晨刊 `crossref_search=True` | 工件含 `crossref-*` |
| 4 | Crossref 未索引 DOI 补链 | `channels.collect` 的 unindexed 段 | 晨刊采集 | 无异常即通过 |
| 5 | 学术 RSS（npj 等） | `channels.scholarly_rss` / `ACADEMIC_FEEDS` | 晨刊采集 | 工件含 `*-rss` |
| 6 | 中文财经/国际 RSS | `discover.fetch_candidates`（wider=True） | 晚刊采集 | 工件 acquisition 含中新网/BBC/Al Jazeera |
| 7 | 官方网站（医保局/工信部/载人航天） | `discover.fetch_official_page_candidates` | 晚刊采集 | acquisition 含对应来源 |
| 8 | Tavily 免费额度主动搜索 | `channels.search_web` + `WEB_TOPICS`（en / zh-institution / zh-media / wechat） | `TAVILY_KEY` 存在且 `credit_limit>0` | 工件 `source_report.tavily` 有 attempted/credits；**paygo>0 时必须 0 次尝试** |
| 9 | DeepSeek 独立 Search | `v15.sources.SearchFetch.search` | 按需（见下） | `tool_calls` 含 tool=search，status 非 transport_error |
| 10 | DeepSeek 独立 Fetch | `v15.sources.SearchFetch.fetch` | 按需 | `tool_calls` 含 tool=fetch |
| 11 | DeepSeek 学术补漏 `academic()` | `v15.sources.academic` | **晨刊每次**（`edition is None or edition=="morning"`） | `source_report.deepseek_search.trigger == "bounded_gap_fill_morning"` |
| 12 | 晚刊重大事件补漏 `evening_events()` | `v15.sources.evening_events` | 世界候选中官方重大事件 < 1 且工具额度未用尽 | `source_report.evening_events.trigger == "thin_major_event_coverage"`；已知 URL 必须去重 |
| 13 | 国自然（NSFC）追踪 | `v15.sources.alerts` 的 FUNDING 查询 + `FUNDING_SCOPE` 过滤 | 仅晨刊 | `source_report.notice_search` 记录 results；命中时入 dated_alerts |
| 14 | 重要会议追踪 | `v15.sources.alerts` 的 MEETINGS 查询 + `aossm_calendar` 直连 | 仅晨刊 | `source_report.official_calendar` 记录 status/dated_entries |
| 15 | 备用检索访问探测 | `channels.ACCESS` + `SourceHealth` | 每次 collect | `source_report.access_probes` 记录四项探测 |
| 16 | 30/90 天扩窗补选 | `curation.widen`（Europe PMC，免费） | 补选缺口存在且 live | 工件 channels 含 `europe:<slot>:30d/90d` |

### 关键约束
- **Tavily 只用已核实免费额度**：`search_web` 在 `paygo>0` 时直接拒绝；`room < credit_limit+16` 时拒绝；每次尝试前复查 `room>=16`。**禁止付费超额**。
- **科研编辑只用完整原始 Abstract**：晨刊证据恒为 `{"u": original_abstract}`，不得用全文补查替代。
- **DeepSeek 模型内托管搜索已明确取消**，不重新开启。
- **百川已明确取消**，不恢复。
- **付费 Tavily 已明确取消**。

---

## 二、筛选、评分与淘汰

| # | 能力 | 实现位置 | 验收标准 |
|---|---|---|---|
| 17 | 六维价值评分（25/25/20/15/10/5） | `v14.pipeline.WEIGHTS` + `ranked_candidates` | 权重与决策一致 |
| 18 | 晚刊事件评分 `news_value`（4-5 入选） | `ranked_candidates` | `run_audit.news_value` 记录；低分事件不得入选 |
| 19 | 事件主题兜底（WORLD_EVENTS 不匹配时用模型主题） | `ranked_candidates` 的 group 回退 | 未知主题高价值事件不被丢弃 |
| 20 | 排名 token 按候选规模 | `rank_tokens = max(2200, 900+55×候选数)` | 小批次不得低于历史下限 2200 |
| 21 | 模型原始响应/结束原因/解析异常落盘 | `Flash` 保存 raw_content/finish_reason/parse_error → `run_audit.raw_scores` | `raw_scores` 非空且含三字段 |

---

## 三、生成、核验与纠错

| # | 能力 | 实现位置 | 验收标准 |
|---|---|---|---|
| 22 | 晨刊科研字段（研究目的/研究方法与结果/结论） | `RESEARCH_WRITE` + `MORNING_FIELDS` | 三段渲染齐全 |
| 23 | 30 秒导读（核心研究问题＋主要发现） | `readable_lead` + Few-shot 范例 | 导读独立可懂 |
| 24 | 单段结论（作者结论＋局限融合） | `stage2.pipeline` 渲染 | 无「作者结论/编辑评价」标签 |
| 25 | 晚刊三段（summary/why/impact） | `EVENING_WRITE` + `BRIEF_WRITE` | 三段各有信息 |
| 26 | 事实核验 `verify_fact` | `stage2.quality` | 错误数字必须被拒 |
| 27 | 标题数字源（标题＋日期＋证据合并） | `usable_field` | 证据中的数字不得误判 |
| 28 | 日期作为证据源 | `usable_field` 的 source | summary 中的日期不得误判 |
| 29 | 数字提取（版本号紧贴字母） | `NUMBER` 正则 `(?<![0-9.])` | DRG/DIP2.0 的 2.0 可提取 |
| 30 | 跨语言数字识别 | `number_set` 英文数词＋月份名 | "three"↔3、"October"↔10 |
| 31 | 早晚核验隔离（lane 参数） | `usable_field(lane=...)` | 新闻 `s` 引用可用；晨刊仍强制 `r/u` |
| 32 | 针对性纠错 `repair_results` | `write_verified`（v17 含） | 核验失败先纠错再补选 |
| 33 | 补选 `fill_brief`（同类候选＋重核验） | `v14.pipeline.fill_brief` | 不放弃整个栏目；不降标准 |

---

## 四、审计与可观测性

| # | 能力 | 实现位置 | 验收标准 |
|---|---|---|---|
| 34 | `source_report` 落盘 | `run_audit.source_report` + `summary.source_report` | 含 per_window/access_probes/tavily/notice_search/official_calendar/deepseek_search/evening_events |
| 35 | 工具调用与费用 | `run_audit.tool_calls` | 每条含 tool/status/observed_peak_micro_cny |
| 36 | 渠道命中与选题贡献 | `run_audit.channel_contribution` | 每渠道含 candidates/published |
| 37 | 候选全链路追踪 | `candidate_audit`（acquisition/medical/news/reserve） | 每条候选有 outcome |
| 38 | 逐字段核验结果与失配数字 | `field_gate` 的 `_rejected` | 含 reason/proposed/extra_numbers |
| 39 | 生成草稿原文 | `run_audit.drafts` | 模型原始草稿落盘 |
| 40 | 完整原始 Abstract | `run_audit.medical[*].original_abstract` | 晨刊逐篇完整 |
| 41 | 模型原始评分 | `run_audit.scores` / `raw_scores` | 含结束原因与解析异常 |

---

## 五、调度与部署

| # | 能力 | 当前状态 | 验收标准 |
|---|---|---|---|
| 42 | 晨刊 06:30（UTC `30 22 * * *`） | `pni/editions.py` | cron 一致 |
| 43 | 晚刊 19:30（UTC `30 11 * * *`） | `pni/editions.py` | cron 一致 |
| 44 | 生产密钥接线和 Tavily 免费额度守卫 | `news-push.yml` / `channels.search_web` | Tavily 显式启用才注入。读不到可信免费额度或检测到超额用量时跳过 |
| 45 | 研发 workflow 分支含 `upgrade/news-pni-v1.7` | experiment*/source-check/validate | 该分支 push 时触发 |
| 46 | 月度 ¥10 预算保护 | `pni.production.MONTHLY` + `check_month` | 超限拒绝 |
| 47 | 研发 preview 预算晨 ¥1/晚 ¥1 | `PREVIEW_RESERVATION` | 合计 ¥2 |
| 48 | 异常消耗保护 | `Flash.remaining` 保守扣减 | 不可绕过 |
| 49 | 发送防重与状态留档 | `claim` / `GitHubState` | 同日同刊不重复 |
| 50 | 产物保留 7 天 | `upload-artifact` retention-days | — |

---

## 六、已明确取消（不得恢复）

| 项目 | 依据 |
|---|---|
| 付费 Tavily | DECISIONS「付费 Tavily 不启用」；V1.5 报告 |
| 百川 M3-Plus | DECISIONS「选择 DeepSeek 单模型，不重复做百川对照」 |
| Chat Completions 内托管搜索工具 | v15 报告「本次没有在 Chat Completions 开启它」 |
| 每日综合简报 / 多条拆分 | DECISIONS「双时段版替代每日综合简报和常规多条拆分」 |
| 装饰统计与重复标题 | DECISIONS 视觉样式 |
| WorkBuddy 云端凭证持久化 / Server酱 / ClawBot | DECISIONS「不再沿用」 |

---

## 七、当前待办（交接时必须处理）

1. 本地双时段工作流待授权部署。通过最终提交的 Ubuntu 测试后，再验证 Secrets 权限、微信实收和七天审计。Tavily 默认关闭，免费配置确认后可单独启用。
2. 晚刊 round5 实测产出 1 篇医疗民生、2 篇重大事件、1 篇科技产业。两处事实状态偏差的原始工件已用于离线回归。
3. 政策候选最多两篇参与取证与核验，成刊最多保留一篇，分别用于候补和版面配额。

---

## 八、回归测试契约

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_restored_features.py` | Tavily 免费额度守卫与接线、academic 晨刊触发、晚刊补漏触发/去重、source_report/channel_contribution、晚刊科技栏占位、晨刊 Abstract 原则 |
| `tests/test_round2_regression.py` | round2 真实数据：引用规则、数字提取（版本号＋跨语言）、标题日期比对、错误数字仍被拒、事件不误丢 |
| `tests/test_editions.py` | 双时段发布、容量裁剪、预算与生产隔离、审计落盘、事件兜底 |
| `tests/test_brief_offline.py` | 有状态回放、发送防重、缓存恢复 |
| `tests/test_pni_v17.py` / `test_pni_production.py` | 月度保护、调度键、验收触发 |

**任何改动上表能力后，必须新增或更新对应回归测试，并全量跑通 `python -m unittest discover -s tests`。**
**禁止用"无条件满分/无条件核验通过"的 mock 替代关键逻辑测试。**

---

## 九、产品验收门槛（摘自 PROJECT.md）

1. 分别评审晨晚两份完整成品，缺项记录检索及淘汰证据。
2. 原始出处可追溯，关键医学数据完整准确，分析与已核实事实分离。
3. 导读独立可懂，正文无机械重复；微信手机端清晰，含深色环境兼容测试。
4. 两期隔离、超限整篇裁剪、发送防重、费用、缓存和状态回归通过；预览不污染生产。
5. 离线预览先经用户认可；之后才授权一次有界真实测试、微信实收确认，最后明确批准生产合并。
