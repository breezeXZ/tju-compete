# -*- coding: utf-8 -*-
"""天大赛事通 · FastAPI 服务"""
import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import config
import storage
import crawler

app = FastAPI(title="天大赛事通 API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GRADES = [{"id": 1, "name": "大一"}, {"id": 2, "name": "大二"},
          {"id": 3, "name": "大三"}, {"id": 4, "name": "大四"}]
CATEGORIES = ["全部", "创新创业", "学科竞赛", "学术科研", "文体活动", "就业实习"]


@app.get("/health")
def health():
    return {"ok": True, "ts": int(time.time())}


@app.get("/api/meta")
def meta():
    return {
        "grades": GRADES,
        "colleges": [c["name"] for c in config.COLLEGES],
        "categories": CATEGORIES,
        "source_status": storage.get_source_status(),
        "last_refresh": storage.get_last_refresh(),
        "item_count": len(storage.get_items()),
    }


@app.post("/api/refresh")
def do_refresh():
    """触发抓取（节流 ≥ REFRESH_INTERVAL_SECONDS）"""
    return crawler.refresh()


def _score(item, grade, college):
    s = 0
    if college and college in item.get("colleges", []):
        s += 100
    if grade in item.get("grades", []):
        s += 10
    return s


@app.get("/api/feed")
def feed(grade: int = 0, college: str = "", type: str = "all", q: str = ""):
    items = storage.get_items()
    filtered = []
    for it in items:
        if grade and grade not in it.get("grades", []):
            continue
        if type and type != "all" and it.get("category") != type:
            continue
        if q:
            hay = (it.get("title", "") + " " + it.get("summary", "")
                   + " " + " ".join(it.get("competitions", [])))
            if q not in hay:
                continue
        filtered.append(it)

    # 排序：学院优先 + 年级匹配 分数高的在前；原列表已按时间新→旧
    filtered.sort(key=lambda it: _score(it, grade, college), reverse=True)
    return {
        "items": filtered,
        "total": len(filtered),
        "grade": grade,
        "college": college,
        "updated_at": int(time.time()),
    }


@app.post("/api/admin/link")
def admin_link(link: dict, x_admin_token: str = Header("", alias="X-Admin-Token")):
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="管理令牌错误")
    url = (link or {}).get("url", "").strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="需要 http(s) 链接")
    return crawler.add_manual_link(url)
