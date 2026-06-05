"""
백테스트(재설계) — '지금 사면 좋은 종목' 추천 + 그 방식의 과거 적중률 검증.
- 상단: 이 가격기반 선정 방식이 과거 N개월간 적중률 X%, 평균수익 +Y% (지수 대비)
- 본문: 오늘의 추천 종목 리스트 = 추천엔진 결과 + 목표가/손절가/보유기간
※ 뉴스는 과거 재현이 불가해 '과거 적중률'은 가격지표 기반 재현 검증값입니다.
※ 투자 자문이 아니며 미래 수익을 보장하지 않습니다.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from backend import providers, recommend, signals

_CACHE: Dict[str, tuple] = {}
_BENCH = {"KR": "^KS11", "US": "^GSPC"}
_BENCH_NAME = {"KR": "코스피", "US": "S&P 500"}
_HOLD_LABEL = {5: "1주", 20: "1달", 60: "3달"}


def _universe(market: str) -> Dict[str, str]:
    if market == "US":
        return {s: s for s in providers.US_FALLBACK}
    return {f"{code}{suf}": name for code, (name, suf) in providers.KR_FALLBACK.items()}


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).rolling(n).mean()
    loss = (-d.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


# ===========================================================================
def run_backtest(market: str, months: int = 6, hold: int = 20, top: int = 10) -> Dict:
    market = market.upper()
    months = max(1, min(int(months), 18))
    hold = max(5, min(int(hold), 60))
    top = max(1, min(int(top), 15))
    ck = f"bt2:{market}:{months}:{hold}:{top}"
    c = _CACHE.get(ck)
    if c and time.time() - c[0] < 900:
        return c[1]

    validation = _validate(market, months, hold, top)
    picks = _today_picks(market, hold, top)

    out = {
        "market": market, "months": months, "hold": hold,
        "holdLabel": _HOLD_LABEL.get(hold, f"{hold}거래일"),
        "validation": validation,
        "picks": picks,
        "disclaimer": (
            "‘과거 적중률’은 동일한 가격지표(추세·저평가·진입여력)로 과거를 재현해 검증한 값이며, "
            "뉴스 등 일부 요소는 과거 재현이 불가해 제외했습니다. 목표가·손절가는 최근 변동성 기반 참고치입니다. "
            "투자 자문이 아니며 미래 수익을 보장하지 않습니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out


# ===========================================================================
# 과거 적중률 검증 (가격기반 방식 재현)
# ===========================================================================
def _validate(market: str, months: int, hold: int, top: int) -> Dict:
    uni = _universe(market)
    syms = list(uni.keys())
    bench = _BENCH[market]
    period = f"{months + 4}mo"
    try:
        raw = yf.download(syms + [bench], period=period, interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return {"error": f"데이터 다운로드 실패: {str(e)[:100]}"}

    close = {}
    vol = {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            cl = sub["Close"].dropna()
            if len(cl) > 65:
                close[s] = cl
                vol[s] = sub["Volume"].reindex(cl.index)
        except Exception:
            continue
    if len(close) < 5:
        return {"error": "유효 종목 데이터 부족(네트워크 확인)"}

    closeDF = pd.DataFrame(close).sort_index()
    volDF = pd.DataFrame(vol).reindex(closeDF.index)
    try:
        benchS = (raw[bench]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).reindex(closeDF.index).ffill()
    except Exception:
        benchS = closeDF.mean(axis=1)

    ma20 = closeDF.rolling(20).mean()
    ma60 = closeDF.rolling(60).mean()
    rmax40 = closeDF.rolling(40).max()
    vavg20 = volDF.rolling(20).mean()
    rsi = closeDF.apply(_rsi)
    ret1 = closeDF.pct_change()

    dates = closeDF.index
    warm = 60
    if len(dates) <= warm + hold + 1:
        return {"error": "기간이 짧습니다. 개월 수를 늘려보세요."}

    rebal = list(range(warm, len(dates) - hold, hold))
    pick_rets: List[float] = []
    bench_rets: List[float] = []
    equity = 1.0

    for t in rebal:
        scores = {}
        for s in closeDF.columns:
            price = closeDF[s].iloc[t]
            if not np.isfinite(price):
                continue
            m20, m60, rh, r, va, dr = (ma20[s].iloc[t], ma60[s].iloc[t], rmax40[s].iloc[t],
                                       rsi[s].iloc[t], vavg20[s].iloc[t], ret1[s].iloc[t])
            if not (np.isfinite(m20) and np.isfinite(rh) and rh > 0):
                continue
            a20 = price / m20 - 1
            a60 = (price / m60 - 1) if np.isfinite(m60) else 0.0
            room = max(0.0, min(1.0, (rh - price) / rh))
            if market == "KR" and np.isfinite(dr) and dr >= 0.29:
                room *= 0.15
            elif np.isfinite(dr) and dr >= 0.15:
                room *= 0.5
            trend = max(0.0, 1 - abs(a20 - 0.05) / 0.25)
            if a20 < -0.15:
                trend *= 0.6
            rr = r if np.isfinite(r) else 50.0
            value = 0.6 * max(0.0, (55 - rr) / 55) + 0.4 * min(max(0.0, -a60) / 0.20, 1.0)
            vs = (volDF[s].iloc[t] / va) if (np.isfinite(va) and va) else 1.0
            interest = min((vs if np.isfinite(vs) else 1.0) / 3.0, 1.0)
            scores[s] = 0.28 * trend + 0.28 * value + 0.28 * room + 0.16 * interest
        if not scores:
            continue
        picks = sorted(scores, key=scores.get, reverse=True)[:top]
        fwd = []
        for s in picks:
            p0, p1 = closeDF[s].iloc[t], closeDF[s].iloc[t + hold]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                fwd.append(p1 / p0 - 1)
        if not fwd:
            continue
        pick_rets.extend(fwd)
        equity *= (1 + float(np.mean(fwd)))
        b0, b1 = benchS.iloc[t], benchS.iloc[t + hold]
        if np.isfinite(b0) and np.isfinite(b1) and b0 > 0:
            bench_rets.append(b1 / b0 - 1)

    n = len(pick_rets)
    if n == 0:
        return {"error": "검증 거래가 생성되지 않았습니다."}
    wins = sum(1 for x in pick_rets if x > 0)
    return {
        "winRate": round(wins / n * 100, 1),
        "avgReturn": round(float(np.mean(pick_rets)) * 100, 2),
        "trades": n,
        "rebalances": len(rebal),
        "stratTotal": round((equity - 1) * 100, 2),
        "benchReturn": round((float(np.prod([1 + b for b in bench_rets])) - 1) * 100, 2) if bench_rets else 0.0,
        "benchName": _BENCH_NAME[market],
        "bestTrade": round(max(pick_rets) * 100, 2),
        "worstTrade": round(min(pick_rets) * 100, 2),
    }


# ===========================================================================
# 오늘의 추천 종목 (추천엔진 + 목표가/손절가/보유기간)
# ===========================================================================
def _today_picks(market: str, hold: int, top: int) -> List[Dict]:
    try:
        reco = recommend.recommend(market, top).get("items", [])
    except Exception:
        reco = []
    out = []
    for r in reco:
        price = r.get("price")
        move = 0.0
        try:
            ysym = providers.yahoo_symbol(r["symbol"], market)
            daily = signals.get_daily(ysym)
            if daily and len(daily.get("close", [])) > 21:
                closes = daily["close"][-21:]
                rets = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]
                if rets:
                    dvol = float(np.std(rets))
                    move = dvol * math.sqrt(hold)
        except Exception:
            pass
        move = max(0.03, min(move, 0.4))  # 3%~40% 범위로 제한
        target = round(price * (1 + 1.5 * move), 2) if price else None
        stop = round(price * (1 - 1.0 * move), 2) if price else None
        out.append({
            "symbol": r.get("symbol"), "market": market, "name": r.get("name"),
            "price": price, "changePct": r.get("changePct"), "score": r.get("score"),
            "tags": r.get("tags", []), "reason": r.get("reason", ""), "rsi": r.get("rsi"),
            "target": target, "stop": stop,
            "targetPct": round(1.5 * move * 100, 1), "stopPct": round(1.0 * move * 100, 1),
            "holdLabel": _HOLD_LABEL.get(hold, f"{hold}거래일"),
        })
    return out
