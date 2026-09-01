# -*- coding: utf-8 -*-
"""多源爬虫：搜狗微信搜索 + 天大官网新闻 + 手动链接兜底 + 文章正文抓取"""
import re
import time
import logging
import urllib.parse

import requests

import config
import storage
import classifier

log = logging.getLogger("crawler")

# ============ 天大官网新闻 ============
TJU_NEWS_BASE = "https://news.tju.edu.cn"
# 栏目 -> 显示名（尽量覆盖竞赛/教学/院系通知）
TJU_NEWS_COLS = [
    {"path": "/xw/xsky.htm", "label": "天大官网·学术科研"},
    {"path": "/xw/rcpy.htm", "label": "天大官网·人才培养"},
    {"path": "/xw/xysx.htm", "label": "天大官网·院部时讯"},
    {"path": "/xw/xzhx.htm", "label": "天大官网·综合新闻"},
    {"path": "/xw/xxdt.htm", "label": "天大官网·信息动态"},
]
TJU_NEWS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch_tju_list(col, limit=20):
    """抓某个栏目的列表页，返回 [{url,title,date}]（默认取最新 limit 条）"""
    u = TJU_NEWS_BASE + col["path"]
    resp = requests.get(u, headers=TJU_NEWS_HEADERS, timeout=15, verify=False)
    resp.encoding = resp.apparent_encoding
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    # 官网列表结构：.list li / li 内含 a[href*='/info/'] + .times 日期
    out = []
    for li in soup.select(".list li, li"):
        a = li.select_one("a[href*='/info/']")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if len(title) < 6:
            continue
        url = urllib.parse.urljoin(TJU_NEWS_BASE, a.get("href"))
        date = ""
        for tm in li.select("[class*=time], .date, span"):
            m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", tm.get_text())
            if m:
                date = "%s-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                break
        out.append({"url": url, "title": title, "date": date})
    # 去重 + 限条数
    seen = set(); uniq = []
    for r in out:
        if r["url"] not in seen:
            seen.add(r["url"]); uniq.append(r)
    return uniq[:limit]


def fetch_tju_article(url):
    """抓详情页，返回 (title, content_text, date_iso)"""
    resp = requests.get(url, headers=TJU_NEWS_HEADERS, timeout=15, verify=False)
    resp.encoding = resp.apparent_encoding
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")
    t_el = soup.select_one(".info_title") or soup.select_one("h1") or soup.select_one("title")
    c_el = (soup.select_one(".v_news_content") or soup.select_one("#vsb_content")
            or soup.select_one(".content") or soup.select_one(".article"))
    title = t_el.get_text(" ", strip=True) if t_el else ""
    content = c_el.get_text(" ", strip=True) if c_el else ""
    date = ""
    # 详情页日期：优先 .times 里第二个（第一个是栏目英文名），否则全文搜日期
    d_els = soup.select(".times") or soup.select("[class*=time]")
    for d_el in d_els:
        m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", d_el.get_text())
        if m:
            date = "%s-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            break
    if not date:
        m = re.search(r"(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})", resp.text)
        if m:
            date = "%s-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return title, content, date



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

def parse_date(text):
    """把微信常见日期格式解析为 ISO 日期 'YYYY-MM-DD'，失败返回 None"""
    text = text or ""
    # 2026-08-10 / 2026/8/10 / 2026年8月10日
    m = re.search(r"(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})", text)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 8月10日（无年份 → 今年；若日期在未来则归为去年）
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        import datetime
        mo, d = int(m.group(1)), int(m.group(2))
        now = datetime.date.today()
        y = now.year
        if (mo, d) > (now.month, now.day):
            y -= 1
        return "%04d-%02d-%02d" % (y, mo, d)
    return None


def is_recent(date_str, max_days):
    """日期是否在 max_days 天内；未知日期不误删（返回 True）"""
    if not date_str:
        return True
    try:
        import datetime
        y, mo, d = map(int, date_str.split("-"))
        dt = datetime.date(y, mo, d)
        return (datetime.date.today() - dt).days <= max_days
    except Exception:
        return True


