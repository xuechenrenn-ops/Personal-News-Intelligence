import os
import feedparser
import requests
import json
from datetime import datetime

# ================= 1. 获取环境变量 =================
pushplus_token = os.environ.get("PUSHPLUS_TOKEN")
baichuan_key = os.environ.get("BAICHUAN_API_KEY")
deepseek_key = os.environ.get("DEEPSEEK_API_KEY")

if not all([pushplus_token, baichuan_key, deepseek_key]):
    print("错误：缺少必要的 API Key，请检查 GitHub Secrets 配置。")
    exit(1)

# ================= 2. 抓取真实信源 =================
print("正在抓取信源...")
# 医疗/科技信源 (MedicalXpress，包含大量骨科与AI最新研究)
med_rss = "https://medicalxpress.com/rss-feed/"
# 综合/民生信源 (联合早报/新华网等，这里用联合早报测试综合与民生)
news_rss = "https://www.zaobao.com.sg/rss/realtime/world" 

def fetch_rss(url, limit=8):
    try:
        feed = feedparser.parse(url)
        texts = []
        for entry in feed.entries[:limit]:
            texts.append(f"标题：{entry.get('title', '')}\n摘要：{entry.get('summary', '')}\n链接：{entry.get('link', '')}")
        return "\n\n".join(texts)
    except Exception as e:
        print(f"抓取 RSS 失败: {url}, 错误: {e}")
        return ""

med_raw = fetch_rss(med_rss)
news_raw = fetch_rss(news_rss)

# ================= 3. 呼叫百川 M3-Plus (医疗专属) =================
print("正在呼叫百川 M3-Plus 提炼循证医学信息...")
baichuan_url = "https://api.baichuan-ai.com/v1/chat/completions"
baichuan_headers = {
    "Authorization": f"Bearer {baichuan_key}",
    "Content-Type": "application/json"
}
# 注入了你原指令中最核心的医学要求
baichuan_prompt = f"""你是一名严谨的临床医学专家兼资深医学编辑。请阅读以下医疗资讯，精选出 2-3 条对骨科/运动医学或医疗AI有参考价值的内容。

【核心要求】
1. 严格遵循循证医学标准。说明研究设计、样本量、主要结局、临床意义及研究局限。
2. 严格区分相关性与因果性。绝对不得编造 DOI、PMID 或研究结论，不得把单项研究直接当成临床指南。
3. 如果原文未提供样本量或局限性，请明确标注“原文未提及”，严禁瞎编。
4. 将英文翻译为专业、流畅的中文。

【输出格式】
每条新闻按以下 Markdown 结构输出：
**1. [中文标题]**
- **发生了什么**：一句话简述核心事件及研究设计。
- **关键数据与局限**：样本量、主要结局、局限性（若无则标“未提及”）。
- **临床意义**：对临床大夫的参考价值。
- *原文链接：[填入URL]*

医疗资讯原文：
{med_raw}
"""
baichuan_data = {
    "model": "Baichuan-M3-Plus",
    "messages": [{"role": "user", "content": baichuan_prompt}],
    "temperature": 0.2 # 医疗任务，温度调低，减少发散
}

try:
    resp_med = requests.post(baichuan_url, headers=baichuan_headers, json=baichuan_data, timeout=120)
    resp_med.raise_for_status()
    med_summary = resp_med.json()['choices'][0]['message']['content']
    print("百川处理完成。")
except Exception as e:
    print(f"百川调用失败：{e}")
    med_summary = "医疗资讯处理失败，请检查日志。"

# ================= 4. 呼叫 DeepSeek-Flash (拓展+民生+晨语) =================
print("正在呼叫 DeepSeek-Flash 整理综合资讯与晨语...")
deepseek_url = "https://api.deepseek.com/chat/completions"
deepseek_headers = {
    "Authorization": f"Bearer {deepseek_key}",
    "Content-Type": "application/json"
}
# 注入了你原指令中的民生、拓展要求，以及晨语生成
deepseek_prompt = f"""你是一名资深媒体主编。请阅读以下新闻，完成两项任务：

【任务一：新闻精选】
精选出 2-3 条国内外重大事件或民生健康新闻。
要求：
1. 语言通俗自然，重点解释“对普通人的影响”。
2. 过滤掉无意义的八卦或广告。

【任务二：今日晨语】
生成一句 12-35 字的温暖短句。要求：温暖、自然、有哲理；不鸡汤、不说教、不刻意煽情。直接输出句子，不要解释。

【输出格式】
### 🌍 国内外重大事件与民生
**1. [中文标题]**
- **发生了什么**：一句话简述。
- **对普通人的影响**：说明对普通人的影响。
- *原文链接：[填入URL]*

**2. [中文标题]**
...

### 🌅 今日晨语
[在此输出你生成的晨语，不要加引号或其他解释]

新闻原文：
{news_raw}
"""
deepseek_data = {
    "model": "deepseek-flash",
    "messages": [{"role": "user", "content": deepseek_prompt}],
    "temperature": 0.7 # 写作任务，温度调高一点，更有文采
}

try:
    resp_news = requests.post(deepseek_url, headers=deepseek_headers, json=deepseek_data, timeout=120)
    resp_news.raise_for_status()
    news_summary = resp_news.json()['choices'][0]['message']['content']
    print("DeepSeek 处理完成。")
except Exception as e:
    print(f"DeepSeek 调用失败：{e}")
    news_summary = "拓展新闻处理失败，请检查日志。"

# ================= 5. 拼装并推送到微信 =================
# 获取当前日期
today_date = datetime.now().strftime("%Y-%m-%d")
weekday = datetime.now().strftime("%A")

final_content = f"""**Personal News Intelligence**
*{today_date} | 专属医学情报*

---

### 🦴 骨关节外科、运动医学与医疗AI
{med_summary}

---

{news_summary}

---
*由百川医疗大模型与 DeepSeek 联合提供智力支持*
"""

print("正在推送到微信...")
resp_push = requests.post(
    "https://www.pushplus.plus/send",
    json={
        "token": pushplus_token,
        "title": f"📬 每日新闻情报 | {today_date}",
        "content": final_content,
        "template": "markdown",
    },
    timeout=15,
)
print("推送状态:", resp_push.status_code)
print("推送响应:", resp_push.text)
