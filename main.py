# -*- coding: utf-8 -*-
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager
import uvicorn
import json
import os
import time
import asyncio
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessageChunk
from agent.index import graph
from agent.rss import get_rss_updates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from collections import OrderedDict
from utils.lrucache import LRUCache

# 加载环境变量
load_dotenv()


# 初始化 LRU Cache，容量为 1000
event_id_cache = LRUCache(1000)

# 全局静音状态
IS_MUTED = False

# --- 飞书配置 ---
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")

if not APP_ID or not APP_SECRET:
    raise ValueError("FEISHU_APP_ID or FEISHU_APP_SECRET not set in .env")

print("starting...")

# --- 初始化 Lark Client ---
client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .domain(lark.FEISHU_DOMAIN) \
    .timeout(3) \
    .log_level(lark.LogLevel.DEBUG) \
    .build()

SUBSCRIPTIONS_FILE = "subscriptions.json"

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

async def check_rss_and_push():
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
    print(subs)
    if not subs:
        print("No subscribers.")
        return

    
    for entry in updates:
        message_text = f"【Unreal Engine News】\n{entry['title']}\n{entry['link']}"
        for chat_id in subs:
            print(f"Pushing to {chat_id}: {entry['title']}")
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": message_text}))
                    .build()) \
                .build()
            
            # 注意：Lark SDK 是同步的，这里会阻塞。生产环境建议放入线程池。
            resp = client.im.v1.message.create(request)
            if not resp.success():
                print(f"Failed to push to {chat_id}: {resp.code}, {resp.msg}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    pid = os.getpid()
    print(f"[PID:{pid}] Starting application lifespan...")
    
    scheduler = AsyncIOScheduler()
    # 每半小时执行一次 RSS 检查
    scheduler.add_job(check_rss_and_push, 'cron', minute='*/30')
    # 每一分钟执行一次 Cache Sync
    scheduler.add_job(event_id_cache.sync, 'cron', minute='*')
    
    print(f"[PID:{pid}] Scheduler started. Jobs scheduled.")
    scheduler.start()

    # Start Lark WebSocket Client
    print(f"[PID:{pid}] Starting Lark WebSocket Client...")
    cli = lark.ws.Client(APP_ID, APP_SECRET,
                         event_handler=event_handler,
                         log_level=lark.LogLevel.DEBUG)
    
    import threading
    ws_thread = threading.Thread(target=cli.start)
    ws_thread.daemon = True
    ws_thread.start()

    yield
    print(f"[PID:{pid}] Application shutdown.")

app = FastAPI(lifespan=lifespan)

# --- 指令处理函数 ---
def cmd_mute(chat_id, text):
    global IS_MUTED
    IS_MUTED = True
    return "已开启静音模式，我将不再回复普通消息 (指令除外)。"

def cmd_unmute(chat_id, text):
    global IS_MUTED
    IS_MUTED = False
    return "已解除静音，恢复正常对话。"

def cmd_subscribe(chat_id, text):
    if add_subscription(chat_id):
        return "订阅成功！将为您推送 Unreal Engine 最新动态。"
    else:
        return "您已订阅，无需重复操作。"

def cmd_help(chat_id, text):
    help_lines = ["可用指令列表："]
    for cmd, info in COMMAND_MAP.items():
        help_lines.append(f"{cmd} - {info['desc']}")
    return "\n".join(help_lines)

def cmd_push_now(chat_id, text):
    # check_rss_and_push 是 async 的，这里需要同步调用
    # 但由于 check_rss_and_push 内部使用了 client.im.v1.message.create (同步)，
    # 我们可以直接调用同步版本的 check_rss_and_push_sync
    check_rss_and_push_sync()
    print("Manual RSS check and push triggered.")
    return "已手动触发 RSS 检查与推送。"

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
        message_text = f"【Unreal Engine News】\n{entry['title']}\n{entry['link']}"
        for chat_id in subs:
            print(f"Pushing to {chat_id}: {entry['title']}")
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": message_text}))
                    .build()) \
                .build()
            
            resp = client.im.v1.message.create(request)
            if not resp.success():
                print(f"Failed to push to {chat_id}: {resp.code}, {resp.msg}")

COMMAND_MAP = {
    "/mute": {
        "handler": cmd_mute,
        "desc": "开启静音模式 (暂停回复)"
    },
    "/unmute": {
        "handler": cmd_unmute,
        "desc": "解除静音模式"
    },
    "/subscribe": {
        "handler": cmd_subscribe,
        "desc": "订阅 Unreal Engine 新闻推送"
    },
    "/push": {
        "handler": cmd_push_now,
        "desc": "立刻执行一次 RSS 推送"
    },
    "/help": {
        "handler": cmd_help,
        "desc": "显示此帮助信息"
    }
}

# --- 消息处理函数 ---
def handle_message(data: lark.im.v1.P2ImMessageReceiveV1):
    # 获取 event_id
    event_id = data.header.event_id
    pid = os.getpid()

    # 检查消息时间，忽略 1 分钟前的消息
    try:
        create_time = int(data.event.message.create_time)
        current_time = int(time.time() * 1000)
        if current_time - create_time > 60 * 1000:
            print(f"[PID:{pid}] Message too old ({(current_time - create_time)/1000:.2f}s). Skipping.")
            return
    except Exception as e:
        print(f"[PID:{pid}] Warning: Could not check message age: {e}")

    print(f"[PID:{pid}] Received event_id: {event_id}")
    
    # 检查是否已处理
    if event_id_cache.get(event_id):
        print(f"[PID:{pid}] Event {event_id} already processed. Skipping.")
        return
    
    # 标记为已处理
    event_id_cache.put(event_id)

    # 解析消息内容
    msg_content = json.loads(data.event.message.content)
    text = msg_content.get("text", "").strip()
    chat_id = data.event.message.chat_id
    print(f"[PID:{pid}] 收到消息: {text}")

    global IS_MUTED

    
    # 简单分割
    parts = text.split()
    command_key = ""
    for part in parts:
        if part.startswith("/"):
            command_key = part
            break
            
    print(f"Processing command: {command_key}")
    
    if command_key in COMMAND_MAP:
        handler = COMMAND_MAP[command_key]["handler"]
        reply_text = handler(chat_id, text)
    else:
        # 如果处于静音模式，直接返回
        if IS_MUTED:
            print("Muted. Skipping response.")
            return
        
        # 普通消息处理
        try:
            # --- 普通一次性回复模式 ---
            config = {"configurable": {"thread_id": chat_id}}
            # graph.invoke 是同步阻塞的
            result = graph.invoke({"messages": [HumanMessage(content=text)]}, config=config)
            reply_text = result["messages"][-1].content
            print(f"DeepSeek 回复: {reply_text}")

        except Exception as e:
            print(f"DeepSeek 调用失败: {e}")
            reply_text = "抱歉，我遇到了一些问题，请稍后再试。"

    # 发送回复 (仅用于非流式模式或报错情况)
    request = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": reply_text}))
            .build()) \
        .build()

    # 发送回复
    # client.im.v1.message.create 是同步的
    resp = client.im.v1.message.create(request)
    if not resp.success():
        print(f"回复失败: {resp.code}, {resp.msg}")

# --- 注册事件处理器 ---
event_handler = lark.EventDispatcherHandler.builder("", "") \
    .register_p2_im_message_receive_v1(handle_message) \
    .build()

@app.get("/")
async def root():
    return {"message": "Feishu Bot with DeepSeek & RSS is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

def main():
    # 启动 FastAPI
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
