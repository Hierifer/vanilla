from fastapi import FastAPI, Request, Response
import uvicorn
import json
import os
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from agent.index import graph

# 加载环境变量
load_dotenv()

app = FastAPI()

# --- 飞书配置 ---
APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")
# VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN") # Optional if using WS
# ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY") # Optional

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

# --- 消息处理函数 ---
def handle_message(data: lark.im.v1.P2ImMessageReceiveV1):
    # 解析消息内容
    msg_content = json.loads(data.event.message.content)
    text = msg_content.get("text", "")
    chat_id = data.event.message.chat_id
    print(f"收到消息: {text}")

    # 使用 LangGraph 处理消息
    try:
        # 运行图
        # invoke 返回最终状态
        result = graph.invoke({"messages": [HumanMessage(content=text)]})
        response_text = result["messages"][-1].content
        print(f"DeepSeek 回复: {response_text}")
    except Exception as e:
        print(f"DeepSeek 调用失败: {e}")
        response_text = "抱歉，我遇到了一些问题，请稍后再试。"

    # 构造回复消息
    request = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": response_text}))
            .build()) \
        .build()

    # 发送回复
    resp = client.im.v1.message.create(request)
    if not resp.success():
        print(f"回复失败: {resp.code}, {resp.msg}")

# --- 注册事件处理器 ---
# 使用 WebSocket 模式，不需要 Verification Token 和 Encrypt Key (除非配置了)
event_handler = lark.EventDispatcherHandler.builder("", "") \
    .register_p2_im_message_receive_v1(handle_message) \
    .build()

@app.get("/")
async def root():
    return {"message": "Feishu Bot with DeepSeek is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

def main():
    # 使用 WebSocket 长连接
    cli = lark.ws.Client(APP_ID, APP_SECRET,
                         event_handler=event_handler,
                         log_level=lark.LogLevel.DEBUG)
    cli.start()
    
    # 启动 FastAPI (用于健康检查等)
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main()
