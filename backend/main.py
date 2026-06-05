"""
FastAPI 서버 — 주식 대시보드
실행:  python -m uvicorn backend.main:app --reload --port 8000
또는   run.bat / run.sh
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# .env 로드 (NAVER_CLIENT_ID/SECRET, KRX_ID/KRX_PW 등)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from backend import backtest, news, providers, recommend, themes  # noqa: E402

app = FastAPI(title="주식 대시보드", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

WATCHLIST_FILE = ROOT / "watchlist.json"
WEB_DIR = ROOT / "web"


# ---------------------------------------------------------------------------
# 백그라운드 예열(warmer): US/KR 등락률·추천·지수를 미리 계산해 캐시 → 탭 즉시 표시
# ---------------------------------------------------------------------------
import threading  # noqa: E402
import time as _time  # noqa: E402


def _warm_loop():
    while True:
        for mk in ("US", "KR"):
            try:
                providers.get_top_gainers(mk, 10)
            except Exception:
                pass
            try:
                recommend.recommend(mk, 10)
            except Exception:
                pass
        try:
            providers.get_indices()
        except Exception:
            pass
        _time.sleep(270)  # 추천 캐시(300s)보다 짧게 갱신해 항상 따뜻하게 유지


@app.on_event("startup")
def _start_warmer():
    threading.Thread(target=_warm_loop, daemon=True).start()


# ---------------------------------------------------------------------------
# 관심종목 저장/로드
# ---------------------------------------------------------------------------
def _load_watchlist() -> List[dict]:
    if WATCHLIST_FILE.exists():
        try:
            return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_watchlist(items: List[dict]):
    WATCHLIST_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class WatchItem(BaseModel):
    symbol: str
    market: str
    name: str = ""


class AlertItem(BaseModel):
    symbol: str
    market: str
    name: str = ""
    target: float
    direction: str = "above"  # above | below


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "naver_news": bool(os.getenv("NAVER_CLIENT_ID")),
        "krx_login": bool(os.getenv("KRX_ID") and os.getenv("KRX_PW")),
    }


@app.get("/api/gainers")
def gainers(market: str = Query("US"), limit: int = 10):
    market = market.upper()
    if market not in ("US", "KR"):
        raise HTTPException(400, "market must be US or KR")
    return {"market": market, "items": providers.get_top_gainers(market, limit)}


@app.get("/api/quote/{symbol}")
def quote(symbol: str, market: str = Query("US")):
    return providers.get_quote(symbol, market.upper())


@app.get("/api/chart/{symbol}")
def chart(symbol: str, market: str = Query("US"), period: str = Query("1d")):
    return providers.get_chart(symbol, market.upper(), period)


@app.get("/api/news")
def market_news(market: str = Query("US"), limit: int = 12):
    return {"market": market.upper(), "items": news.get_market_news(market.upper(), limit)}


@app.get("/api/news/{symbol}")
def stock_news(symbol: str, market: str = Query("US"), name: str = "", limit: int = 10):
    nm = name or providers._name_of(symbol, market.upper())
    return {"symbol": symbol, "items": news.get_stock_news(symbol, market.upper(), nm, limit)}


@app.get("/api/indices")
def indices():
    return providers.get_indices()


@app.get("/api/recommend")
def recommend_stocks(market: str = Query("US"), limit: int = 10):
    market = market.upper()
    if market not in ("US", "KR"):
        raise HTTPException(400, "market must be US or KR")
    return recommend.recommend(market, limit)


@app.get("/api/themes")
def get_themes():
    return themes.get_themes()


@app.get("/api/backtest")
def backtest_run(market: str = Query("US"), months: int = 6, hold: int = 20, top: int = 10):
    return backtest.run_backtest(market.upper(), months, hold, top)


@app.get("/api/search")
def search(q: str = Query(..., min_length=1), market: str = Query("")):
    mk = market.upper() if market else None
    return {"items": providers.search_symbols(q, mk)}


@app.get("/api/watchlist")
def get_watchlist():
    return {"items": _load_watchlist()}


@app.post("/api/watchlist")
def add_watchlist(item: WatchItem):
    items = _load_watchlist()
    key = (item.symbol, item.market.upper())
    if not any((x["symbol"], x["market"].upper()) == key for x in items):
        nm = item.name or providers._name_of(item.symbol, item.market.upper())
        items.append({"symbol": item.symbol, "market": item.market.upper(), "name": nm})
        _save_watchlist(items)
    return {"items": items}


@app.delete("/api/watchlist")
def del_watchlist(symbol: str, market: str):
    items = _load_watchlist()
    items = [x for x in items if not (x["symbol"] == symbol and x["market"].upper() == market.upper())]
    _save_watchlist(items)
    return {"items": items}


# ---------------------------------------------------------------------------
# 정적 프론트엔드
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