def fetch_article(url):
    """返回 (title, content_text, date_iso, account, perm_url)
    perm_url = 文章永久链接（mp.weixin.qq.com/s/xxx，永不过期）；找不到则返回 None"""
    resp = _get(url)
    if resp.status_code != 200:
        raise RuntimeError("HTTP %s" % resp.status_code)
    html = resp.text
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    t_el = (soup.select_one("#activity-name")
            or soup.select_one("h1.rich_media_title")
            or soup.select_one("h1"))
    c_el = soup.select_one("#js_content") or soup.select_one("div.rich_media_content")
    title = t_el.get_text(" ", strip=True) if t_el else ""
    content = c_el.get_text(" ", strip=True) if c_el else ""

    # 永久链接：优先 canonical/og:url，其次全文正则（mp.weixin.qq.com/s/xxx）
    perm_url = None
    canon = soup.select_one('link[rel="canonical"]')
    if canon and "mp.weixin.qq.com" in canon.get("href", ""):
        perm_url = canon.get("href")
    if not perm_url:
        og = soup.select_one('meta[property="og:url"]')
        if og and "mp.weixin.qq.com" in og.get("content", ""):
            perm_url = og.get("content")
    if not perm_url:
        m = re.search(r"https://mp\.weixin\.qq\.com/s/[A-Za-z0-9_\-]+", html)
        if m:
            perm_url = m.group(0)

    # 公众号名：#js_name 或 .rich_media_meta_nickname
    a_el = (soup.select_one("#js_name")
            or soup.select_one(".rich_media_meta_nickname")
            or soup.select_one(".profile_nickname"))
    account = a_el.get_text(strip=True) if a_el else ""

    # 日期：优先 #publish_time / og 标签 / 全文第一个日期
    date = ""
    p_el = soup.select_one("#publish_time") or soup.select_one("em#publish_time")
    if p_el:
        date = parse_date(p_el.get_text())
    if not date:
        og = soup.select_one('meta[property="og:article:published_time"]')
        if og:
            date = parse_date(og.get("content", ""))
    if not date:
        m = re.search(r"20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}", html)
        if m:
            date = parse_date(m.group(0))
    return title, content, date, account, perm_url


# ============ 主流程 ============
def _normalize(s):
    return re.sub(r"\s+|、|，|,|-|·", "", s or "")


def _is_known_account(account):
    """严格校验文章公众号是否在用户名单里。
    规则：文章号名 == 名单号名；或文章号名是某个名单号名的子名（且文章号名足够长）。
    绝不反向匹配（否则名单里的『天津大学』会让所有『天津大学XX』混进来）。
    """
    if not account:
        return True   # 页面拿不到号名时保留（#js_name 基本都能拿到）
    an = _normalize(account)
    for acc in config.ACCOUNTS:
        if not acc.get("enabled", True):
            continue
        cn = _normalize(acc["name"])
        if an == cn:
            return True
        if len(an) >= 4 and an in cn:
            return True
    return False


def _is_perm_link(url):
    """永久链接格式：mp.weixin.qq.com/s/xxx（无 src=11 临时参数）"""
    return bool(url) and "mp.weixin.qq.com/s/" in url and "src=11" not in url


def _is_tju_news(url):
    """天大官网新闻链接"""
    return bool(url) and ("news.tju.edu.cn" in url or "tju.edu.cn/info" in url)


def prune(items):
    """导出前清洗：
    - 官网新闻（news.tju.edu.cn）：始终保留（只要近期）
    - 公众号（mp.weixin）：只保留 名单内 + 永久链接 + 近期
    """
    out = []
    for it in items:
        url = it.get("url", "")
        d = it.get("date", "")
        if d and not is_recent(d, config.MAX_ARTICLE_AGE_DAYS):
            continue
        if _is_tju_news(url):
            out.append(it)
            continue
        if not _is_perm_link(url):
            continue
        if not _is_known_account(it.get("source", "")):
            continue
        out.append(it)
    return out


