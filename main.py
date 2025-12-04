from fastapi import FastAPI, Request, Response
import uvicorn
import json
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

app = FastAPI()

# --- 飞书配置 (请填入你的应用信息) ---
# 请在飞书开放平台 (open.feishu.cn) 获取以下信息
APP_ID = "cli_a9bbb79c71389bb4" 
APP_SECRET = "1kqNqwnfqFqJU7bsrw1zubVjx3nqMmXn"
VERIFICATION_TOKEN = "xxxxxxxxxxxxxxxx"
ENCRYPT_KEY = "" # 如果开启了加密，请填入

# --- 初始化 Lark Client ---
client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .log_level(lark.LogLevel.DEBUG) \
    .build()

# --- 消息处理函数 ---
def handle_message(data):
    # 解析消息内容
    msg_content = json.loads(data.event.message.content)
    text = msg_content.get("text", "")
    chat_id = data.event.message.chat_id
    print(f"收到消息: {text}")

    # 构造回复消息
    request = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": f"收到: {text}"}))
            .build()) \
        .build()

    # 发送回复
    resp = client.im.v1.message.create(request)
    if not resp.success():
        print(f"回复失败: {resp.code}, {resp.msg}")

# --- 注册事件处理器 ---
# 自动处理 URL 验证 (url_verification)
event_handler = lark.EventDispatcherHandler.builder(ENCRYPT_KEY, VERIFICATION_TOKEN, lark.LogLevel.DEBUG) \
    .register_p2_im_message_receive_v1(handle_message) \
    .build()

@app.get("/")
async def root():
    return {"message": "Feishu Bot is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/webhook/event")
async def webhook_event(request: Request):
    # 将 FastAPI 请求转换为 Lark SDK 需要的 RawRequest
    req = lark.RawRequest(
        uri=str(request.url),
        headers=dict(request.headers),
        body=await request.body()
    )
    
    # 处理事件
    resp = event_handler.do(req)
    
    # 返回响应
    return Response(content=resp.body, status_code=resp.status_code, headers=dict(resp.headers))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
