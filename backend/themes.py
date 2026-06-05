"""
테마 전망 — GAS(Google Apps Script)가 생성한 themes.json을 가져온다.
- THEMES_URL(.env)이 있으면 그 URL(GAS 웹앱 또는 Drive)에서 fetch (서버측이라 CORS 무관)
- 없거나 실패하면 로컬 시드(web/themes_seed.json) 사용
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "web" / "themes_seed.json"

_CACHE = {"t": 0, "data": None}


def get_themes() -> dict:
    # 10분 캐시
    if _CACHE["data"] and time.time() - _CACHE["t"] < 600:
        return _CACHE["data"]

    url = os.getenv("THEMES_URL", "").strip()
    data = None
    if url:
        try:
            r = requests.get(url, timeout=12)
            r.raise_for_status()
            data = r.json()
            data["_source"] = "live"
        except Exception as e:
            data = None

    if data is None:
        try:
            data = json.loads(SEED.read_text(encoding="utf-8"))
            data["_source"] = "seed"
        except Exception:
            data = {"short": {"horizons": []}, "long": {"horizons": []},
                    "_source": "none", "generatedAt": None}

    _CACHE["t"] = time.time()
    _CACHE["data"] = data
    return data
