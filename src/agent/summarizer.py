import os
import requests
from bs4 import BeautifulSoup
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

# Load env if needed, but main.py should have loaded it. 
# However, to be safe if imported independently:

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_URL = "https://api.deepseek.com"

def get_llm():
    if not DEEPSEEK_API_KEY:
        print("Warning: DEEPSEEK_API_KEY not set.")
        return None
    
    return ChatOpenAI(
        model="deepseek-chat",
        openai_api_key=DEEPSEEK_API_KEY,
        openai_api_base=BASE_URL,
        temperature=0.3
    )

def fetch_page_content(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
            
        # Get text
        text = soup.get_text()
        
        # Break into lines and remove leading/trailing space on each
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # Limit to ~4000 chars to avoid context overflow for simple summary
        return text[:4000]
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def fetch_and_summarize(url):
    print(f"Summarizing URL: {url}")
    content = fetch_page_content(url)
    if not content:
        return "无法获取页面内容，无法生成摘要。"
    
    llm = get_llm()
    if not llm:
        return "LLM 未配置，无法生成摘要。"

    prompt = f"""
请阅读以下网页正文内容，并用中文总结成 2-3 句话。
总结重点：这篇文章的核心新闻、技术亮点或主要观点。

内容：
{content}
"""
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        return response.content.strip()
    except Exception as e:
        print(f"Error generating summary: {e}")
        return "生成摘要时发生错误。"
