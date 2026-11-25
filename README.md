# Personal News Intelligence

每日新闻采集、AI辅助筛选与微信推送。

## 当前版本
- 百川 Baichuan-M3-Plus：骨科、运动医学及医疗AI资讯。
- DeepSeek-Flash：国内外重大事件、民生健康及科技经济资讯。
- 医学RSS：MedicalXpress骨科、运动医学及综合医学源；医学筛选严格聚焦骨科、运动医学和医疗AI。
- 综合新闻RSS：中国新闻网即时、社会、财经、生活、国际频道；新华网国际、财经、科技频道；BBC World、Al Jazeera。
- RSS采集使用HTTP请求、超时和状态检查；来源名称缺失时回退到订阅源名称，并记录各源有效候选数。
- 综合新闻兼顾中国国内与国际视角，避免大量选择缺乏现实影响的礼仪性会议报道；不设置僵硬配额，不为凑数而选。
- Python保留原始新闻ID、日期和链接，并校验模型返回的ID。
- 默认安全模式：push_enabled=false，只生成日志预览，不发送微信。
- 只有明确选择 push_enabled=true 才会调用PushPlus。

## GitHub Secrets
在 Settings → Secrets and variables → Actions 中配置：
- BAICHUAN_API_KEY
- DEEPSEEK_API_KEY
- PUSHPLUS_TOKEN

不要将密钥写入代码、提交记录或日志。

## 手动测试
Actions → Daily News Push → Run workflow：
1. 首次测试保持 push_enabled=false。
2. 查看日志，确认RSS源HTTP读取成功、各源候选数量合理、两个模型调用成功、JSON解析成功、新闻链接来自原始RSS。
3. 特别检查中国国内新闻与国际新闻是否均有覆盖，医学新闻是否真正聚焦骨科、运动医学和医疗AI。确认预览内容后，再单独手动运行并选择 push_enabled=true 进行真实推送测试。

## 自动运行
目前尚未启用定时任务。待手动测试通过后，再配置每天北京时间18:00运行。
