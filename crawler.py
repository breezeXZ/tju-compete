# -*- coding: utf-8 -*-
"""多源爬虫：RSS/RssHub 源 + 搜狗微信搜索 + 手动链接兜底 + 文章正文抓取"""
import re
import time
import logging

import requests

import config
import storage
import classifier

log = logging.getLogger("crawler")


# ============ 通用 HTTP ============
def _get(url, headers=None, timeout=None):
    return requests.get(
        url,
        headers=headers or config.SOGOU_HEADERS,
        timeout=timeout or config.HTTP_TIMEOUT,
        verify=True,
    )


# ============ 搜狗微信搜索 ============
_SOGOU_SESSION = None


def _sogou_session():
    global _SOGOU_SESSION
    if _SOGOU_SESSION is None:
        s = requests.Session()
        try:
            s.get("https://weixin.sogou.com/", headers=config.SOGOU_HEADERS, timeout=config.HTTP_TIMEOUT)
        except Exception as e:
            log.warning("搜狗 cookie 初始化失败: %s", e)
        _SOGOU_SESSION = s
    return _SOGOU_SESSION


def _is_captcha(text):
    return "请输入验证码" in text or "antispider" in text or "访问过于频繁" in text


def _resolve_sogou_link(session, href, referer):
    """跟随搜狗 /link 跳转：真实文章 URL 被拆成碎片拼在 JS 里，正则拼接还原"""
    if "mp.weixin.qq.com" in href:
        return href
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = "https://weixin.sogou.com" + href
    headers = dict(config.SOGOU_HEADERS)
    headers["Referer"] = referer
    resp = session.get(href, headers=headers, timeout=config.HTTP_TIMEOUT)
    frags = re.findall(r"url\s*\+=\s*'([^']*)'", resp.text)
    if not frags:
        # 兜底：直接找 mp.weixin 链接
        m = re.search(r"https?://mp\.weixin\.qq\.com/s[^\"'\\s>]*", resp.text)
        return m.group(0) if m else href
    real = "".join(frags).replace("@", "")
    return real


def sogou_search_account(name):
    """按公众号名搜索，返回 [{url, title, date}]；被拦截返回 None；失败返回 []"""
    s = _sogou_session()
    from urllib.parse import quote
    url = "https://weixin.sogou.com/weixin?type=2&query=" + quote(name) + "&ie=utf8"
    resp = s.get(url, headers=config.SOGOU_HEADERS, timeout=config.HTTP_TIMEOUT)
    html = resp.text

    if _is_captcha(html):
        log.warning("搜狗对公众号 %s 触发了验证码", name)
        return None  # None = 被拦截

    results = _parse_sogou(html)
    # 解析真实文章链接
    for r in results:
        try:
            r["url"] = _resolve_sogou_link(s, r["link"], url)
        except Exception as e:
            log.warning("解析跳转失败 %s: %s", r.get("link"), e)
    # 限速
    time.sleep(config.SOGOU_DELAY_SECONDS)
    return results


