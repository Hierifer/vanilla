# -*- coding: utf-8 -*-
from fastapi import FastAPI
from contextlib import asynccontextmanager
import uvicorn
import json
import os
import asyncio
import threading
from dotenv import load_dotenv

# 加载环境变量
# 必须在导入 src.larkClient 之前加载，否则 src.agent.index 会先加载默认的 .env
load_dotenv(dotenv_path=".env.example", override=True)

APP_ID_DEBUG = os.getenv("FEISHU_APP_ID", "")
print(f"DEBUG: Loaded FEISHU_APP_ID from env: {APP_ID_DEBUG[:10]}***")

import lark_oapi as lark
from src.larkClient import LarkClient
from src.agent.rss import get_rss_updates
from src.agent.summarizer import fetch_and_summarize
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from src.utils.lrucache import LRUCache

# 初始化 LRU Cache，容量为 1000
event_id_cache = LRUCache(1000)

SUBSCRIPTIONS_FILE = "./storage/subscriptions.json"
CHAT_LOG_FILE = "logs/chat_history.jsonl"
_chat_log_lock = threading.Lock()

# --- 飞书配置 ---
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
BOT_NAME = os.getenv("BOT_NAME")

print(f"starting...{BOT_NAME}")

if not APP_ID or not APP_SECRET:
    raise ValueError("FEISHU_APP_ID or FEISHU_APP_SECRET not set in .env")

# --- 初始化 Lark Client ---
# 传入 event_id_cache 供客户端去重使用
lark_client_instance = LarkClient(APP_ID, APP_SECRET, event_id_cache)
client = lark_client_instance.api_client

def get_subscribed_chats():
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return []
    with open(SUBSCRIPTIONS_FILE, "r") as f:
        return json.load(f)

def add_subscription(chat_id):
    subs = get_subscribed_chats()
    if chat_id not in subs:
        subs.append(chat_id)
        with open(SUBSCRIPTIONS_FILE, "w") as f:
            json.dump(subs, f)
        return True
    return False

import time

def append_chat_log(entry: dict):
    """Persist chat transcripts locally for audit and delayed reactions."""
    try:
        log_dir = os.path.dirname(CHAT_LOG_FILE)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_line = json.dumps(entry, ensure_ascii=False)
        with _chat_log_lock:
            with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
    except Exception as e:
        print(f"Failed to write chat log: {e}")

def get_chat_history(chat_id, days=1):
    """Retrieve chat history for the specified chat_id from the last N days."""
    try:
        if not os.path.exists(CHAT_LOG_FILE):
            return []
            
        current_time_ms = int(time.time() * 1000)
        cutoff_time_ms = current_time_ms - (days * 24 * 60 * 60 * 1000)
        
        history = []
        with _chat_log_lock:
            with open(CHAT_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        entry_time = entry.get("timestamp_ms", 0)
                        
                        if entry.get("chat_id") == chat_id and entry_time >= cutoff_time_ms:
                            history.append(entry)
                    except json.JSONDecodeError:
                        continue
        # Ensure chronological order
        history.sort(key=lambda x: x.get("timestamp_ms", 0))
        return history
    except Exception as e:
        print(f"Error reading chat history: {e}")
        return []

# 注册回调和 History Provider
lark_client_instance.set_chat_log_callback(append_chat_log)
lark_client_instance.set_history_provider(get_chat_history)

def check_rss_and_push():
    print("Checking RSS updates...")
    try:
        updates = get_rss_updates()
    except Exception as e:
        print(f"Error fetching RSS: {e}")
        return

    if not updates:
        print("No new updates.")
        return

    subs = get_subscribed_chats()
    print(f"Subscribers: {subs}")
    if not subs:
        print("No subscribers.")
        return
    
    for entry in updates:
        print(f"Generating summary for: {entry['title']}")
        summary = fetch_and_summarize(entry['link'])
        message_text = f"【GameDev News】\n{entry['title']}\n{entry['link']}\n\n【女仆摘要】\n{summary}"
        for chat_id in subs:
            print(f"Pushing to {chat_id}: {entry['title']}")
            resp = lark_client_instance.send_text_message(chat_id, message_text)
            if not resp.success():
                print(f"Failed to push to {chat_id}: {resp.code}, {resp.msg}")

async def check_rss_and_push_async():
    """
    Async wrapper for check_rss_and_push with timeout.
    """
    print("Starting scheduled RSS check (Async)...")
    try:
        # 设置 300 秒 (5分钟) 的整体超时时间
        await asyncio.wait_for(asyncio.to_thread(check_rss_and_push), timeout=300)
    except asyncio.TimeoutError:
        print("Scheduled RSS check timed out after 300s!")
    except Exception as e:
        print(f"Error in scheduled RSS check: {e}")

def check_rss_and_push_sync():
    print("Checking RSS updates (Sync)...")
    try:
        updates = get_rss_updates()
    except Exception as e:
        print(f"Error fetching RSS: {e}")
        return

    if not updates:
        print("No new updates.")
        return

    subs = get_subscribed_chats()
    if not subs:
        print("No subscribers.")
        return

    for entry in updates:
        print(f"Generating summary for: {entry['title']}")
        summary = fetch_and_summarize(entry['link'])
        message_text = f"【Neko 新闻】\n{entry['title']}\n{entry['link']}\n\n【AI 摘要】\n{summary}"
        for chat_id in subs:
            print(f"Pushing to {chat_id}: {entry['title']}")
            resp = lark_client_instance.send_text_message(chat_id, message_text)
            if not resp.success():
                print(f"Failed to push to {chat_id}: {resp.code}, {resp.msg}")


# --- 定义依赖主逻辑的指令 ---
def cmd_subscribe(chat_id, text):
    if add_subscription(chat_id):
        return "订阅成功！将为您推送游戏开发最新动态。"
    else:
        return "您已订阅，无需重复操作。"

def cmd_push_now(chat_id, text):
    # check_rss_and_push_sync 是 main 中的函数
    check_rss_and_push_sync()
    print("Manual RSS check and push triggered.")
    return "已手动触发 RSS 检查与推送。"

# --- 注册外部指令到 LarkClient ---
lark_client_instance.register_command("/subscribe", cmd_subscribe, "订阅 Unreal Engine 新闻推送")
lark_client_instance.register_command("/push", cmd_push_now, "立刻执行一次 RSS 推送")


@asynccontextmanager
async def lifespan(app: FastAPI):
    pid = os.getpid()
    print(f"[PID:{pid}] Starting application lifespan...")
    
    scheduler = AsyncIOScheduler()
    # 每半小时执行一次 RSS 检查
    scheduler.add_job(check_rss_and_push_async, 'cron', minute='*/30', max_instances=3)
    # 每一分钟执行一次 Cache Sync
    scheduler.add_job(event_id_cache.sync, 'cron', minute='*')
    
    print(f"[PID:{pid}] Scheduler started. Jobs scheduled.")
    scheduler.start()

    # Start Lark WebSocket Client
    print(f"[PID:{pid}] Starting Lark WebSocket Client...")
    # 使用 lark_client_instance.event_handler
    cli = lark.ws.Client(APP_ID, APP_SECRET,
                         event_handler=lark_client_instance.event_handler,
                         log_level=lark.LogLevel.DEBUG)
    
    ws_thread = threading.Thread(target=cli.start)
    ws_thread.daemon = True
    ws_thread.start()

    yield
    print(f"[PID:{pid}] Application shutdown.")

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"message": "Feishu Bot with LarkClient & RSS is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

def main():
    # 启动 FastAPI
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
