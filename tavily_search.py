import requests
from urllib.parse import urlparse
from cache_manager import monthly_tavily_credits, record_tavily_credits

TAVILY_URL="https://api.tavily.com/search"
MONTHLY_CREDIT_BUDGET=700
SEARCHES=[
{"key":"ortho_clinical","group":"medical","prefix":"TM","query":"recent orthopedic and musculoskeletal clinical research sports medicine arthroscopy ligament meniscus shoulder foot ankle fracture spine arthroplasty osteoporosis rehabilitation perioperative outcomes","topic":"general","time_range":"month","source_category":"骨科临床研究"},
{"key":"ortho_ai","group":"medical","prefix":"TA","query":"artificial intelligence in orthopedics sports medicine musculoskeletal medicine clinical decision support prediction surgical robotics navigation rehabilitation gait wearable large language models multimodal federated learning","topic":"general","time_range":"month","source_category":"骨科AI研究"},
{"key":"medical_ai","group":"medical","prefix":"TI","query":"recent clinical artificial intelligence research healthcare large language models agents RAG multimodal causal inference clinical decision support medical robotics wearable digital twin real world data","topic":"general","time_range":"month","source_category":"医疗AI交叉研究"},
{"key":"china_health_policy","group":"general","prefix":"TN","query":"中国 医疗卫生 医保 医院改革 药品医疗器械监管 公共卫生 医疗服务 最新政策 公告","topic":"news","time_range":"day","source_category":"中国医疗政策与民生"},
{"key":"china_world_news","group":"general","prefix":"TW","query":"中国国内及国际 重大科技 社会 经济 公共卫生 新闻 最新进展","topic":"news","time_range":"day","source_category":"国内外重要动态"},
{"key":"nsfc_official","group":"nsfc","prefix":"NF","query":"国家自然科学基金委 医学科学部 H06 运动系统 骨科 运动医学 人工智能 交叉科学部 项目指南 资助通知","topic":"general","time_range":"month","include_domains":["nsfc.gov.cn"],"source_category":"NSFC官方指南与项目动态"}]

def _domain(url):
    host=urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host or "搜索来源"

def fetch_tavily_candidates(api_key):
    output={"medical":[],"general":[],"nsfc":[]}; seen=set(); used=0
    for spec in SEARCHES:
        if monthly_tavily_credits()>=MONTHLY_CREDIT_BUDGET:
            print(f"Tavily月度预算达到{MONTHLY_CREDIT_BUDGET} credits，暂停主动搜索。",flush=True); break
        payload={"query":spec["query"],"search_depth":"basic","topic":spec["topic"],
                 "time_range":spec["time_range"],"max_results":5,"include_answer":False,
                 "include_raw_content":False,"include_usage":True}
        if spec.get("include_domains"): payload["include_domains"]=spec["include_domains"]
        try:
            response=requests.post(TAVILY_URL,headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},json=payload,timeout=35)
            response.raise_for_status(); data=response.json()
        except Exception as exc:
            status=getattr(getattr(exc,"response",None),"status_code",None)
            print(f"Tavily搜索失败：{spec['key']}｜HTTP={status or 'unknown'}｜{type(exc).__name__}",flush=True); continue
        usage=data.get("usage") or {}; credits=usage.get("credits") or data.get("credits") or 1
        try: total=record_tavily_credits(int(credits))
        except (TypeError,ValueError): total=record_tavily_credits(1)
        used+=1
        for result in data.get("results",[]):
            url=str(result.get("url") or "").strip(); title=str(result.get("title") or "").strip()
            if not url.startswith(("http://","https://")) or not title or url in seen: continue
            seen.add(url)
            published=result.get("published_date") or result.get("date") or result.get("published_at") or "搜索结果未提供"
            output[spec["group"]].append({"id":f"{spec['prefix']}{len(output[spec['group']])+1}",
                "title":title[:400],"source":_domain(url),"published_at":str(published),"url":url,
                "summary":str(result.get("content") or result.get("snippet") or "")[:1800],
                "source_category":spec["source_category"],"discovery_source":"Tavily"})
        print(f"Tavily｜{spec['key']}｜结果{len(data.get('results',[]))}条｜月度已用{total}/{MONTHLY_CREDIT_BUDGET} credits",flush=True)
    output["queries_used"]=used
    return output
