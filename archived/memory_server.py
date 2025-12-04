# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from memory import CompressedRecentHistoryManager, SemanticMemory, ImportantSettingsManager, TimeIndexedMemory
from fastapi import FastAPI
import json
import uvicorn
from langchain_core.messages import convert_to_messages
from uuid import uuid4
from config import get_character_data, MEMORY_SERVER_PORT
from pydantic import BaseModel
import re
import asyncio
import logging
import argparse

# Setup logger
logger = logging.getLogger(__name__)

class HistoryRequest(BaseModel):
    input_history: str

app = FastAPI()

# 初始化组件
recent_history_manager = CompressedRecentHistoryManager()
semantic_manager = SemanticMemory(recent_history_manager)
settings_manager = ImportantSettingsManager()
time_manager = TimeIndexedMemory(recent_history_manager)

# 全局变量用于控制服务器关闭
shutdown_event = asyncio.Event()
# 全局变量控制是否响应退出请求
enable_shutdown = False

@app.post("/shutdown")
async def shutdown_memory_server():
    """接收来自main_server的关闭信号"""
    global enable_shutdown
    if not enable_shutdown:
        logger.warning("收到关闭信号，但当前模式不允许响应退出请求")
        return {"status": "shutdown_disabled", "message": "当前模式不允许响应退出请求"}
    
    try:
        logger.info("收到来自main_server的关闭信号")
        shutdown_event.set()
        return {"status": "shutdown_signal_received"}
    except Exception as e:
        logger.error(f"处理关闭信号时出错: {e}")
        return {"status": "error", "message": str(e)}

@app.on_event("shutdown")
async def shutdown_event_handler():
    """应用关闭时执行清理工作"""
    logger.info("Memory server正在关闭...")
    # 这里可以添加任何需要的清理工作
    logger.info("Memory server已关闭")


@app.post("/process/{lanlan_name}")
def process_conversation(request: HistoryRequest, lanlan_name: str):
    try:
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        recent_history_manager.update_history(input_history, lanlan_name)
        """
        下面屏蔽了两个模块，因为这两个模块需要消耗token，但当前版本实用性近乎于0。尤其是，Qwen与GPT等旗舰模型相比性能差距过大。
        """
        # settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # semantic_manager.store_conversation(uid, input_history, lanlan_name)
        time_manager.store_conversation(uid, input_history, lanlan_name)
        recent_history_manager.review_history(lanlan_name)
        return {"status": "processed"}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}

@app.post("/renew/{lanlan_name}")
def process_conversation_for_renew(request: HistoryRequest, lanlan_name: str):
    try:
        uid = str(uuid4())
        input_history = convert_to_messages(json.loads(request.input_history))
        recent_history_manager.update_history(input_history, lanlan_name, detailed=True)
        # settings_manager.extract_and_update_settings(input_history, lanlan_name)
        # semantic_manager.store_conversation(uid, input_history, lanlan_name)
        time_manager.store_conversation(uid, input_history, lanlan_name)
        # recent_history_manager.review_history(lanlan_name)
        return {"status": "processed"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/get_recent_history/{lanlan_name}")
def get_recent_history(lanlan_name: str):
    history = recent_history_manager.get_recent_history(lanlan_name)
    _, _, _, _, name_mapping, _, _, _, _, _ = get_character_data()
    name_mapping['ai'] = lanlan_name
    result = f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
    for i in history:
        if i.type == 'system':
            result += i.content + "\n"
        else:
            result += f"{name_mapping[i.type]} | {'\n'.join([j['text'] for j in i.content if j['type']=='text'])}\n"
    return result

@app.get("/search_for_memory/{lanlan_name}/{query}")
def get_memory(query: str, lanlan_name:str):
    return semantic_manager.query(query, lanlan_name)

@app.get("/get_settings/{lanlan_name}")
def get_settings(lanlan_name: str):
    result = f"{lanlan_name}记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}"
    return result

@app.get("/new_dialog/{lanlan_name}")
def new_dialog(lanlan_name: str):
    m1 = re.compile('$$.*?$$')
    master_name, _, _, _, name_mapping, _, _, _, _, _ = get_character_data()
    name_mapping['ai'] = lanlan_name
    result = f"\n========{lanlan_name}的内心活动========\n{lanlan_name}的脑海里经常想着自己和{master_name}的事情，她记得{json.dumps(settings_manager.get_settings(lanlan_name), ensure_ascii=False)}\n\n"
    result += f"开始聊天前，{lanlan_name}又在脑海内整理了近期发生的事情。\n"
    for i in recent_history_manager.get_recent_history(lanlan_name):
        if type(i.content) == str:
            result += f"{name_mapping[i.type]} | {i.content}\n"
        else:
            result += f"{name_mapping[i.type]} | {'\n'.join([m1.sub(j['text'], '') for j in i.content if j['type'] == 'text'])}\n"
    return result

if __name__ == "__main__":
    import threading
    import time
    import signal
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='Memory Server')
    parser.add_argument('--enable-shutdown', action='store_true', 
                       help='启用响应退出请求功能（仅在终端用户环境使用）')
    args = parser.parse_args()
    
    # 设置全局变量
    enable_shutdown = args.enable_shutdown
    
    # 创建一个后台线程来监控关闭信号
    def monitor_shutdown():
        while not shutdown_event.is_set():
            time.sleep(0.1)
        logger.info("检测到关闭信号，正在关闭memory_server...")
        # 发送SIGTERM信号给当前进程
        os.kill(os.getpid(), signal.SIGTERM)
    
    # 只有在启用关闭功能时才启动监控线程
    if enable_shutdown:
        shutdown_monitor = threading.Thread(target=monitor_shutdown, daemon=True)
        shutdown_monitor.start()
    
    # 启动服务器
    uvicorn.run(app, host="0.0.0.0", port=MEMORY_SERVER_PORT)