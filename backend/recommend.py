"""
추천 엔진 — '진입가치' 기반 (모멘텀 일변도 X)
점수 = 가중합( 추세건전성, 저평가, 진입여력, 뉴스감성, 거래량·관심도, 시장추세 )
- 이미 상한가/고점에 잠긴 종목은 진입여력 감점 (들어갈 자리가 없음)
- 음봉·하락이라도 과매도·이평선 하회면 '저평가'로 가점
- 후보군 = 당일 상승 + 하락 + 거래활발 종목으로 확대해 저평가주도 포착
※ 투자 자문이 아니라 공개 데이터 기반의 알고리즘 스크리닝입니다.
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Dict, List

import yfinance as yf

from backend import news as news_mod
from backend import providers, signals

POS_KW = [
    "상승", "급등", "강세", "호조", "최고", "신고가", "돌파", "수주", "흑자", "개선",
    "성장", "확대", "호실적", "기대", "수혜", "반등", "목표가 상향", "매수",
    "surge", "soar", "rally", "jump", "gain", "beat", "record", "upgrade", "buy",
    "growth", "strong", "high", "rise", "boost", "bullish", "outperform",
]
NEG_KW = [
    "하락", "급락", "약세", "부진", "최저", "신저가", "적자", "악화", "감소", "리콜",
    "소송", "우려", "쇼크", "하향", "매도", "손실", "경고", "위기",
    "plunge", "drop", "fall", "slump", "miss", "downgrade", "sell", "loss",
    "weak", "low", "cut", "bearish", "underperform", "lawsuit", "warning", "risk",
]

# 테마 태그 키워드 (뉴스/종목명에서 매칭 → #태그)
THEME_TAGS = [
    ("#AI", ["ai", "인공지능", "에이아이", "gpt", "llm", "머신러닝", "딥러닝", "생성형"]),
    ("#반도체", ["반도체", "semiconductor", "hbm", "파운드리", "웨이퍼", "메모리", "디램", "낸드", "soc"]),
    ("#광통신", ["광통신", "광트랜시버", "optical", "광모듈", "실리콘포토닉스", "광인터커넥트"]),
    ("#2차전지", ["2차전지", "이차전지", "배터리", "battery", "양극재", "음극재", "전해질", "전고체"]),
    ("#전기차", ["전기차", "테슬라", "tesla", "자율주행", " ev"]),
    ("#바이오", ["바이오", "제약", "신약", "임상", "biotech", "pharma", "항체", "fda"]),
    ("#로봇", ["로봇", "robot", "휴머노이드", "자동화"]),
    ("#방산", ["방산", "국방", "defense", "미사일", "무기", "우주항공", "방위"]),
    ("#양자", ["양자", "quantum"]),
    ("#전력인프라", ["변압기", "송전", "전력기기", "grid", "전선", "전력망"]),
    ("#원전", ["원전", "원자력", "nuclear", "smr"]),
    ("#에너지", ["태양광", "풍력", "수소", "재생에너지", "solar", "energy"]),
    ("#금융", ["증권", "은행", "보험", "지주", "금융", "bank", "fintech"]),
    ("#게임", ["게임", "game"]),
    ("#엔터", ["엔터", "연예", "k-pop", "아이돌", "음반"]),
    ("#조선", ["조선", "선박", "shipbuilding", "해양"]),
    ("#화장품", ["화장품", "뷰티", "cosmetic"]),
    ("#철강", ["철강", "steel", "포스코"]),
]


def _theme_tags(text: str, name: str) -> List[str]:
    blob = (text + " " + (name or "")).lower()
    out = []
    for tag, kws in THEME_TAGS:
        if any(kw in blob for kw in kws):
            out.append(tag)
        if len(out) >= 3:
            break
    return out


_CACHE: Dict[str, tuple] = {}


def _cache_get(k, ttl):
    it = _CACHE.get(k)
    if it and time.time() - it[0] < ttl:
        return it[1]
    return None


def _cache_set(k, v):
    _CACHE[k] = (time.time(), v)


def _score_news(items: List[Dict]):
    if not items:
        return 0.0, 0
    pos = neg = 0
    for n in items:
        text = ((n.get("title") or "") + " " + (n.get("summary") or "")).lower()
        for kw in POS_KW:
            if kw.lower() in text:
                pos += 1
        for kw in NEG_KW:
            if kw.lower() in text:
                neg += 1
    total = pos + neg
    sentiment = (pos - neg) / total if total else 0.0
    return sentiment, len(items)


def _minmax(vals):
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return [50.0 for _ in vals]
    return [(v - lo) / (hi - lo) * 100 for v in vals]


# ---------------------------------------------------------------------------
# 후보 유니버스 (상승 + 하락 + 거래활발)
# ---------------------------------------------------------------------------
def _build_universe(market: str, per: int = 12) -> List[Dict]:
    out: List[Dict] = []
    seen = set()

    def add(sym, mkt, name, price, chg, vol, ysym):
        key = (mkt, sym)
        if key in seen:
            return
        seen.add(key)
        out.append({"symbol": sym, "market": mkt, "name": name, "price": price,
                    "changePct": chg, "volume": vol, "yahooSym": ysym})

    if market == "US":
        for screen in ("day_gainers", "day_losers", "most_actives"):
            try:
                data = yf.screen(screen, count=per)
                for q in (data.get("quotes", []) if isinstance(data, dict) else []):
                    sym = q.get("symbol")
                    if not sym:
                        continue
                    add(sym, "US", q.get("shortName") or sym,
                        _r(q.get("regularMarketPrice")), _r(q.get("regularMarketChangePercent")),
                        q.get("regularMarketVolume"), sym)
            except Exception:
                continue
        if not out:
            for s in providers.US_FALLBACK:
                add(s, "US", s, None, None, None, s)
    else:  # KR
        rows = _kr_universe_pykrx(per)
        for r in rows:
            add(r["symbol"], "KR", r["name"], r["price"], r["changePct"], r["volume"], r["yahooSym"])
        if not out:
            for code, (name, suffix) in providers.KR_FALLBACK.items():
                add(code, "KR", name, None, None, None, f"{code}{suffix}")
    return out


def _kr_universe_pykrx(per: int) -> List[Dict]:
    if not (providers._pykrx_available()):
        return []
    try:
        from pykrx import stock
    except Exception:
        return []
    today, prev = providers._recent_two_bdays()
    rows: List[Dict] = []
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_price_change(prev, today, market=mkt)
        except Exception:
            continue
        suffix = ".KS" if mkt == "KOSPI" else ".KQ"
        for code, r in df.iterrows():
            chg = _r(r.get("등락률"))
            vol = r.get("거래량")
            rows.append({"symbol": code, "name": r.get("종목명") or code,
                         "price": _r(r.get("종가")), "changePct": chg,
                         "volume": int(vol) if vol == vol else None,
                         "yahooSym": f"{code}{suffix}", "suffix": suffix})
    rows = [x for x in rows if x.get("changePct") is not None]
    # 상승 상위 + 하락 하위 + 거래량 상위
    gainers = sorted(rows, key=lambda x: x["changePct"], reverse=True)[:per]
    losers = sorted(rows, key=lambda x: x["changePct"])[:per]
    actives = sorted(rows, key=lambda x: (x["volume"] or 0), reverse=True)[:per]
    picked, seen = [], set()
    for x in gainers + losers + actives:
        if x["symbol"] in seen:
            continue
        seen.add(x["symbol"])
        providers.KR_FALLBACK.setdefault(x["symbol"], (x["name"], x["suffix"]))
        picked.append(x)
    return picked


def _r(v):
    try:
        return round(float(v), 2) if v is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 추천 산출
# ---------------------------------------------------------------------------
WSPEC = [
    ("추세건전성", 0.20, "20일선 대비 위치 (건전한 상승 가점, 과열 감점)"),
    ("저평가", 0.20, "RSI 과매도·이평선 하회 정도 (쌀수록 가점)"),
    ("진입여력", 0.20, "최근 고점 대비 남은 공간 (상한가·고점잠김 감점)"),
    ("뉴스감성", 0.15, "관련 뉴스 제목·요약의 긍정/부정 키워드 비율"),
    ("거래량·관심도", 0.15, "거래량 급증 배수 + 관련 뉴스 건수"),
    ("시장추세", 0.10, "해당 시장 지수들의 평균 등락률"),
]


def recommend(market: str, limit: int = 10) -> Dict:
    ck = f"reco2:{market}:{limit}"
    cached = _cache_get(ck, ttl=300)
    if cached:
        return cached

    universe = _build_universe(market)

    market_trend = 0.0
    try:
        idx = providers.get_indices()
        pcts = [x["changePct"] for x in idx.get(market, []) if x.get("changePct") is not None]
        if pcts:
            market_trend = sum(pcts) / len(pcts)
    except Exception:
        pass
    trend_norm = (max(min(market_trend, 3), -3) / 3 * 100 + 100) / 2  # 0~100 공통

    def _process(c):
        daily = signals.get_daily(c["yahooSym"])
        if not daily:
            return None
        ind = signals.indicators(daily, c.get("price"), c.get("changePct"), market)
        nitems = []
        try:
            nitems = news_mod.get_stock_news(c["symbol"], market, c.get("name", ""), limit=5)
            sent, nvol = _score_news(nitems)
        except Exception:
            sent, nvol = 0.0, 0
        newstext = " ".join(((n.get("title") or "") + " " + (n.get("summary") or "")) for n in nitems)
        tags = _theme_tags(newstext, c.get("name", ""))
        # 진입여력
        entry = ind["room"]
        if ind["limitLocked"]:
            entry *= 0.15
        elif ind["overheatedToday"]:
            entry *= 0.5
        # 추세건전성: +5% 부근 최고, 과열(+30%)·급락(-20%)서 0
        a = ind["aboveMa20"] or 0.0
        trend = max(0.0, 1 - abs(a - 0.05) / 0.25)
        if a < -0.15:
            trend *= 0.6
        # 저평가: RSI 낮고 60일선 아래일수록 큼
        rsi = ind["rsi"] if ind["rsi"] is not None else 50.0
        value = 0.6 * max(0.0, (55 - rsi) / 55) + 0.4 * min(max(0.0, -(ind["aboveMa60"] or 0.0)) / 0.20, 1.0)
        # 거래량·관심도
        interest = 0.6 * min((ind["volSurge"] or 1.0) / 3.0, 1.0) + 0.4 * min(nvol / 8.0, 1.0)
        return {
            "symbol": c["symbol"], "market": market, "name": c.get("name") or c["symbol"],
            "price": c.get("price") or ind.get("price"), "changePct": c.get("changePct"),
            "rsi": round(rsi) if rsi is not None else None,
            "tags": tags,
            "_entry": entry, "_trend": trend, "_value": value, "_sent": sent, "_int": interest,
            "_limit": ind["limitLocked"],
        }

    # 종목별 외부조회(일봉+뉴스)를 병렬로 — 순차 대비 수십초 → 수초
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(_process, universe):
            if r:
                rows.append(r)

    if not rows:
        out = {"market": market, "marketTrend": round(market_trend, 2), "items": [],
               "weights": [{"label": l, "weightPct": round(w * 100), "basis": b} for l, w, b in WSPEC],
               "disclaimer": _DISCLAIMER}
        _cache_set(ck, out)
        return out

    nEntry = _minmax([r["_entry"] for r in rows])
    nTrend = _minmax([r["_trend"] for r in rows])
    nValue = _minmax([r["_value"] for r in rows])
    nSent = [(r["_sent"] + 1) / 2 * 100 for r in rows]
    nInt = _minmax([r["_int"] for r in rows])

    for i, r in enumerate(rows):
        norms = {"추세건전성": nTrend[i], "저평가": nValue[i], "진입여력": nEntry[i],
                 "뉴스감성": nSent[i], "거래량·관심도": nInt[i], "시장추세": trend_norm}
        comps, total = [], 0.0
        for label, w, basis in WSPEC:
            n = norms[label]
            pts = w * n
            total += pts
            comps.append({"label": label, "weightPct": round(w * 100), "maxPts": round(w * 100, 1),
                          "normScore": round(n), "points": round(pts, 1), "basis": basis})
        r["score"] = round(total, 1)
        r["components"] = comps
        r["breakdown"] = {c["label"]: c["normScore"] for c in comps}
        r["reason"] = _reason(r, norms)
        for k in ("_entry", "_trend", "_value", "_sent", "_int", "_limit"):
            r.pop(k, None)

    rows.sort(key=lambda x: x["score"], reverse=True)
    out = {"market": market, "marketTrend": round(market_trend, 2),
           "weights": [{"label": l, "weightPct": round(w * 100), "basis": b} for l, w, b in WSPEC],
           "items": rows[:limit], "disclaimer": _DISCLAIMER}
    _cache_set(ck, out)
    return out


def _reason(r, norms) -> str:
    bits = []
    if norms["저평가"] >= 65:
        bits.append("저평가 구간(과매도·이평선 하회)")
    if norms["추세건전성"] >= 65:
        bits.append("건전한 상승 추세")
    if norms["진입여력"] >= 65:
        bits.append("고점 대비 진입 여력")
    elif norms["진입여력"] <= 30:
        bits.append("고점 부근(진입 부담)")
    if norms["뉴스감성"] >= 65:
        bits.append("긍정적 뉴스 우세")
    if r.get("changePct") is not None and r["changePct"] < 0:
        bits.append(f"오늘 {r['changePct']:.1f}%이나 가치 신호 양호")
    if not bits:
        bits.append("종합 지표 상위")
    return " · ".join(bits[:3])


_DISCLAIMER = (
    "본 추천은 공개된 시세·기술지표·뉴스·지수를 가중 점수화한 알고리즘 스크리닝 결과이며, "
    "투자 자문이나 매수 권유가 아닙니다. 미래 수익을 보장하지 않으며 투자 판단과 책임은 본인에게 있습니다."
)
