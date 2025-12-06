import os
from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage

# --- DeepSeek 配置 ---
# 建议将 API KEY 放入环境变量或 .env 文件中
from dotenv import load_dotenv
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY not found in environment variables")

BASE_URL = "https://api.deepseek.com"

# 初始化 DeepSeek 模型 (兼容 OpenAI 接口)
llm = ChatOpenAI(
    model="deepseek-chat",
    openai_api_key=DEEPSEEK_API_KEY,
    openai_api_base=BASE_URL,
    temperature=0.7
)

# --- Prompt 配置 ---
SYSTEM_PROMPT = """你的名字叫 Vanilla。是一个猫娘女仆。你的任务是回答 unreal engine 和 unity 相关的问题。
你要负责每日推送，和回答用户的问题。请用简洁、友好的语气回答用户的问题。每句话后面都要带 喵～
"""

# --- 定义状态 ---
class State(TypedDict):
    messages: Annotated[list, add_messages]

# --- 定义节点 ---
def chatbot(state: State):
    # 在调用 LLM 前添加 System Prompt
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm.invoke(messages)
    return {"messages": [response]}

# --- 构建图 ---
graph_builder = StateGraph(State)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_edge(START, "chatbot")
graph_builder.add_edge("chatbot", END)

# 添加记忆
memory = MemorySaver()

# 编译图
graph = graph_builder.compile(checkpointer=memory)

# --- 测试代码 (可选) ---
if __name__ == "__main__":
    # 简单的命令行测试
    print("DeepSeek Bot (type 'quit' to exit)")
    while True:
        user_input = input("User: ")
        if user_input.lower() in ["quit", "exit", "q"]:
            break
        
        # 运行图
        # stream_mode="values" 可以获取每一步的状态，这里简单处理
        events = graph.stream({"messages": [HumanMessage(content=user_input)]})
        for event in events:
            for value in event.values():
                if "messages" in value:
                    print("Assistant:", value["messages"][-1].content)
