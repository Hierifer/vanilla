import feedparser
import json
import os
from datetime import datetime
import time
from email.utils import parsedate_to_datetime

RSS_URL = "https://www.unrealengine.com/en-US/rss"
STATE_FILE = "rss_state.json"

def get_rss_updates():
    """
    获取 RSS 更新，返回新文章列表。
    """
    feed = feedparser.parse(RSS_URL)

    print(feed)
    
    if not os.path.exists(STATE_FILE):
        last_published = 0
        # 如果是第一次运行，只保存最新的一条作为标记，避免一次性推送太多
        # 或者可以设置为 0，推送所有（如果 feed 条目不多）
        # 这里为了安全起见，初始化为当前 feed 的第一条时间（如果有）
        if feed.entries:
             # 尝试解析时间，RSS 时间格式可能不同
            latest_entry = feed.entries[0]
            published_parsed = latest_entry.get("published_parsed") or latest_entry.get("updated_parsed")
            if published_parsed:
                last_published = time.mktime(published_parsed)
            
            save_state(last_published)
            return [] # 首次运行不推送，或者根据需求修改
    else:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
            last_published = data.get("last_published", 0)

    new_entries = []
    max_published = last_published

    # 遍历条目（通常 RSS 是按时间倒序的，但为了保险起见，我们检查所有）
    for entry in feed.entries:
        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not published_parsed:
            continue
        
        published_ts = time.mktime(published_parsed)
        
        if published_ts > last_published:
            new_entries.append({
                "title": entry.title,
                "link": entry.link,
                "summary": entry.summary if 'summary' in entry else "",
                "published": entry.published if 'published' in entry else ""
            })
            if published_ts > max_published:
                max_published = published_ts

    if max_published > last_published:
        save_state(max_published)

    return new_entries

def save_state(last_published):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_published": last_published}, f)

if __name__ == "__main__":
    # Test
    updates = get_rss_updates()
    print(f"Found {len(updates)} updates")
    for up in updates:
        print(f"- {up['title']}")
