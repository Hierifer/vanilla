import os
import json
import time
import logging
import random
import lark_oapi as lark
from lark_oapi.api.im.v1 import *
from langchain_core.messages import HumanMessage, AIMessage
from src.agent.index import graph


class LarkClient:
    def __init__(self, app_id: str, app_secret: str, event_id_cache, log_level=lark.LogLevel.DEBUG):
        self.app_id = app_id
        self.app_secret = app_secret
        self.event_id_cache = event_id_cache
        self.logger = logging.getLogger(__name__)
        
        # Initialize Lark Client
        self.client = lark.Client.builder() \
            .app_id(self.app_id) \
            .app_secret(self.app_secret) \
            .domain(lark.FEISHU_DOMAIN) \
            .timeout(3) \
            .log_level(log_level) \
            .build()
            
        # Initialize Event Handler
        self.event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._handle_message) \
            .build()
        
        print("LarkClient initialized.")
            
        # Hardcoded command map for now, can be injected or extended later
        self.command_map = {
            "/mute": {"handler": self._cmd_mute, "desc": "开启静音模式 (暂停回复)"},
            "/unmute": {"handler": self._cmd_unmute, "desc": "解除静音模式"},
            "/help": {"handler": self._cmd_help, "desc": "显示此帮助信息"}, 
        }
        self.is_muted = False
        self.chat_log_callback = None # Function to call for logging chats
        self.history_provider = None  # Function to retrieve chat history

    @property
    def api_client(self):
        return self.client
        
    @property
    def handler(self):
        return self.event_handler

    def set_chat_log_callback(self, callback):
        self.chat_log_callback = callback
    
    def set_history_provider(self, provider):
        self.history_provider = provider

    def register_command(self, command, handler, desc):
        self.command_map[command] = {"handler": handler, "desc": desc}

    def send_text_message(self, receive_id, text, receive_id_type="chat_id"):
        content = json.dumps({"text": text})
        request = CreateMessageRequest.builder() \
            .receive_id_type(receive_id_type) \
            .request_body(CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("text")
                .content(content)
                .build()) \
            .build()
        return self.client.im.v1.message.create(request)

    def _cmd_mute(self, chat_id, text):
        self.is_muted = True
        return "已开启静音模式，我将不再回复普通消息 (指令除外)。"

    def _cmd_unmute(self, chat_id, text):
        self.is_muted = False
        return "已解除静音，恢复正常对话。"

    def _cmd_help(self, chat_id, text):
        help_lines = ["可用指令列表："]
        for cmd, info in self.command_map.items():
            help_lines.append(f"{cmd} - {info['desc']}")
        return "\n".join(help_lines)

    def _handle_message(self, data: lark.im.v1.P2ImMessageReceiveV1):
        event_id = data.header.event_id
        pid = os.getpid()

        # Check message age (ignore > 1 min)
        create_time = None
        try:
            create_time = int(data.event.message.create_time)
            current_time = int(time.time() * 1000)
            if current_time - create_time > 60 * 1000:
                print(f"[PID:{pid}] Message too old. Skipping.")
                return
        except Exception as e:
            print(f"[PID:{pid}] Warning: Could not check message age: {e}")

        # Check for duplicates
        if self.event_id_cache.get(event_id):
            print(f"[PID:{pid}] Event {event_id} already processed. Skipping.")
            return
        self.event_id_cache.put(event_id)

        # Parse content
        msg_content = json.loads(data.event.message.content)
        text = msg_content.get("text", "").strip()
        chat_id = data.event.message.chat_id
        
        # Gather sender metadata
        sender_meta = {}
        sender_obj = getattr(getattr(data.event, "sender", None), "sender_id", None)
        if sender_obj:
            sender_meta = {
                "user_id": getattr(sender_obj, "user_id", None),
                "open_id": getattr(sender_obj, "open_id", None),
                "union_id": getattr(sender_obj, "union_id", None)
            }

        print(f"[PID:{pid}] 收到消息: {text}")

        # Inbound Logging
        if self.chat_log_callback:
            self.chat_log_callback({
                "direction": "inbound",
                "timestamp_ms": create_time or int(time.time() * 1000),
                "chat_id": chat_id,
                "message_id": getattr(data.event.message, "message_id", None),
                "sender": sender_meta,
                "text": text
            })

        # Command Dispatch
        parts = text.split()
        command_key = parts[0] if parts and parts[0].startswith("/") else ""
        
        reply_text = ""
        if command_key in self.command_map:
            print(f"Processing command: {command_key}")
            handler = self.command_map[command_key]["handler"]
            reply_text = handler(chat_id, text)
        else:
            if self.is_muted:
                print("Muted. Skipping response.")
                return

            # Check mentions strategy
            mentions = getattr(data.event.message, "mentions", []) or []
            is_mentioned = False
            
            target_bot_name = os.getenv("BOT_NAME", "Neko☆Chocola")
            
            for m in mentions:
                if getattr(m, "name", "") == target_bot_name:
                    is_mentioned = True
                    break
            
            should_reply = False
            use_history = False
            
            if is_mentioned:
                print(f"Bot was explicitly mentioned ({target_bot_name}). Replying.")
                should_reply = True
            else:
                # 20% chance to reply if not mentioned
                if random.random() < 0.2:
                    print("Random reply triggered (20%). Using chat history.")
                    should_reply = True
                    use_history = True
                else:
                    print("Not mentioned and random check skipped. No reply.")
            
            if not should_reply:
                return
            
            # Application Logic (Graph Invoke)
            try:
                messages_input = [HumanMessage(content=text)]
                
                # If random trigger, try to fetch history
                if use_history and self.history_provider:
                    try:
                        history_data = self.history_provider(chat_id)
                        if history_data:
                            # Convert history dicts to LangChain messages
                            history_messages = []
                            for h in history_data:
                                # Skip current message if it somehow got logged already (check by content or exclude generally)
                                if h.get("text") == text:
                                    continue
                                    
                                if h.get("direction") == "inbound":
                                    history_messages.append(HumanMessage(content=h.get("text", "")))
                                elif h.get("direction") == "outbound":
                                    history_messages.append(AIMessage(content=h.get("text", "")))
                            
                            # Prepend history to current message
                            messages_input = history_messages + messages_input
                            print(f"Attached {len(history_messages)} historical messages to context.")
                    except Exception as he:
                        print(f"Failed to fetch/process history: {he}")
                
                config = {"configurable": {"thread_id": chat_id}}
                result = graph.invoke({"messages": messages_input}, config=config)
                reply_text = result["messages"][-1].content
                print(f"DeepSeek 回复: {reply_text}")
            except Exception as e:
                print(f"DeepSeek 调用失败: {e}")
                reply_text = "抱歉，我遇到了一些问题，请稍后再试。"

        # Send Reply
        resp = self.send_text_message(chat_id, reply_text)
        if not resp.success():
            print(f"回复失败: {resp.code}, {resp.msg}")

        # Outbound Logging
        if self.chat_log_callback:
            resp_message_id = getattr(resp.data, "message_id", None) if getattr(resp, "data", None) else None
            self.chat_log_callback({
                "direction": "outbound",
                "timestamp_ms": int(time.time() * 1000),
                "chat_id": chat_id,
                "message_id": resp_message_id,
                "status": "ok" if resp.success() else "error",
                "text": reply_text,
                "error": None if resp.success() else {"code": resp.code, "msg": resp.msg}
            })
