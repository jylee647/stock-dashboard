"""
관련주 영향·방향 엔진 (테마 단위)
- 같은 테마(#AI, #반도체 ...) 안에서 '리더'가 움직이면 '팔로워'가 며칠 뒤 어느 방향으로
  따라가는지를 시차 상관(lead-lag cross-correlation)으로 계산한다.
- 출력: 테마별 리더 + 팔로워(최적 시차·상관·방향) + 현재 방향 신호.
※ 종목명 키워드로 테마를 부여하므로 한국 시장에서 의미가 크다(영문 티커뿐인 미국은 희박).
※ 가격 데이터 기반의 통계적 관계이며, 인과나 미래 수익을 보장하지 않는다.
"""
from __future__ import annotations

import time
from typing import Dict, List

import numpy as np
import pandas as pd
import yfinance as yf

from backend import providers, recommend

_CACHE: Dict[str, tuple] = {}
_BENCH = {"KR": "^KS11", "US": "^GSPC"}
_MAX_LAG = 5          # 며칠 뒤까지 따라가는지 (1~5거래일)
_MIN_MEMBERS = 3      # 테마 최소 구성 종목
_MIN_ABS_CORR = 0.2   # 의미 있는 상관 하한


def _universe(market: str) -> Dict[str, str]:
    if market == "US":
        return {s: s for s in providers.US_FALLBACK}
    return {f"{code}{suf}": name for code, (name, suf) in providers.KR_FALLBACK.items()}


def _tags_for(name: str) -> List[str]:
    """종목명 키워드로 테마 태그 부여 (recommend.THEME_TAGS 재사용)."""
    blob = (name or "").lower()
    out = []
    for tag, kws in recommend.THEME_TAGS:
        if any(kw.strip() and kw.strip() in blob for kw in kws):
            out.append(tag)
    return out


def _lead_lag(leader: pd.Series, follower: pd.Series) -> tuple:
    """leader 수익률이 follower 수익률을 며칠 선행하는지.
    반환: (best_lag, corr) — corr 부호가 +면 동행, -면 역행."""
    best_lag, best_corr = 0, 0.0
    for lag in range(1, _MAX_LAG + 1):
        a = leader.iloc[:-lag]
        b = follower.iloc[lag:]
        n = min(len(a), len(b))
        if n < 20:
            continue
        a = a.iloc[-n:].values
        b = b.iloc[-n:].values
        if np.std(a) == 0 or np.std(b) == 0:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if np.isfinite(c) and abs(c) > abs(best_corr):
            best_lag, best_corr = lag, c
    return best_lag, best_corr


def compute(market: str) -> Dict:
    market = market.upper()
    ck = f"infl:{market}"
    c = _CACHE.get(ck)
    if c and time.time() - c[0] < 900:
        return c[1]

    uni = _universe(market)
    syms = list(uni.keys())
    try:
        raw = yf.download(syms, period="8mo", interval="1d",
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception as e:
        return {"error": f"데이터 다운로드 실패: {str(e)[:100]}"}

    # 종가 수집
    close = {}
    for s in syms:
        try:
            sub = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
            cl = sub["Close"].dropna()
            if len(cl) > 80:
                close[s] = cl
        except Exception:
            continue
    if len(close) < _MIN_MEMBERS:
        return {"error": "유효 종목 데이터 부족(네트워크 확인)"}

    closeDF = pd.DataFrame(close).sort_index()
    retDF = closeDF.pct_change()
    ret60 = closeDF.pct_change(60)
    ret5 = closeDF.pct_change(5)

    # 테마 → 구성 종목
    theme_members: Dict[str, List[str]] = {}
    for s in close:
        for tag in _tags_for(uni.get(s, s)):
            theme_members.setdefault(tag, []).append(s)

    themes_out = []
    for theme, members in theme_members.items():
        members = [m for m in members if m in retDF.columns]
        if len(members) < _MIN_MEMBERS:
            continue
        # 리더 = 최근 60일 모멘텀 1위
        scored = [(m, ret60[m].iloc[-1]) for m in members if np.isfinite(ret60[m].iloc[-1])]
        if len(scored) < _MIN_MEMBERS:
            continue
        scored.sort(key=lambda x: x[1], reverse=True)
        leader_sym = scored[0][0]
        leader_ret = retDF[leader_sym].dropna()
        leader_move5 = float(ret5[leader_sym].iloc[-1]) if np.isfinite(ret5[leader_sym].iloc[-1]) else 0.0

        followers = []
        for f, _ in scored[1:]:
            fr = retDF[f].dropna()
            # 공통 구간 정렬
            idx = leader_ret.index.intersection(fr.index)
            if len(idx) < 40:
                continue
            lag, corr = _lead_lag(leader_ret.reindex(idx), fr.reindex(idx))
            if lag == 0 or abs(corr) < _MIN_ABS_CORR:
                continue
            direction = "동행" if corr > 0 else "역행"
            # 현재 방향 신호: 리더 최근 5일 방향 × 상관 부호
            bias = leader_move5 * corr
            signal = "상승 기대" if bias > 0 else ("하락 주의" if bias < 0 else "중립")
            followers.append({
                "symbol": f, "name": uni.get(f, f),
                "lag": lag, "corr": round(corr, 2),
                "direction": direction, "signal": signal,
            })
        if not followers:
            continue
        followers.sort(key=lambda x: abs(x["corr"]), reverse=True)
        themes_out.append({
            "theme": theme,
            "members": len(members),
            "leader": {"symbol": leader_sym, "name": uni.get(leader_sym, leader_sym),
                       "ret60Pct": round(scored[0][1] * 100, 1),
                       "move5Pct": round(leader_move5 * 100, 2)},
            "followers": followers[:8],
        })

    themes_out.sort(key=lambda t: t["members"], reverse=True)
    out = {
        "market": market,
        "themes": themes_out,
        "asOf": str(closeDF.index[-1].date()) if len(closeDF) else None,
        "disclaimer": (
            "같은 테마 안에서 리더(최근 60일 모멘텀 1위)가 움직이면 팔로워가 며칠 뒤 어느 방향으로 "
            "따라갔는지를 과거 8개월 시차 상관으로 계산한 값입니다. 통계적 관계이며 인과·미래수익을 "
            "보장하지 않습니다. 투자 자문이 아닙니다."
        ),
    }
    _CACHE[ck] = (time.time(), out)
    return out
