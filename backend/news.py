"""
뉴스 모듈
- 한국(KR): 네이버 뉴스 검색 API (NAVER_CLIENT_ID/SECRET 환경변수가 있으면)
- 미국(US): yfinance 종목 뉴스 / yfinance Search 뉴스
- 키가 없거나 실패하면 가능한 다른 소스로 폴백
"""
from __future__ import annotations

import html
import os
import re
import time
from typing import Dict, List

import requests
import yfinance as yf

_CACHE: Dict[str, tuple] = {}


def _cache_get(key, ttl):
    it = _CACHE.get(key)
    if it and time.time() - it[0] < ttl:
        return it[1]
    return None


def _cache_set(key, val):
    _CACHE[key] = (time.time(), val)


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return html.unescape(s).strip()


# ---------------------------------------------------------------------------
# 네이버 뉴스 (한국)
# ---------------------------------------------------------------------------
def naver_news(query: str, display: int = 10) -> List[Dict]:
    cid = os.getenv("NAVER_CLIENT_ID")
    csec = os.getenv("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        return []
    ck = f"naver:{query}:{display}"
    cached = _cache_get(ck, ttl=120)
    if cached is not None:
        return cached
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": display, "sort": "date"},
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
            timeout=8,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        out = []
        for it in items:
            out.append({
                "title": _strip_tags(it.get("title")),
                "summary": _strip_tags(it.get("description")),
                "link": it.get("originallink") or it.get("link"),
                "source": "네이버뉴스",
                "date": it.get("pubDate", ""),
            })
        _cache_set(ck, out)
        return out
    except Exception:
        return []


# ---------------------------------------------------------------------------
# yfinance 뉴스 (미국/공통)
# ---------------------------------------------------------------------------
def yf_ticker_news(symbol: str, limit: int = 10) -> List[Dict]:
    ck = f"yfnews:{symbol}:{limit}"
    cached = _cache_get(ck, ttl=180)
    if cached is not None:
        return cached
    out: List[Dict] = []
    try:
        raw = yf.Ticker(symbol).news or []
        for n in raw[:limit]:
            out.append(_norm_yf_news(n))
    except Exception:
        pass
    if not out:
        # Search 기반 폴백
        try:
            sr = yf.Search(symbol, news_count=limit, max_results=0)
            for n in (getattr(sr, "news", []) or [])[:limit]:
                out.append(_norm_yf_news(n))
        except Exception:
            pass
    out = [o for o in out if o.get("title")]
    _cache_set(ck, out)
    return out


def _norm_yf_news(n: Dict) -> Dict:
    # yfinance 뉴스 스키마는 버전에 따라 평면/중첩(content) 두 형태가 존재
    content = n.get("content") if isinstance(n.get("content"), dict) else None
    if content:
        title = content.get("title")
        link = ((content.get("clickThroughUrl") or {}) or {}).get("url") \
            or ((content.get("canonicalUrl") or {}) or {}).get("url")
        provider = (content.get("provider") or {}).get("displayName")
        pub = content.get("pubDate") or content.get("displayTime") or ""
        summary = content.get("summary") or content.get("description") or ""
    else:
        title = n.get("title")
        link = n.get("link")
        provider = n.get("publisher")
        pub = n.get("providerPublishTime") or ""
        summary = n.get("summary", "")
        if isinstance(pub, (int, float)):
            pub = time.strftime("%Y-%m-%d %H:%M", time.localtime(pub))
    return {
        "title": title, "summary": summary or "", "link": link,
        "source": provider or "Yahoo Finance", "date": str(pub),
    }


# ---------------------------------------------------------------------------
# 통합 진입점
# ---------------------------------------------------------------------------
def get_stock_news(symbol: str, market: str, name: str = "", limit: int = 10) -> List[Dict]:
    if market == "KR":
        q = name or symbol
        items = naver_news(q, display=limit)
        if items:
            return items
        # 폴백: 야후 .KS 뉴스 (영문일 수 있음)
        suffix = ".KS"
        return yf_ticker_news(f"{symbol}{suffix}", limit)
    else:
        items = yf_ticker_news(symbol, limit)
        if items:
            return items
        return yf_ticker_news(symbol, limit)


def get_market_news(market: str, limit: int = 12) -> List[Dict]:
    """탭 기본 뉴스: 시장 전반."""
    if market == "KR":
        items = naver_news("증시 코스피 코스닥", display=limit)
        if items:
            return items
        return yf_ticker_news("^KS11", limit)
    else:
        # 미국 시장 전반: 주요 지수/대표주 뉴스 모음
        out: List[Dict] = []
        for sym in ("^GSPC", "AAPL", "NVDA"):
            out.extend(yf_ticker_news(sym, 5))
            if len(out) >= limit:
                break
        # 중복 제목 제거
        seen, uniq = set(), []
        for o in out:
            t = o.get("title")
            if t and t not in seen:
                seen.add(t)
                uniq.append(o)
        return uniq[:limit]
