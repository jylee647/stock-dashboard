"""
백테스트(재설계 v2) — '지금 사면 좋은 종목' 추천 + 그 방식의 과거 적중률 검증.
- 상단: 이 선정 방식이 과거 N개월간 적중률 X%, 평균수익 +Y% (지수 대비)
- 본문: 오늘의 추천 종목 리스트 = 추천엔진 결과 + 목표가/손절가/보유기간

[v2 개선 — 적중률 정직하게 +로 끌어올리기]
 1) 시장 국면 필터: 지수(^GSPC/^KS11)가 60일선 위일 때만 신규 매수, 하락장은 현금 보유(스킵).
 2) 추세 필터: price>MA20 and MA20>MA60(상승추세) + 20일 모멘텀 양호만 선택, 과열(>MA20 +25%) 제외.
 3) 목표가/손절가 청산 시뮬: 보유기간 내 고가가 목표가 도달=익절, 저가가 손절가 도달=손절,
    둘 다 아니면 종가수익률. (yf.download에서 High/Low 추출.)
 4) 목표/손절은 변동성(일간표준편차×√기간) 기반. 보상:위험 = 2.0 : 1.6 으로 승률↑·기대수익 양수 균형.
 5) winRate/avgReturn 은 부풀리지 않고 실측값 그대로 표기.

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

# 목표/손절 배수 (변동성 밴드 기준) — 보상:위험 비대칭으로 기대수익 양수 유도
_TP_MULT = 2.0   # 목표가 = entry * (1 + 2.0 * band)
_SL_MULT = 1.6   # 손절가 = entry * (1 - 1.6 * band)


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
    ck = f"bt3:{market}:{months}:{hold}:{top}"
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
            "‘과거 적중률’은 동일한 규칙(시장국면 필터 + 상승추세·모멘텀 선별 + 목표가/손절가 청산)으로 "
            "과거를 재현해 검증한 값입니다. 하락장에서는 신규 매수를 건너뛰므로(현금 보유) 지수와 단순 비교가 "
            "어려울 수 있고, 뉴스 등 일부 요소는 과거 재현이 불가해 제외했습니다. 목표가·손절가는 최근 변동성 "
            "기반 참고치입니다. 투자 자문이 아니며 미래 수익을 보장하지 않습니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out


# ===========================================================================
# 과거 적중률 검증 (규칙 기반 재현)
# ===========================================================================
def _validate(market: str, months: int, hold: int, top: int) -> Dict:
    uni = _universe(market)
    syms = list(uni.keys())
    bench = _BENCH[market]
    # MA60 + 지수 60일선 워밍업(약 3개월) + 보유기간 여유를 위해 버퍼 확보
    period = f"{months + 5}mo"
    try:
        raw = yf.download(syms + [bench], period=period, interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return {"error": f"데이터 다운로드 실패: {str(e)[:100]}"}

    # --- OHLCV 추출 (Close/Volume + High/Low) ---
    close, high, low, vol = {}, {}, {}, {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            cl = sub["Close"].dropna()
            if len(cl) > 65:
                close[s] = cl
                high[s] = sub["High"].reindex(cl.index)
                low[s] = sub["Low"].reindex(cl.index)
                vol[s] = sub["Volume"].reindex(cl.index)
        except Exception:
            continue
    if len(close) < 5:
        return {"error": "유효 종목 데이터 부족(네트워크 확인)"}

    closeDF = pd.DataFrame(close).sort_index()
    highDF = pd.DataFrame(high).reindex(closeDF.index)
    lowDF = pd.DataFrame(low).reindex(closeDF.index)
    try:
        benchS = (raw[bench]["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw["Close"]).reindex(closeDF.index).ffill()
    except Exception:
        benchS = closeDF.mean(axis=1)
    benchMA60 = benchS.rolling(60).mean()

    # --- 지표 ---
    ma20 = closeDF.rolling(20).mean()
    ma60 = closeDF.rolling(60).mean()
    mom20 = closeDF.pct_change(20)                       # 20일 모멘텀
    dstd = closeDF.pct_change().rolling(20).std()        # 일간 변동성(20일)

    dates = closeDF.index
    warm = 60
    if len(dates) <= warm + hold + 1:
        return {"error": "기간이 짧습니다. 개월 수를 늘려보세요."}

    rebal = list(range(warm, len(dates) - hold, hold))
    pick_rets: List[float] = []
    bench_rets: List[float] = []
    equity = 1.0
    skipped_regime = 0       # 하락장으로 스킵한 리밸런싱 수
    skipped_nopick = 0       # 조건 충족 종목이 없어 스킵한 수
    exit_tp = 0              # 목표가 청산 건수
    exit_sl = 0              # 손절가 청산 건수
    exit_time = 0           # 만기 종가 청산 건수

    for t in rebal:
        # (1) 시장 국면 필터: 지수가 60일선 위일 때만 신규 매수
        bpx, bma = benchS.iloc[t], benchMA60.iloc[t]
        if np.isfinite(bpx) and np.isfinite(bma) and bpx < bma:
            skipped_regime += 1
            continue

        # (2) 추세 + 모멘텀 후보 선별
        cand = {}
        for s in closeDF.columns:
            price = closeDF[s].iloc[t]
            m20, m60 = ma20[s].iloc[t], ma60[s].iloc[t]
            mo = mom20[s].iloc[t]
            if not (np.isfinite(price) and np.isfinite(m20) and np.isfinite(m60) and m60 > 0):
                continue
            a20 = price / m20 - 1
            # 상승추세 정렬: price > MA20 > MA60
            if not (price > m20 and m20 > m60):
                continue
            # 모멘텀 양호(20일 수익률 +)
            if not (np.isfinite(mo) and mo > 0):
                continue
            # 과열 제외: MA20 대비 +25% 이상은 매수 안 함
            if a20 > 0.25:
                continue
            # 랭킹 점수: 모멘텀 + 추세강도, 과열(15%↑) 페널티
            trend_str = m20 / m60 - 1
            cand[s] = (0.6 * min(mo, 0.5)
                       + 0.4 * min(max(trend_str, 0.0), 0.3)
                       - 0.3 * max(0.0, a20 - 0.15))
        if not cand:
            skipped_nopick += 1
            continue
        picks = sorted(cand, key=cand.get, reverse=True)[:top]

        # (3) 목표가/손절가 청산 시뮬 (High/Low 사용)
        fwd = []
        for s in picks:
            entry = closeDF[s].iloc[t]
            v = dstd[s].iloc[t]
            if not (np.isfinite(entry) and entry > 0):
                continue
            v = v if (np.isfinite(v) and v > 0) else 0.02
            # (4) 변동성(일간표준편차 × √보유기간) 기반 밴드, 3%~30%로 제한
            band = max(0.03, min(v * math.sqrt(hold), 0.30))
            target = entry * (1 + _TP_MULT * band)
            stop = entry * (1 - _SL_MULT * band)
            ret = None
            for k in range(1, hold + 1):
                hi = highDF[s].iloc[t + k]
                lo = lowDF[s].iloc[t + k]
                # 보수적: 같은 날 손절·익절 동시 도달 시 손절을 먼저 적용
                if np.isfinite(lo) and lo <= stop:
                    ret = stop / entry - 1
                    exit_sl += 1
                    break
                if np.isfinite(hi) and hi >= target:
                    ret = target / entry - 1
                    exit_tp += 1
                    break
            if ret is None:
                p1 = closeDF[s].iloc[t + hold]
                if not (np.isfinite(p1) and p1 > 0):
                    continue
                ret = p1 / entry - 1
                exit_time += 1
            fwd.append(ret)
        if not fwd:
            skipped_nopick += 1
            continue

        pick_rets.extend(fwd)
        equity *= (1 + float(np.mean(fwd)))
        # 벤치마크는 '실제로 매수한' 구간만 비교(현금구간 제외 → 공정 비교)
        b0, b1 = benchS.iloc[t], benchS.iloc[t + hold]
        if np.isfinite(b0) and np.isfinite(b1) and b0 > 0:
            bench_rets.append(b1 / b0 - 1)

    n = len(pick_rets)
    if n == 0:
        return {"error": "조건을 만족하는 매수 시점이 없었습니다(추세 약함/하락장). 기간·종목을 조정해 보세요."}
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
        # 참고용(프론트 비표시 가능) — 규칙 동작 투명성
        "tradedRebalances": len(bench_rets),
        "skippedDownMarket": skipped_regime,
        "skippedNoPick": skipped_nopick,
        "exitTakeProfit": exit_tp,
        "exitStopLoss": exit_sl,
        "exitTimeClose": exit_time,
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
        move = max(0.03, min(move, 0.30))  # 변동성 밴드: 3%~30% (검증 로직과 동일)
        # 검증 로직과 동일한 보상:위험 비대칭 적용
        target = round(price * (1 + _TP_MULT * move), 2) if price else None
        stop = round(price * (1 - _SL_MULT * move), 2) if price else None
        out.append({
            "symbol": r.get("symbol"), "market": market, "name": r.get("name"),
            "price": price, "changePct": r.get("changePct"), "score": r.get("score"),
            "tags": r.get("tags", []), "reason": r.get("reason", ""), "rsi": r.get("rsi"),
            "target": target, "stop": stop,
            "targetPct": round(_TP_MULT * move * 100, 1), "stopPct": round(_SL_MULT * move * 100, 1),
            "holdLabel": _HOLD_LABEL.get(hold, f"{hold}거래일"),
        })
    return out
