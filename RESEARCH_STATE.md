# Personal News Intelligence 研发断点

更新日期 2026-11-26，北京时间。本文件是本分支的续研入口。唯一允许写入 `upgrade/news-pipeline-v1`。基线主分支 SHA `8967e6b72d4c0298eecbe7f76db645f9c71996b8`。

- 当前阶段为第三阶段恢复，已完成核心优化 5/30。已有 13 份运行清单、12 份作业领取记录。取消固定付费触发次数上限。
- 历史未释放预留 ¥5.37，累计模型峰值估算 ¥0.443576，历史 Tavily 16 credits。按搜索最坏分摊合计 ¥2.043576。预留包含已发生支出，累计上限 ¥20。
- 进入第三阶段时原优化自动任务已停用，Actions 无在途或排队的作业。已有实验结果保存于 `experiments/stage2/results/stage2-metrics.json`，已完成的付费任务不重复。
- 当前最佳已验证基线为 `e99ddd4af2e79519ea28957028c3eebca1fe6534`，交付快照 `715b14488ac15bc4a7744e449bac7437d56e42c3`。该版本第五轮实时的专业正文 0 条，拓展两条，现阶段质量未验收。
- 下一步先用官方元数据复现 ACL 和 osteotomy 漏报，改为以可回溯的摘要证据为渲染输入，并区分拒收原因。再在未见日期和实时源上验证，按专业产出、重点召回、准确性和成本比较。
- 暂停的外部事项为百川 M3-Plus 的自动医疗搜索计费无可核实的单请求硬上限，余额未核实。DeepSeek 和免费检索不受影响。
- 恢复操作依次读取此文件、核对分支头及 `experiments/dispatch/`、`experiments/claims/`、Actions 最近运行状态。运行 `python -m unittest discover -s tests` 验证代码。真实发送、正式调度与跨日稳定性尚未验收。