def _parse_sogou(html):
    """解析搜狗微信搜索列表页。结构：ul.news-list > li > .txt-box(h3 a) + .s2(日期)"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("ul.news-list li"):
        h3 = li.select_one(".txt-box h3 a")
        if not h3:
            continue
        href = h3.get("href", "")
        title = h3.get_text(" ", strip=True)
        date = ""
        s2 = li.select_one(".s2") or li.select_one("span[class*=time]")
        if s2:
            m = re.search(r"\d{4}-\d{2}-\d{2}", s2.get_text())
            if m:
                date = m.group(0)
        if href and title:
            out.append({"url": "", "link": href, "title": title, "date": date})
    # 去重（按 link）
    seen = set()
    uniq = []
    for r in out:
        if r["link"] not in seen:
            seen.add(r["link"])
            uniq.append(r)
    return uniq


# ============ RSS 源 ============
def fetch_rss(account):
    try:
        import feedparser
    except ImportError:
        log.warning("缺少 feedparser，无法解析 RSS")
        return []
    try:
        feed = feedparser.parse(account["rss"])
        out = []
        for e in feed.entries[:10]:
            out.append({
                "url": e.get("link", ""),
                "title": e.get("title", "").strip(),
                "date": e.get("published", e.get("updated", "")),
            })
        return out
    except Exception as e:
        log.warning("RSS 抓取失败 %s: %s", account["rss"], e)
        return []


# ============ 文章正文抓取 ============
def fetch_article(url):
    """返回 (title, content_text, date)，失败抛异常"""
    resp = _get(url)
    if resp.status_code != 200:
        raise RuntimeError("HTTP %s" % resp.status_code)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    t_el = (soup.select_one("#activity-name")
            or soup.select_one("h1.rich_media_title")
            or soup.select_one("h1"))
    c_el = soup.select_one("#js_content") or soup.select_one("div.rich_media_content")
    title = t_el.get_text(" ", strip=True) if t_el else ""
    content = c_el.get_text(" ", strip=True) if c_el else ""
    date = ""
    p_el = soup.select_one("#publish_time") or soup.select_one("em#publish_time")
    if p_el:
        m = re.search(r"\d{4}-\d{2}-\d{2}", p_el.get_text())
        if m:
            date = m.group(0)
    return title, content, date


# ============ 主流程 ============
def _process_new(account_name, article):
    """对一篇新文章：抓正文 → 分类 → 入库。返回是否新增"""
    url = article["url"]
    if not url or storage.seen(url):
        return False
    try:
        title, content, date = fetch_article(url)
        if not title and not content:
            title = article.get("title", "")
        date = date or article.get("date", "")
        item = classifier.classify(title, content, account_name, url, date)
        storage.mark_seen(url)
        return storage.upsert_item(item)
    except Exception as e:
        log.warning("抓取文章失败 %s: %s", url, e)
        storage.mark_seen(url)  # 失败也记，避免反复重试
        return False


def refresh():
    """抓取所有启用的公众号，返回统计。节流由调用方/内部判断"""
    now = int(time.time())
    last = storage.get_last_refresh()
    if now - last < config.REFRESH_INTERVAL_SECONDS:
        return {"status": "throttled", "message": "刷新太频繁，稍后再试",
                "next_after": config.REFRESH_INTERVAL_SECONDS - (now - last)}

    added = 0
    total = 0
    statuses = {}
    consecutive_blocks = 0
    for acc in config.ACCOUNTS:
        if not acc.get("enabled", True):
            continue
        name = acc["name"]
        total += 1
        try:
            if acc.get("rss"):
                articles = fetch_rss(acc)
            else:
                if consecutive_blocks >= config.MAX_CONSECUTIVE_BLOCKS:
                    statuses[name] = "本轮已因频繁拦截提前结束"
                    storage.set_source_status(name, "⚠ 被拦截过多，本轮跳过")
                    continue
                articles = sogou_search_account(name)
                if articles is None:
                    consecutive_blocks += 1
                    statuses[name] = "被搜狗拦截（验证码）"
                    storage.set_source_status(name, "⚠ 被拦截，稍后自动重试")
                    continue
                else:
                    consecutive_blocks = 0
                    articles = articles[:config.MAX_ARTICLES_PER_ACCOUNT]
            for a in articles:
                if _process_new(name, a):
                    added += 1
            statuses[name] = "成功 %d 篇" % len(articles)
            storage.set_source_status(name, "✓ 最近更新")
        except Exception as e:
            log.warning("公众号 %s 抓取异常: %s", name, e)
            statuses[name] = "异常: %s" % e
            storage.set_source_status(name, "⚠ 抓取异常")

    storage.set_last_refresh(int(time.time()))
    return {"status": "ok", "added": added, "sources_total": total, "sources_ok": len(statuses)}


def add_manual_link(url, source="手动添加"):
    """手动链接兜底：抓正文 → 分类 → 入库"""
    try:
        title, content, date = fetch_article(url)
    except Exception as e:
        return {"ok": False, "error": "抓取失败: %s" % e}
    if not title and not content:
        return {"ok": False, "error": "内容为空（链接可能已失效或需要登录）"}
    item = classifier.classify(title, content, source, url, date)
    is_new = storage.upsert_item(item)
    storage.mark_seen(url)
    return {"ok": True, "is_new": is_new, "item": item}
