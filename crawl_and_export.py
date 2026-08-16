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

GRADES = [{"id": 1, "name": "大一"}, {"id": 2, "name": "大二"},
          {"id": 3, "name": "大三"}, {"id": 4, "name": "大四"}]
CATEGORIES = ["全部", "创新创业", "学科竞赛", "学术科研", "文体活动", "就业实习", "其他"]


def export():
    os.makedirs("data", exist_ok=True)
    items = storage.get_items()
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
    # 抓取全部公众号
    result = crawler.refresh()
    print("抓取结果:", result)
    export()


if __name__ == "__main__":
    main()
