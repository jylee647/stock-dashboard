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


_BENCH_SYM = {"KR": "^KS11", "US": "^GSPC"}


def _bench_ret60(market: str) -> float:
    """지수(^KS11/^GSPC)의 60거래일 수익률 — 상대강도(RS) 기준."""
    try:
        d = signals.get_daily(_BENCH_SYM[market], days=260)
        c = d["close"] if d else []
        if len(c) >= 61 and c[-61]:
            return c[-1] / c[-61] - 1
    except Exception:
        pass
    return 0.0


def recommend(market: str, limit: int = 10) -> Dict:
    """추천 = #강세(백테스트 종목필터 정합) + #반등(과매도 저평가), 구분 태그.
    - #강세: price>MA20>MA60>MA200, 모멘텀+, 과열X, RSI<=70, 지수대비 RS>0 (시장국면 게이트는 제외 -> 하락장에도 표시)
    - #반등: RSI<=35, 60일선 하회, 구조 유지(MA200*0.8 위)
    - 2단계 처리: 가격게이트 먼저 -> 통과 종목만 뉴스/테마태그 (부하 절감)
    """
    ck = f"reco3:{market}:{limit}"
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

    bench_ret60 = _bench_ret60(market)

    # 1단계: 가격/지표 게이트 (뉴스 미조회)
    def _screen(c):
        daily = signals.get_daily(c["yahooSym"], days=260)
        if not daily:
            return None
        closes = daily["close"]
        if len(closes) < 60:
            return None
        price = c.get("price") or closes[-1]
        if not price:
            return None
        ma20 = signals._ma(closes, 20)
        ma60 = signals._ma(closes, 60)
        ma200 = signals._ma(closes, 200)
        rsi = signals._rsi(closes, 14)
        rsi = rsi if rsi is not None else 50.0
        mom20 = (closes[-1] / closes[-21] - 1) if (len(closes) >= 21 and closes[-21]) else 0.0
        ret60 = (closes[-1] / closes[-61] - 1) if (len(closes) >= 61 and closes[-61]) else 0.0
        a20 = (price / ma20 - 1) if ma20 else 0.0
        rs = ret60 - bench_ret60

        sig = None
        score = 0.0
        comps = []
        reason = ""
        # #강세 — 백테스트 종목필터와 동일 (시장국면 게이트 제외)
        if (ma20 and ma60 and ma200 and price > ma20 > ma60 > ma200
                and mom20 > 0 and a20 <= 0.25 and rsi <= 70 and rs > 0):
            sig = "강세"
            trend_str = ma20 / ma60 - 1
            rs_n = min(max(rs, 0.0), 0.6) / 0.6 * 100
            mom_n = min(max(mom20, 0.0), 0.5) / 0.5 * 100
            tr_n = min(max(trend_str, 0.0), 0.3) / 0.3 * 100
            score = round(0.45 * rs_n + 0.35 * mom_n + 0.20 * tr_n, 1)
            comps = [
                {"label": "상대강도(RS)", "basis": "지수 대비 60일 초과수익",
                 "weightPct": 45, "maxPts": 45.0,
                 "normScore": round(rs_n), "points": round(0.45 * rs_n, 1)},
                {"label": "모멘텀(20일)", "basis": "최근 20거래일 수익률",
                 "weightPct": 35, "maxPts": 35.0,
                 "normScore": round(mom_n), "points": round(0.35 * mom_n, 1)},
                {"label": "추세강도", "basis": "MA20/MA60 이격(상승 정렬 강도)",
                 "weightPct": 20, "maxPts": 20.0,
                 "normScore": round(tr_n), "points": round(0.20 * tr_n, 1)},
            ]
            reason = f"추세 정렬(MA20>60>200) · 지수대비 +{rs * 100:.1f}% · RSI {round(rsi)}"
        # #반등 — 과매도 저평가 (별도 로직)
        elif (ma60 and rsi <= 35 and price < ma60 and (not ma200 or price > ma200 * 0.80)):
            sig = "반등"
            depth = max(0.0, (35 - rsi) / 35)
            below = min(max(0.0, -(price / ma60 - 1)) / 0.20, 1.0)
            score = round((0.6 * depth + 0.4 * below) * 100, 1)
            comps = [
                {"label": "과매도(RSI)", "basis": "RSI(14) — 낮을수록 가점",
                 "weightPct": 60, "maxPts": 60.0,
                 "normScore": round(depth * 100), "points": round(0.6 * depth * 100, 1)},
                {"label": "이평선 하회", "basis": "60일선 아래 낙폭(눌림 깊이)",
                 "weightPct": 40, "maxPts": 40.0,
                 "normScore": round(below * 100), "points": round(0.4 * below * 100, 1)},
            ]
            reason = f"과매도 반등 후보 · RSI {round(rsi)} · 60일선 {(price / ma60 - 1) * 100:.1f}%"
        else:
            return None

        return {
            "symbol": c["symbol"], "market": market, "name": c.get("name") or c["symbol"],
            "price": round(price, 2), "changePct": c.get("changePct"),
            "rsi": round(rsi), "signalType": sig, "score": score,
            "components": comps, "reason": reason,
        }

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(_screen, universe):
            if r:
                rows.append(r)

    # 2단계: 통과 종목만 뉴스/테마 태그
    def _enrich(r):
        tags = []
        try:
            nitems = news_mod.get_stock_news(r["symbol"], market, r.get("name", ""), limit=5)
            newstext = " ".join(((n.get("title") or "") + " " + (n.get("summary") or "")) for n in nitems)
            tags = _theme_tags(newstext, r.get("name", ""))
        except Exception:
            tags = _theme_tags("", r.get("name", ""))
        sig_tag = "#강세" if r["signalType"] == "강세" else "#반등"
        r["tags"] = [sig_tag] + tags
        return r

    if rows:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            rows = list(ex.map(_enrich, rows))

    strong = sorted([r for r in rows if r["signalType"] == "강세"], key=lambda x: x["score"], reverse=True)
    rebound = sorted([r for r in rows if r["signalType"] == "반등"], key=lambda x: x["score"], reverse=True)
    items = (strong + rebound)[:limit]

    out = {
        "market": market, "marketTrend": round(market_trend, 2),
        "weights": [
            {"label": "강세(추세추종)", "weightPct": 0,
             "basis": "MA20>60>200 · 모멘텀+ · 지수대비 강세 · RSI<=70 (백테스트 종목필터 정합)"},
            {"label": "반등(과매도)", "weightPct": 0,
             "basis": "RSI<=35 · 60일선 하회 · 구조 유지(MA200*0.8 위)"},
        ],
        "items": items, "disclaimer": _DISCLAIMER,
        "strongCount": len(strong), "reboundCount": len(rebound),
    }
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
