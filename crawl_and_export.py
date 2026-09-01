# -*- coding: utf-8 -*-
"""一键抓取 + 导出静态数据（供 GitHub Actions 定时任务 + GitHub Pages 使用）

用法：
    python crawl_and_export.py                 # 抓取并导出 data/data.json
    python crawl_and_export.py <文章链接>       # 先手动加一篇文章，再抓取导出
"""
import json
import os
import sys
import time

os.environ.setdefault("DATA_FILE", "_crawl_state.json")

import config   # noqa: E402
import crawler  # noqa: E402
import storage  # noqa: E402
import seed     # noqa: E402

GRADES = [{"id": 1, "name": "大一"}, {"id": 2, "name": "大二"},
          {"id": 3, "name": "大三"}, {"id": 4, "name": "大四"}]
CATEGORIES = ["全部", "创新创业", "学科竞赛", "学术科研", "文体活动", "就业实习", "其他"]

# 兜底最小条数：抓取内容不足时用种子补足，保证 App 有内容可看
MIN_ITEMS = 12


def export():
    os.makedirs("data", exist_ok=True)
    real_items = crawler.prune(storage.get_items())   # 抓取内容：名单内+近期+真实链接
    items = list(real_items)

    # 兜底：抓取内容过少时补入种子（放在抓取内容之后，不覆盖真实数据）
    if len(items) < MIN_ITEMS:
        existing_ids = set(i.get("id") for i in items)
        used_titles = set(i.get("title", "") for i in items)
        for s in seed.SEED_ITEMS:
            if len(items) >= MIN_ITEMS:
                break
            if s["title"] in used_titles:
                continue
            s2 = dict(s)
            s2["id"] = "seed_" + str(abs(hash(s["title"])) % 10 ** 8)
            s2["url"] = ""          # 种子是介绍占位，无原文链接
            s2["source"] = "竞赛介绍"
            s2["date"] = ""
            s2["colleges"] = s.get("colleges", [])
            items.append(s2)
            used_titles.add(s["title"])

    payload = {
        "items": items,
        "updated_at": int(time.time()),
        "updated_text": time.strftime("%Y-%m-%d %H:%M"),
        "source_status": storage.get_source_status(),
    }
    with open("data/data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    meta = {
        "grades": GRADES,
        "colleges": [c["name"] for c in config.COLLEGES],
        "categories": CATEGORIES,
        "item_count": len(items),
    }
    with open("data/meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    print("已导出 %d 条竞赛信息 -> data/data.json" % len(items))


def main():
    # 手动加链接（可选第一个参数）
    if len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        r = crawler.add_manual_link(sys.argv[1], source="手动添加")
        print("手动添加:", r.get("ok"), r.get("error", ""))
    # ① 主数据源：天大官网新闻（稳定、最新）
    added_web = crawler.crawl_tju_news()
    print("官网新闻新增:", added_web)
    # ② 辅助：搜狗公众号（能抓多少算多少，被拦会跳过）
    result = crawler.refresh()
    print("公众号抓取:", result.get("status"), "新增", result.get("added", 0))
    export()


if __name__ == "__main__":
    main()
