import feedparser
import json
import os
from datetime import datetime
import time
from email.utils import parsedate_to_datetime
import urllib.request

RSS_URLS = [
    "https://www.unrealengine.com/zh-CN/rss",
    "https://blog.unity.com/feed",
    "https://www.gamedev.net/articles/feed",
    "https://gamedev.net/blogs/feed/",
    "http://www.yystv.cn/rss/feed",
]


STATE_FILE = "rss_state.json"

def get_rss_updates():
    """
    获取 RSS 更新，返回新文章列表。
    """
    all_new_entries = []
    state = load_state()
    state_updated = False

    for url in RSS_URLS:
        try:
            print(f"Checking feed: {url}")
            # Use urllib to fetch with timeout to prevent hanging
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                feed = feedparser.parse(response)
            
            if not feed.entries:
                continue
            
            # 获取该 URL 的上次更新时间，默认为 0
            last_published = state.get(url, 0)
            max_published = last_published
            url_new_entries = []

            # 如果是该 URL 首次运行
            if last_published == 0:
                if feed.entries:
                    latest_entry = feed.entries[0]
                    published_parsed = latest_entry.get("published_parsed") or latest_entry.get("updated_parsed")
                    if published_parsed:
                        current_ts = time.mktime(published_parsed)
                        state[url] = current_ts
                        state_updated = True
                        
                        # 首次运行推送最新一条，以便确认
                        source_title = feed.feed.title if 'title' in feed.feed else "RSS Feed"
                        all_new_entries.append({
                            "title": f"[{source_title}] {latest_entry.title}",
                            "link": latest_entry.link,
                            "summary": latest_entry.summary if 'summary' in latest_entry else "",
                            "published": latest_entry.published if 'published' in latest_entry else ""
                        })
                continue

            # 遍历条目
            for entry in feed.entries:
                published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if not published_parsed:
                    continue
                
                published_ts = time.mktime(published_parsed)
                
                if published_ts > last_published:
                    source_title = feed.feed.title if 'title' in feed.feed else "RSS Feed"
                    url_new_entries.append({
                        "title": f"[{source_title}] {entry.title}",
                        "link": entry.link,
                        "summary": entry.summary if 'summary' in entry else "",
                        "published": entry.published if 'published' in entry else ""
                    })
                    if published_ts > max_published:
                        max_published = published_ts
            
            if max_published > last_published:
                state[url] = max_published
                state_updated = True
                all_new_entries.extend(url_new_entries)

        except Exception as e:
            print(f"Error fetching {url}: {e}")

    if state_updated:
        save_state(state)

    return all_new_entries

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            # 兼容旧格式：如果包含 "last_published"，说明是旧文件，重置为空字典
            if "last_published" in data:
                return {}
            return data
    except:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

if __name__ == "__main__":
    # Test
    updates = get_rss_updates()
    print(f"Found {len(updates)} updates")
    for up in updates:
        print(f"- {up['title']}")
