# -*- coding: utf-8 -*-
"""后端自测：分类器单测 + 存储/API 逻辑 + 手动链接抓取"""
import os
import sys

os.environ.setdefault("DATA_FILE", "test_data.json")

import config
import classifier
import storage
import crawler

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]", name)
    else:
        FAIL += 1
        print("  [FAIL]", name, "->", detail)


def main():
    print("== 分类器单测 ==")
    # 1. 大二数学建模
    it = classifier.classify(
        "2026年全国大学生数学建模竞赛报名通知",
        "面向全校大二、大三学生开放报名，请各学院积极组织参赛。",
        "测试源", "http://x/1", "2026-08-01")
    check("命中数学建模", "全国大学生数学建模竞赛" in it["competitions"], it["competitions"])
    check("类别=学科竞赛", it["category"] == "学科竞赛", it["category"])
    check("年级含2,3", 2 in it["grades"] and 3 in it["grades"], it["grades"])
    check("学院=全校", it["colleges"] == [], it["colleges"])

    # 2. 软件学院 + 大一新生
    it2 = classifier.classify(
        "软件学院新生程序设计竞赛",
        "面向大一新生，欢迎软件学院和智算学部同学参加，蓝桥杯选拔。",
        "测试源", "http://x/2", "2026-08-02")
    check("命中蓝桥杯/程序设计", "蓝桥杯" in it2["competitions"], it2["competitions"])
    check("学院=智算学部(软件学院归入)", "智能与计算学部" in it2["colleges"], it2["colleges"])
    check("年级=大一", it2["grades"] == [1], it2["grades"])

    # 3. 挑战杯 + 全体
    it3 = classifier.classify(
        "挑战杯校内选拔赛开始报名",
        "面向全体本科生，创新创业类竞赛，优秀项目将推荐参加市赛。",
        "测试源", "http://x/3", "2026-08-03")
    check("命中挑战杯", "挑战杯" in it3["competitions"], it3["competitions"])
    check("类别=创新创业", it3["category"] == "创新创业", it3["category"])
    check("全年级", it3["grades"] == [1, 2, 3, 4], it3["grades"])

    # 4. 无关键词 → 默认学科竞赛/全年级
    it4 = classifier.classify("本周校园新闻", "关于食堂开放时间的通知。", "测试源", "http://x/4", "")
    check("无竞赛→空", it4["competitions"] == [], it4["competitions"])
    check("默认全年级", it4["grades"] == [1, 2, 3, 4], it4["grades"])

    # 5. 就业（大四）
    it5 = classifier.classify("秋季校园招聘宣讲会",
                              "面向大四毕业生，多家企业现场招聘，含实习岗位。",
                              "测试源", "http://x/5", "")
    check("类别=就业实习", it5["category"] == "就业实习", it5["category"])
    check("年级含4", 4 in it5["grades"], it5["grades"])

    print("== 存储 & 去重 ==")
    storage.upsert_item(it)
    storage.upsert_item(it2)
    storage.upsert_item(it3)
    n = storage.upsert_item(it)  # 重复
    check("重复不新增", n is False, n)
    check("items=3", len(storage.get_items()) == 3, len(storage.get_items()))

    print("== API feed 过滤排序 ==")
    from api import _score
    # 智算学部学生：it2 学院命中 → 分数最高
    s1 = _score(it, 2, "智能与计算学部")
    s2 = _score(it2, 1, "智能与计算学部")
    check("学院优先排序", s2 > s1, (s2, s1))
    # 大二看：it(建模) 命中年级，it2(软件) 未命中大二
    sa = _score(it, 2, "")
    sb = _score(it2, 2, "")
    check("年级匹配加分", sa > sb, (sa, sb))

    # 手动链接抓取（真实微信文章，网络可用则通过）
    print("== 手动链接抓取（真实 mp.weixin）==")
    real = os.environ.get("REAL_URL", "")
    if real:
        try:
            r = crawler.add_manual_link(real)
            check("手动链接入库(真实链接)", r.get("ok") is True, r)
        except Exception as e:
            print("  · 真实链接测试异常:", e)
    else:
        # 无效链接应优雅降级：返回 ok=False + 错误说明，不崩溃
        r = crawler.add_manual_link("https://mp.weixin.qq.com/s/INVALID_PLACEHOLDER_XYZ")
        check("无效链接优雅降级", r.get("ok") is False and "error" in r, r)

    print("\n结果: %d 通过, %d 失败" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
