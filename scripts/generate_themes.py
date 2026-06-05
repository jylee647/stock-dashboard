"""
기술 테마 전망 생성 — GitHub Actions 하루 4회.
뉴스 수집(네이버+구글뉴스) → 1단계 Haiku 요약 → 2단계 Sonnet(tool-use로 JSON 강제) → themes.json.
"""
import os
import re
import html
import json
import time
import datetime
import xml.etree.ElementTree as ET

import requests

ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
STAGE1_MODEL = os.environ.get("STAGE1_MODEL", "claude-haiku-4-5")
STAGE2_MODEL = os.environ.get("STAGE2_MODEL", "claude-sonnet-4-6")
OUT = os.environ.get("THEMES_OUT", "themes.json")

QUERIES_KR = ["반도체 AI 전망", "2차전지 전기차", "로봇 자동화", "바이오 신약", "방산 우주항공", "전력 에너지"]
QUERIES_US = ["AI semiconductor outlook", "battery EV technology", "robotics automation stocks",
              "biotech breakthrough", "defense space technology", "energy grid power", "quantum computing"]
PER = 6

API = "https://api.anthropic.com/v1/messages"
HEADERS = {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def fetch_naver(q):
    if not (NAVER_ID and NAVER_SECRET):
        return []
    try:
        r = requests.get("https://openapi.naver.com/v1/search/news.json",
                         params={"query": q, "display": PER, "sort": "date"},
                         headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET}, timeout=10)
        if r.status_code != 200:
            return []
        return [{"region": "KR", "title": strip_tags(i.get("title")), "desc": strip_tags(i.get("description"))}
                for i in r.json().get("items", [])]
    except Exception:
        return []


def fetch_gnews(q):
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        return [{"region": "US", "title": (it.findtext("title") or ""), "desc": strip_tags(it.findtext("description") or "")}
                for it in root.find("channel").findall("item")[:PER]]
    except Exception:
        return []


def collect():
    out = []
    for q in QUERIES_KR:
        out += fetch_naver(q)
    for q in QUERIES_US:
        out += fetch_gnews(q)
    seen, uniq = set(), []
    for a in out:
        k = (a.get("title") or "")[:40]
        if k and k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def _post(payload):
    for attempt in range(3):
        try:
            r = requests.post(API, headers=HEADERS, json=payload, timeout=120)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 529):
                time.sleep(3 * (attempt + 1)); continue
            print("API 오류", r.status_code, r.text[:300]); return None
        except Exception as e:
            print("API 예외", e); time.sleep(2)
    return None


def claude_text(model, system, user, max_tokens):
    d = _post({"model": model, "max_tokens": max_tokens, "system": system,
               "messages": [{"role": "user", "content": user}]})
    if not d:
        return ""
    try:
        return d["content"][0]["text"]
    except Exception:
        return ""


# 2단계 출력 스키마 (tool-use로 유효 JSON 강제)
_STOCK_KR = {"type": "object", "properties": {"name": {"type": "string"}, "code": {"type": "string"}, "why": {"type": "string"}}, "required": ["name"]}
_STOCK_US = {"type": "object", "properties": {"name": {"type": "string"}, "ticker": {"type": "string"}, "why": {"type": "string"}}, "required": ["name"]}
_THEME = {"type": "object", "properties": {
    "theme": {"type": "string"}, "summary": {"type": "string"},
    "confidence": {"type": "string", "enum": ["높음", "중간", "낮음"]},
    "drivers": {"type": "array", "items": {"type": "string"}},
    "stocks": {"type": "object", "properties": {"KR": {"type": "array", "items": _STOCK_KR}, "US": {"type": "array", "items": _STOCK_US}}}},
    "required": ["theme", "summary"]}
_HORIZON = {"type": "object", "properties": {"label": {"type": "string"}, "themes": {"type": "array", "items": _THEME}}, "required": ["label", "themes"]}
_GROUP = {"type": "object", "properties": {"horizons": {"type": "array", "items": _HORIZON}}, "required": ["horizons"]}
THEME_TOOL = {
    "name": "emit_themes",
    "description": "기술 테마 전망 결과를 구조화해 제출",
    "input_schema": {"type": "object", "properties": {
        "short": _GROUP, "long": _GROUP,
        "crossCheckNote": {"type": "string"}, "sources": {"type": "array", "items": {"type": "string"}}},
        "required": ["short", "long"]},
}


def stage1(batch):
    lines = "\n".join(f"{i+1}. [{a['region']}] {a['title']} :: {(a.get('desc') or '')[:200]}" for i, a in enumerate(batch))
    sys = ("너는 금융·기술 뉴스 분석가다. 아래 뉴스를 읽고 각 기술/산업 테마별로 핵심을 한국어로 압축 요약하라. "
           "형식: \"- [지역KR/US] <기술테마>: <핵심 1줄> (긍정/부정/중립)\". 광고·중복·무관 기사 제외. 최대 12줄.")
    return claude_text(STAGE1_MODEL, sys, lines, 1500)


def stage2(summaries):
    sys = ("너는 시니어 기술/주식 애널리스트다. 아래 국내(KR)·해외(US) 뉴스 요약을 교차검증하여(한쪽에만 있으면 신뢰도↓, 일치하면↑), "
           "향후 핵심 기술 분야를 기간별로 도출하라. 단기 short=[\"1개월 내\",\"3개월 내\"], 장기 long=[\"6개월 내\",\"1년 내\",\"1년 이상\"]. "
           "각 기간 핵심테마 2~3개, 테마마다 관련 종목 국내(이름+6자리코드)·해외(이름+티커) 각 1~2개. "
           "반드시 emit_themes 도구로 제출하라.")
    d = _post({"model": STAGE2_MODEL, "max_tokens": 8000, "system": sys,
               "tools": [THEME_TOOL], "tool_choice": {"type": "tool", "name": "emit_themes"},
               "messages": [{"role": "user", "content": summaries}]})
    if not d:
        return None
    for block in d.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == "emit_themes":
            return block.get("input")
    print("tool_use 블록 없음:", json.dumps(d)[:300])
    return None


def main():
    articles = collect()
    print(f"수집 기사: {len(articles)}")
    if not articles:
        return 1
    summaries = []
    for i in range(0, len(articles), 12):
        s = stage1(articles[i:i + 12])
        if s:
            summaries.append(s)
        time.sleep(0.4)
    print(f"1단계 요약 묶음: {len(summaries)}")
    obj = stage2("\n".join(summaries) if summaries else "뉴스 요약 없음. 일반적 기술 전망으로 작성.")
    if not obj:
        print("2단계 실패")
        return 1
    kst = datetime.timezone(datetime.timedelta(hours=9))
    obj["generatedAt"] = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")
    obj["model"] = {"stage1": STAGE1_MODEL, "stage2": STAGE2_MODEL}
    obj["articleCount"] = len(articles)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {OUT} ({obj['generatedAt']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