def _process_new(account_name, article):
    """对一篇新文章：抓正文 → 校验来源(只留名单里的号) → 过滤旧文 → 分类入库。返回是否新增"""
    url = article["url"]
    if not url or storage.seen(url):
        return False
    if "mp.weixin.qq.com" not in url:      # 只存真实文章链接
        storage.mark_seen(url)
        return False
    try:
        title, content, date, account, perm_url = fetch_article(url)
        # ① 只保留用户名单里的公众号文章
        if not _is_known_account(account):
            storage.mark_seen(url)
            return False
        # ③ 只存永久链接（搜狗临时链接会过期）
        if not perm_url:
            storage.mark_seen(url)
            return False
        if storage.seen(perm_url):
            storage.mark_seen(url)
            return False
        if not title and not content:
            title = article.get("title", "")
        date = date or article.get("date", "") or ""
        date = parse_date(date) or date
        # ② 只保留近期文章（搜狗按公众号搜常返回陈年旧文）
        if date and not is_recent(date, config.MAX_ARTICLE_AGE_DAYS):
            storage.mark_seen(url)
            return False
        item = classifier.classify(title, content, account or account_name, perm_url, date)
        storage.mark_seen(url)
        storage.mark_seen(perm_url)
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
    # 分批：按游标取 BATCH_SIZE 个公众号，下一轮从后面继续（轮流覆盖全部，降低单轮请求量）
    accounts = [a for a in config.ACCOUNTS if a.get("enabled", True)]
    offset = storage.get_batch_offset() % len(accounts) if accounts else 0
    batch = accounts[offset:offset + config.BATCH_SIZE]
    storage.set_batch_offset((offset + config.BATCH_SIZE))
    if not batch:
        batch = accounts

    for acc in batch:
        name = acc["name"]
        total += 1
        try:
            if acc.get("rss"):
                articles = fetch_rss(acc)
            else:
                # 被拦的号：跳过但继续下一个（不中断整轮）；达到硬上限才提前结束
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
    return {"status": "ok", "added": added, "sources_total": total,
            "sources_ok": len(statuses), "batch": len(batch)}


def crawl_tju_news():
    """天大官网新闻：抓列表 → 详情 → 分类入库。官网稳定、最新，是主数据源。返回新增数"""
    added = 0
    # 官网分批：每轮抓 2 个栏目，避免超时；用本轮的 url 去重（跨栏目同文章只留一次）
    cols = TJU_NEWS_COLS
    offset = storage.get_batch_offset() % len(cols) if cols else 0
    this_cols = cols[offset:offset + 2]
    storage.set_batch_offset(offset + 2)
    if not this_cols:
        this_cols = cols
    seen_this_round = set()
    for col in this_cols:
        try:
            items = fetch_tju_list(col, limit=15)
        except Exception as e:
            log.warning("官网栏目 %s 抓列表失败: %s", col["path"], e)
            continue
        for it in items:
            url = it["url"]
            if url in seen_this_round:      # 跨栏目同文章去重
                continue
            try:
                title, content, date = fetch_tju_article(url)
            except Exception as e:
                log.warning("官网文章抓取失败 %s: %s", url, e)
                continue
            seen_this_round.add(url)
            if not title and not content:
                title = it["title"]
            date = date or it["date"] or ""
            if date and not is_recent(date, config.MAX_ARTICLE_AGE_DAYS):
                continue
            item = classifier.classify(title, content, col["label"], url, date)
            if storage.upsert_item(item):
                added += 1
    return added


def add_manual_link(url, source="手动添加"):
    """手动链接兜底：抓正文 → 分类 → 入库"""
    try:
        title, content, date, account, perm_url = fetch_article(url)
    except Exception as e:
        return {"ok": False, "error": "抓取失败: %s" % e}
    if not title and not content:
        return {"ok": False, "error": "内容为空（链接可能已失效或需要登录）"}
    final_url = perm_url or url
    item = classifier.classify(title, content, account or source, final_url, date or "")
    is_new = storage.upsert_item(item)
    storage.mark_seen(url)
    if perm_url:
        storage.mark_seen(perm_url)
    return {"ok": True, "is_new": is_new, "item": item}
