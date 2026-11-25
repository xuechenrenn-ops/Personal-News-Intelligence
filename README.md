# Personal News Intelligence

每日新闻采集、AI辅助筛选与微信推送。

## 当前版本
- 百川 Baichuan-M3-Plus：骨科、运动医学及医疗AI资讯。
- DeepSeek-Flash：国内外重大事件、民生健康及科技经济资讯。
- 当前RSS来源：MedicalXpress、联合早报（初始信源，后续可扩展）。
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
2. 查看日志，确认两个模型调用成功、JSON解析成功、新闻链接来自原始RSS。
3. 确认预览内容后，再单独手动运行并选择 push_enabled=true 进行真实推送测试。

## 自动运行
目前尚未启用定时任务。待手动测试通过后，再配置每天北京时间18:00运行。
