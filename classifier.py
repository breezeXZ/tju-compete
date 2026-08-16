# -*- coding: utf-8 -*-
"""分类器：公众号文章 → 结构化竞赛条目（竞赛名/类别/年级/学院）"""
import hashlib
import re
import config


def _compounds():
    """复合词集合：竞赛名/竞赛别名/类别关键词，用于避免把子串误当学院"""
    comps = set()
    for name, meta in config.COMPETITIONS.items():
        comps.add(name)
        if meta.get("alias_of"):
            comps.add(name)
        if meta.get("alias_of"):
            comps.add(meta["alias_of"])
    for kws in config.CATEGORY_KEYWORDS.values():
        comps.update(kws)
    return comps


_COMPOUNDS = _compounds()


def find_colleges(text):
    """返回命中的学院规范名列表（含复合词守卫，避免「数学建模」误判为「数学学院」）"""
    found = []
    for c in config.COLLEGES:
        names = [c["name"]] + c["aliases"]
        hit = None
        for n in names:
            if n in text:
                # 若 n 是文本中某个更长复合词的一部分（且该复合词不是 n 本身）→ 跳过
                if any(n in comp and comp in text and comp != n for comp in _COMPOUNDS):
                    continue
                hit = c["name"]
                break
        if hit and hit not in found:
            found.append(hit)
    # 专业名 → 学院（辅助识别，如「软件工程专业」）
    for major, college in config.MAJOR_TO_COLLEGE.items():
        if major in text and college not in found:
            found.append(college)
    return found


def find_grades(text):
    """返回 (年级集合, 是否显式命中年级关键词)"""
    grades = set()
    hit = False
    for kw, gs in config.GRADE_KEYWORDS:
        if kw in text:
            grades.update(gs)
            hit = True
    return sorted(grades), hit


def find_competitions(text):
    """返回竞赛规范名列表（含别名归并）"""
    comps = []
    # 主名命中
    for name, meta in config.COMPETITIONS.items():
        if meta.get("alias_of"):
            continue
        if name in text:
            if name not in comps:
                comps.append(name)
    # 别名归并到主名
    for alias, meta in config.COMPETITIONS.items():
        if meta.get("alias_of") and alias in text:
            main = meta["alias_of"]
            if main not in comps:
                comps.append(main)
    return comps


def category_of(text, comps):
    """类别：优先用竞赛自带类别，其次关键词"""
    if comps:
        return config.COMPETITIONS[comps[0]]["category"]
    for cat, kws in config.CATEGORY_KEYWORDS.items():
        if any(k in text for k in kws):
            return cat
    return "学科竞赛"


def summary_of(text, n=70):
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def classify(title, content, source, url, date=""):
    """文章 → 条目 dict"""
    text = (title or "") + "\n" + (content or "")
    comps = find_competitions(text)
    grades, grade_hit = find_grades(text)
    colleges = find_colleges(text)
    category = category_of(text, comps)

    if not grade_hit:
        # 无显式年级 → 用竞赛默认年级；再无 → 全年级
        if comps:
            gset = set()
            for c in comps:
                gset.update(config.COMPETITIONS.get(c, {}).get("grades", [1, 2, 3, 4]))
            grades = sorted(gset) or [1, 2, 3, 4]
        else:
            grades = [1, 2, 3, 4]

    item = {
        "id": hashlib.md5((url or "").encode("utf-8")).hexdigest()[:12],
        "title": (title or "").strip(),
        "summary": summary_of(content or title),
        "source": source,
        "url": url,
        "date": date,
        "category": category,
        "competitions": comps,
        "grades": grades,
        "colleges": colleges,
        "content_len": len(content or ""),
    }
    return item
