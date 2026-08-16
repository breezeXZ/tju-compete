# -*- coding: utf-8 -*-
"""存储层：JSON 文件持久化（条目 / 已见URL去重 / 节流时间 / 来源状态）"""
import json
import os
import threading
import config

_lock = threading.Lock()
_data = None


def _default():
    return {"items": [], "seen_urls": {}, "last_refresh": 0, "source_status": {}}


def load():
    global _data
    if _data is None:
        if os.path.exists(config.DATA_FILE):
            try:
                with open(config.DATA_FILE, encoding="utf-8") as f:
                    _data = json.load(f)
            except Exception:
                _data = _default()
        else:
            _data = _default()
        d = _default()
        for k in d:
            _data.setdefault(k, d[k])
    return _data


def save():
    with _lock:
        if _data is not None:
            tmp = config.DATA_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_data, f, ensure_ascii=False)
            os.replace(tmp, config.DATA_FILE)


def seen(url):
    return load()["seen_urls"].get(url)


def mark_seen(url):
    d = load()
    d["seen_urls"][url] = True
    keys = list(d["seen_urls"].keys())
    if len(keys) > 4000:
        for k in keys[:-2500]:
            d["seen_urls"].pop(k, None)
    save()


def upsert_item(item):
    """新条目插入到最前；已存在则更新。返回是否新增"""
    d = load()
    for i, it in enumerate(d["items"]):
        if it["id"] == item["id"]:
            d["items"][i] = item
            save()
            return False
    d["items"].insert(0, item)
    if len(d["items"]) > 2000:
        d["items"] = d["items"][:2000]
    save()
    return True


def get_items():
    return load()["items"]


def set_last_refresh(ts):
    d = load()
    d["last_refresh"] = ts
    save()


def get_last_refresh():
    return load()["last_refresh"]


def set_source_status(name, status):
    d = load()
    d["source_status"][name] = status
    save()


def get_source_status():
    return load()["source_status"]
