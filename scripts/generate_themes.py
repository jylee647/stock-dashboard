"""
기술 테마 전망 생성 — GitHub Actions에서 하루 4회 실행.
뉴스 수집(네이버+구글뉴스) → 1단계 Haiku 요약 → 2단계 Sonnet 종합 → themes.json 저장.
환경변수: ANTHROPIC_API_KEY(필수), NAVER_CLIENT_ID/SECRET(선택), STAGE1_MODEL/STAGE2_MODEL(선택)
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


def strip_tags(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


def fetch_naver(q):
    if not (NAVER_ID and NAVER_SECRET):
        return []
    try:
        r = requests.get("https://openapi.naver.com/v1/search/news.json",
                         params={"query": q, "display": PER, "sort": "date"},
                         headers={"X-Naver-Client-Id": NAVER_ID, "X-Naver-Client-Secret": NAVER_SECRET},
                         timeout=10)
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
        items = root.find("channel").findall("item")[:PER]
        return [{"region": "US", "title": (it.findtext("title") or ""),
                 "desc": strip_tags(it.findtext("description") or "")} for it in items]
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


def claude(model, system, user, max_tokens):
    for attempt in range(3):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"},
                              json={"model": model, "max_tokens": max_tokens, "system": system,
                                    "messages": [{"role": "user", "content": user}]},
                              timeout=120)
            if r.status_code == 200:
                return r.json()["content"][0]["text"]
            if r.status_code in (429, 500, 529):
                time.sleep(3 * (attempt + 1))
                continue
            print("Claude error", r.status_code, r.text[:300])
            return ""
        except Exception as e:
            print("Claude exc", e)
            time.sleep(2)
    return ""


def stage1(batch):
    lines = "\n".join(f"{i+1}. [{a['region']}] {a['title']} :: {(a.get('desc') or '')[:200]}"
                      for i, a in enumerate(batch))
    sys = ("너는 금융·기술 뉴스 분석가다. 아래 뉴스 목록을 읽고 각 기술/산업 테마별로 핵심을 한국어로 압축 요약하라. "
           "형식: \"- [지역KR/US] <기술테마>: <핵심내용 1줄> (긍정/부정/중립)\". 광고·중복·무관 기사는 제외. 최대 12줄.")
    return claude(STAGE1_MODEL, sys, lines, 1500)


def stage2(summaries):
    sys = (
        "너는 시니어 기술/주식 애널리스트다. 아래는 국내(KR)·해외(US) 뉴스 요약 모음이다. "
        "국내와 해외 소스를 교차검증하여(한쪽에만 있는 주장은 신뢰도를 낮춤, 양쪽 일치 시 높임), "
        "향후 어떤 기술 분야가 핵심이 될지 기간별로 도출하라.\n"
        "단기(short): \"1개월 내\", \"3개월 내\" / 장기(long): \"6개월 내\", \"1년 내\", \"1년 이상\".\n"
        "각 기간마다 핵심 기술테마 2~3개, 테마마다 관련 종목을 국내(KR: 이름+6자리코드)와 해외(US: 이름+티커)로 제시.\n"
        "반드시 아래 JSON 스키마로만 출력(설명/코드펜스 금지):\n"
        '{"short":{"horizons":[{"label":"1개월 내","themes":[{"theme":"","summary":"","confidence":"높음|중간|낮음","drivers":["",""],"stocks":{"KR":[{"name":"","code":"","why":""}],"US":[{"name":"","ticker":"","why":""}]}}]},{"label":"3개월 내","themes":[]}]},'
        '"long":{"horizons":[{"label":"6개월 내","themes":[]},{"label":"1년 내","themes":[]},{"label":"1년 이상","themes":[]}]},'
        '"crossCheckNote":"국내·해외 교차검증 요약 1~2문장","sources":["네이버뉴스","Google News"]}'
    )
    txt = claude(STAGE2_MODEL, sys, summaries, 4000)
    try:
        return json.loads(txt)
    except Exception:
        m, n = txt.find("{"), txt.rfind("}")
        if m >= 0 and n > m:
            try:
                return json.loads(txt[m:n + 1])
            except Exception:
                return None
    return None


def main():
    articles = collect()
    print(f"수집 기사: {len(articles)}")
    if not articles:
        print("뉴스 없음 — 중단")
        return 1
    summaries = []
    for i in range(0, len(articles), 12):
        s = stage1(articles[i:i + 12])
        if s:
            summaries.append(s)
        time.sleep(0.5)
    obj = stage2("\n".join(summaries))
    if not obj:
        print("2단계 종합 실패")
        return 1
    kst = datetime.timezone(datetime.timedelta(hours=9))
    obj["generatedAt"] = datetime.datetime.now(kst).strftime("%Y-%m-%d %H:%M KST")
    obj["model"] = {"stage1": STAGE1_MODEL, "stage2": STAGE2_MODEL}
    obj["articleCount"] = len(articles)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"저장 완료: {OUT} ({obj['generatedAt']}, 기사 {len(articles)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
