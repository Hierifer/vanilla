# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mimetypes
mimetypes.add_type("application/javascript", ".js")
import asyncio
import json
import traceback
import uuid
import logging
from datetime import datetime
import webbrowser

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, File, UploadFile, Form, Body
from fastapi.staticfiles import StaticFiles
from main_helper import core as core, cross_server as cross_server
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
from utils.preferences import load_user_preferences, update_model_preferences, validate_model_preferences, move_model_to_top
from utils.frontend_utils import find_models
from multiprocessing import Process, Queue, Event
import atexit
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService
import requests
from openai import AsyncOpenAI
from config import get_character_data, MAIN_SERVER_PORT, CORE_API_KEY, AUDIO_API_KEY, EMOTION_MODEL, OPENROUTER_API_KEY, OPENROUTER_URL, load_characters, save_characters
from config.prompts_sys import emotion_analysis_prompt
import glob

templates = Jinja2Templates(directory="./")

# Configure logging
def setup_logging():
    """Setup logging configuration"""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f'lanlan_server_{datetime.now().strftime("%Y%m%d")}.log', encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)

logger = setup_logging()

def cleanup():
    logger.info("Starting cleanup process")
    for k in sync_message_queue:
        while sync_message_queue[k] and not sync_message_queue[k].empty():
            sync_message_queue[k].get_nowait()
        sync_message_queue[k].close()
        sync_message_queue[k].join_thread()
    logger.info("Cleanup completed")
atexit.register(cleanup)
sync_message_queue = {}
sync_shutdown_event = {}
session_manager = {}
session_id = {}
sync_process = {}
# Unpack character data once for initialization
master_name, her_name, master_basic_config, lanlan_basic_config, name_mapping, lanlan_prompt, semantic_store, time_store, setting_store, recent_log = get_character_data()
catgirl_names = list(lanlan_prompt.keys())
for k in catgirl_names:
    sync_message_queue[k] = Queue()
    sync_shutdown_event[k] = Event()
    session_manager[k] = core.LLMSessionManager(
        sync_message_queue[k],
        k,
        lanlan_prompt[k].replace('{LANLAN_NAME}', k).replace('{MASTER_NAME}', master_name)
    )
    session_id[k] = None
    sync_process[k] = None
lock = asyncio.Lock()

# --- FastAPI App Setup ---
app = FastAPI()

class CustomStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith('.js'):
            response.headers['Content-Type'] = 'application/javascript'
        return response
app.mount("/static", CustomStaticFiles(directory="static"), name="static")

# 使用 FastAPI 的 app.state 来管理启动配置
def get_start_config():
    """从 app.state 获取启动配置"""
    if hasattr(app.state, 'start_config'):
        return app.state.start_config
    return {
        "browser_mode_enabled": False,
        "browser_page": "chara_manager",
        'server': None
    }

def set_start_config(config):
    """设置启动配置到 app.state"""
    app.state.start_config = config

@app.get("/", response_class=HTMLResponse)
async def get_default_index(request: Request):
    # 每次动态获取角色数据
    _, her_name, _, lanlan_basic_config, _, _, _, _, _, _ = get_character_data()
    # 获取live2d字段
    live2d = lanlan_basic_config.get(her_name, {}).get('live2d', 'mao_pro')
    # 查找所有模型
    models = find_models()
    # 根据live2d字段查找对应的model path
    model_path = next((m["path"] for m in models if m["name"] == live2d), f"/static/{live2d}/{live2d}.model3.json")
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        "lanlan_name": her_name,
        "model_path": model_path,
        "focus_mode": False
    })

@app.get("/focus", response_class=HTMLResponse)
async def get_default_focus_index(request: Request):
    # 每次动态获取角色数据
    _, her_name, _, lanlan_basic_config, _, _, _, _, _, _ = get_character_data()
    # 获取live2d字段
    live2d = lanlan_basic_config.get(her_name, {}).get('live2d', 'mao_pro')
    # 查找所有模型
    models = find_models()
    # 根据live2d字段查找对应的model path
    model_path = next((m["path"] for m in models if m["name"] == live2d), f"/static/{live2d}/{live2d}.model3.json")
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        "lanlan_name": her_name,
        "model_path": model_path,
        "focus_mode": True
    })

@app.get("/api/preferences")
async def get_preferences():
    """获取用户偏好设置"""
    preferences = load_user_preferences()
    return preferences

@app.post("/api/preferences")
async def save_preferences(request: Request):
    """保存用户偏好设置"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 验证偏好数据
        if not validate_model_preferences(data):
            return {"success": False, "error": "偏好数据格式无效"}
        
        # 更新偏好
        if update_model_preferences(data['model_path'], data['position'], data['scale']):
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/models")
async def get_models():
    """
    API接口，调用扫描函数并以JSON格式返回找到的模型列表。
    """
    models = find_models()
    return models

@app.post("/api/preferences/set-preferred")
async def set_preferred_model(request: Request):
    """设置首选模型"""
    try:
        data = await request.json()
        if not data or 'model_path' not in data:
            return {"success": False, "error": "无效的数据"}
        
        if move_model_to_top(data['model_path']):
            return {"success": True, "message": "首选模型已更新"}
        else:
            return {"success": False, "error": "模型不存在或更新失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/config/core_api")
async def get_core_config():
    """获取核心配置（API Key）"""
    try:
        # 尝试从core_config.json读取
        try:
            with open('./config/core_config.json', 'r', encoding='utf-8') as f:
                core_cfg = json.load(f)
                api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前内存中的CORE_API_KEY
            api_key = CORE_API_KEY
        
        return {
            "api_key": api_key,
            "coreApi": core_cfg.get('coreApi', 'qwen'),
            "assistApi": core_cfg.get('assistApi', 'qwen'),
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', ''),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', ''),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', ''),
            "success": True
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/config/core_api")
async def update_core_config(request: Request):
    """更新核心配置（API Key）"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        if 'coreApiKey' not in data:
            return {"success": False, "error": "缺少coreApiKey字段"}
        
        api_key = data['coreApiKey']
        if api_key is None:
            return {"success": False, "error": "API Key不能为null"}
        
        if not isinstance(api_key, str):
            return {"success": False, "error": "API Key必须是字符串类型"}
        
        api_key = api_key.strip()
        if not api_key:
            return {"success": False, "error": "API Key不能为空"}
        
        # 保存到core_config.json
        core_cfg = {"coreApiKey": api_key}
        if 'coreApi' in data:
            core_cfg['coreApi'] = data['coreApi']
        if 'assistApi' in data:
            core_cfg['assistApi'] = data['assistApi']
        if 'assistApiKeyQwen' in data:
            core_cfg['assistApiKeyQwen'] = data['assistApiKeyQwen']
        if 'assistApiKeyOpenai' in data:
            core_cfg['assistApiKeyOpenai'] = data['assistApiKeyOpenai']
        if 'assistApiKeyGlm' in data:
            core_cfg['assistApiKeyGlm'] = data['assistApiKeyGlm']
        with open('./config/core_config.json', 'w', encoding='utf-8') as f:
            json.dump(core_cfg, f, indent=2, ensure_ascii=False)
        
        return {"success": True, "message": "API Key已保存"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.on_event("startup")
async def startup_event():
    global sync_process
    logger.info("Starting sync connector processes")
    # 启动同步连接器进程
    for k in sync_process:
        if sync_process[k] is None:
            sync_process[k] = Process(
                target=cross_server.sync_connector_process,
                args=(sync_message_queue[k], sync_shutdown_event[k], k, "ws://0.0.0.0:8002", {'bullet': False, 'monitor': False})
            )
            sync_process[k].start()
            logger.info(f"同步连接器进程已启动 (PID: {sync_process[k].pid})")
    
    # 如果启用了浏览器模式，在服务器启动完成后打开浏览器
    current_config = get_start_config()
    print(f"启动配置: {current_config}")
    if current_config['browser_mode_enabled']:
        import threading
        
        def launch_browser_delayed():
            # 等待一小段时间确保服务器完全启动
            import time
            time.sleep(1)
            # 从 app.state 获取配置
            config = get_start_config()
            url = f"http://127.0.0.1:{MAIN_SERVER_PORT}/{config['browser_page']}"
            try:
                webbrowser.open(url)
                logger.info(f"服务器启动完成，已打开浏览器访问: {url}")
            except Exception as e:
                logger.error(f"打开浏览器失败: {e}")
        
        # 在独立线程中启动浏览器
        t = threading.Thread(target=launch_browser_delayed, daemon=True)
        t.start()


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    logger.info("Shutting down sync connector processes")
    # 关闭同步服务器连接
    for k in sync_process:
        if sync_process[k] is not None:
            sync_shutdown_event[k].set()
            sync_process[k].join(timeout=3)  # 等待进程正常结束
            if sync_process[k].is_alive():
                sync_process[k].terminate()  # 如果超时，强制终止
    logger.info("同步连接器进程已停止")
    
    # 向memory_server发送关闭信号
    try:
        import requests
        from config import MEMORY_SERVER_PORT
        shutdown_url = f"http://0.0.0.0:{MEMORY_SERVER_PORT}/shutdown"
        response = requests.post(shutdown_url, timeout=2)
        if response.status_code == 200:
            logger.info("已向memory_server发送关闭信号")
        else:
            logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
    except Exception as e:
        logger.warning(f"向memory_server发送关闭信号时出错: {e}")


@app.websocket("/ws/{lanlan_name}")
async def websocket_endpoint(websocket: WebSocket, lanlan_name: str):
    await websocket.accept()
    this_session_id = uuid.uuid4()
    async with lock:
        global session_id
        session_id[lanlan_name] = this_session_id
    logger.info(f"⭐websocketWebSocket accepted: {websocket.client}, new session id: {session_id[lanlan_name]}, lanlan_name: {lanlan_name}")

    try:
        while True:
            data = await websocket.receive_text()
            if session_id[lanlan_name] != this_session_id:
                await session_manager[lanlan_name].send_status(f"切换至另一个终端...")
                await websocket.close()
                break
            message = json.loads(data)
            action = message.get("action")
            # logger.debug(f"WebSocket received action: {action}") # Optional debug log

            if action == "start_session":
                session_manager[lanlan_name].active_session_is_idle = False
                input_type = message.get("input_type")
                if input_type in ['audio', 'screen', 'camera']:
                    asyncio.create_task(session_manager[lanlan_name].start_session(websocket, message.get("new_session", False)))
                else:
                    await session_manager[lanlan_name].send_status(f"Invalid input type: {input_type}")

            elif action == "stream_data":
                asyncio.create_task(session_manager[lanlan_name].stream_data(message))

            elif action == "end_session":
                session_manager[lanlan_name].active_session_is_idle = False
                asyncio.create_task(session_manager[lanlan_name].end_session())

            elif action == "pause_session":
                session_manager[lanlan_name].active_session_is_idle = True

            else:
                logger.warning(f"Unknown action received: {action}")
                await session_manager[lanlan_name].send_status(f"Unknown action: {action}")

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        error_message = f"WebSocket handler error: {e}"
        logger.error(f"💥 {error_message}")
        logger.error(traceback.format_exc())
        try:
            await session_manager[lanlan_name].send_status(f"Server error: {e}")
        except:
            pass
    finally:
        logger.info(f"Cleaning up WebSocket resources: {websocket.client}")
        await session_manager[lanlan_name].cleanup()

@app.get("/l2d", response_class=HTMLResponse)
async def get_l2d_manager(request: Request, lanlan_name: str = ""):
    """渲染Live2D模型管理器页面"""
    return templates.TemplateResponse("templates/l2d_manager.html", {
        "request": request,
        "lanlan_name": lanlan_name
    })

@app.get('/chara_manager', response_class=HTMLResponse)
async def chara_manager(request: Request):
    """渲染主控制页面"""
    return templates.TemplateResponse('templates/chara_manager.html', {"request": request})

@app.get('/voice_clone', response_class=HTMLResponse)
async def voice_clone_page(request: Request, lanlan_name: str = ""):
    return templates.TemplateResponse("templates/voice_clone.html", {"request": request, "lanlan_name": lanlan_name})

@app.get("/api_key", response_class=HTMLResponse)
async def api_key_settings(request: Request):
    """API Key 设置页面"""
    return templates.TemplateResponse("templates/api_key_settings.html", {
        "request": request
    })

@app.get('/api/characters')
async def get_characters():
    return JSONResponse(content=load_characters())

@app.get('/api/characters/current_catgirl')
async def get_current_catgirl():
    """获取当前使用的猫娘名称"""
    characters = load_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return JSONResponse(content={'current_catgirl': current_catgirl})

@app.post('/api/characters/current_catgirl')
async def set_current_catgirl(request: Request):
    """设置当前使用的猫娘"""
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''
    
    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    
    characters = load_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)
    
    characters['当前猫娘'] = catgirl_name
    save_characters(characters)
    return {"success": True}

@app.post('/api/characters/master')
async def update_master(request: Request):
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    characters = load_characters()
    characters['主人'] = {k: v for k, v in data.items() if v}
    save_characters(characters)
    return {"success": True}

@app.post('/api/characters/catgirl')
async def add_catgirl(request: Request):
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    
    characters = load_characters()
    key = data['档案名']
    if key in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '该猫娘已存在'}, status_code=400)
    
    if '猫娘' not in characters:
        characters['猫娘'] = {}
    
    # 创建猫娘数据，只保存非空字段
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名' and v:  # 只保存非空字段
            catgirl_data[k] = v
    
    characters['猫娘'][key] = catgirl_data
    save_characters(characters)
    return {"success": True}

@app.put('/api/characters/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    characters = load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    # 只更新前端传来的字段，未传字段保留原值，且不允许通过此接口修改 system_prompt
    removed_fields = []
    for k, v in characters['猫娘'][name].items():
        if k not in data and k not in ('档案名', 'system_prompt', 'voice_id', 'live2d'):
            removed_fields.append(k)
    for k in removed_fields:
        characters['猫娘'][name].pop(k)
    for k, v in data.items():
        if k not in ('档案名') and v:
            characters['猫娘'][name][k] = v
    save_characters(characters)
    return {"success": True}

@app.put('/api/characters/catgirl/l2d/{name}')
async def update_catgirl_l2d(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    characters = load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    if 'live2d' in data:
        characters['猫娘'][name]['live2d'] = data['live2d']
    save_characters(characters)
    return {"success": True}

@app.put('/api/characters/catgirl/voice_id/{name}')
async def update_catgirl_voice_id(name: str, request: Request):
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    characters = load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    if 'voice_id' in data:
        characters['猫娘'][name]['voice_id'] = data['voice_id']
    save_characters(characters)
    return {"success": True}

@app.post('/api/characters/clear_voice_ids')
async def clear_voice_ids():
    """清除所有角色的本地Voice ID记录"""
    try:
        characters = load_characters()
        cleared_count = 0
        
        # 清除所有猫娘的voice_id
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if 'voice_id' in characters['猫娘'][name] and characters['猫娘'][name]['voice_id']:
                    characters['猫娘'][name]['voice_id'] = ''
                    cleared_count += 1
        
        save_characters(characters)
        
        return JSONResponse({
            'success': True, 
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False, 
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)

@app.post('/api/tmpfiles_voice_clone')
async def tmpfiles_voice_clone(file: UploadFile = File(...), prefix: str = Form(...)):
    import os
    temp_path = f'tmp_{file.filename}'
    with open(temp_path, 'wb') as f:
        f.write(await file.read())
    tmp_url = None
    try:
        # 1. 上传到 tmpfiles.org
        with open(temp_path, 'rb') as f2:
            files = {'file': (file.filename, f2)}
            resp = requests.post('https://tmpfiles.org/api/v1/upload', files=files, timeout=30)
            data = resp.json()
            if not data or 'data' not in data or 'url' not in data['data']:
                return JSONResponse({'error': '上传到 tmpfiles.org 失败'}, status_code=500)
            page_url = data['data']['url']
            # 替换域名部分为直链
            if page_url.startswith('http://tmpfiles.org/'):
                tmp_url = page_url.replace('http://tmpfiles.org/', 'http://tmpfiles.org/dl/', 1)
            elif page_url.startswith('https://tmpfiles.org/'):
                tmp_url = page_url.replace('https://tmpfiles.org/', 'https://tmpfiles.org/dl/', 1)
            else:
                tmp_url = page_url  # 兜底
        # 2. 用直链注册音色
        dashscope.api_key = AUDIO_API_KEY
        service = VoiceEnrollmentService()
        target_model = "cosyvoice-v2"
        voice_id = service.create_voice(target_model=target_model, prefix=prefix, url=tmp_url)
        return JSONResponse({
            'voice_id': voice_id,
            'request_id': service.get_last_request_id(),
            'file_url': tmp_url
        })
    except Exception as e:
        return JSONResponse({'error': str(e), 'file_url': tmp_url}, status_code=500)
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass

@app.delete('/api/characters/catgirl/{name}')
async def delete_catgirl(name: str):
    characters = load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    del characters['猫娘'][name]
    save_characters(characters)
    return {"success": True}

@app.post('/api/beacon/shutdown')
async def beacon_shutdown():
    """Beacon API for graceful server shutdown"""
    try:
        # 从 app.state 获取配置
        current_config = get_start_config()
        # Only respond to beacon if server was started with --open-browser
        if current_config['browser_mode_enabled']:
            logger.info("收到beacon信号，准备关闭服务器...")
            # Schedule server shutdown
            asyncio.create_task(shutdown_server_async())
            return {"success": True, "message": "服务器关闭信号已接收"}
    except Exception as e:
        logger.error(f"Beacon处理错误: {e}")
        return {"success": False, "error": str(e)}

async def shutdown_server_async():
    """异步关闭服务器"""
    try:
        # Give a small delay to allow the beacon response to be sent
        await asyncio.sleep(0.5)
        logger.info("正在关闭服务器...")
        
        # 向memory_server发送关闭信号
        try:
            import requests
            from config import MEMORY_SERVER_PORT
            shutdown_url = f"http://0.0.0.0:{MEMORY_SERVER_PORT}/shutdown"
            response = requests.post(shutdown_url, timeout=1)
            if response.status_code == 200:
                logger.info("已向memory_server发送关闭信号")
            else:
                logger.warning(f"向memory_server发送关闭信号失败，状态码: {response.status_code}")
        except Exception as e:
            logger.warning(f"向memory_server发送关闭信号时出错: {e}")
        
        # Signal the server to stop
        current_config = get_start_config()
        if current_config['server'] is not None:
            current_config['server'].should_exit = True
    except Exception as e:
        logger.error(f"关闭服务器时出错: {e}")

@app.post('/api/characters/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    data = await request.json()
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)
    characters = load_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)
    # 重命名
    characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
    save_characters(characters)
    return {"success": True}

@app.post('/api/characters/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """解除猫娘的声音注册"""
    try:
        characters = load_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
        
        # 检查是否已有voice_id
        if not characters['猫娘'][name].get('voice_id'):
            return JSONResponse({'success': False, 'error': '该猫娘未注册声音'}, status_code=400)
        
        # 删除voice_id字段
        if 'voice_id' in characters['猫娘'][name]:
            characters['猫娘'][name].pop('voice_id')
        save_characters(characters)
        
        logger.info(f"已解除猫娘 '{name}' 的声音注册")
        return {"success": True, "message": "声音注册已解除"}
        
    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)

@app.get('/api/memory/recent_files')
async def get_recent_files():
    """获取 memory/store 下所有 recent*.json 文件名列表"""
    files = glob.glob('memory/store/recent*.json')
    file_names = [os.path.basename(f) for f in files]
    return {"files": file_names}

@app.get('/api/memory/review_config')
async def get_review_config():
    """获取记忆审阅配置"""
    try:
        config_path = './config/core_config.json'
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
                # 如果配置中没有这个键，默认返回True（开启）
                return {"enabled": config_data.get('recent_memory_auto_review', True)}
        else:
            # 如果配置文件不存在，默认返回True（开启）
            return {"enabled": True}
    except Exception as e:
        logger.error(f"读取记忆审阅配置失败: {e}")
        return {"enabled": True}

@app.post('/api/memory/review_config')
async def update_review_config(request: Request):
    """更新记忆审阅配置"""
    try:
        data = await request.json()
        enabled = data.get('enabled', True)
        
        config_path = './config/core_config.json'
        config_data = {}
        
        # 读取现有配置
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
        
        # 更新配置
        config_data['recent_memory_auto_review'] = enabled
        
        # 保存配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"记忆审阅配置已更新: enabled={enabled}")
        return {"success": True, "enabled": enabled}
    except Exception as e:
        logger.error(f"更新记忆审阅配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.get('/api/memory/recent_file')
async def get_recent_file(filename: str):
    """获取指定 recent*.json 文件内容"""
    file_path = os.path.join('memory/store', filename)
    if not (filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    if not os.path.exists(file_path):
        return JSONResponse({"success": False, "error": "文件不存在"}, status_code=404)
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return {"content": content}

@app.get('/api/live2d/emotion_mapping/{model_name}')
async def get_emotion_mapping(model_name: str):
    """获取指定Live2D模型的情感映射配置"""
    try:
        # 动态加载Live2D情感映射配置文件
        mapping_file_path = os.path.join(os.path.dirname(__file__), 'config', 'live2d_emotion_mapping.json')
        
        try:
            with open(mapping_file_path, 'r', encoding='utf-8') as f:
                emotion_mapping_data = json.load(f)
        except FileNotFoundError:
            logger.info(f"未找到Live2D映射配置文件: {mapping_file_path}，使用默认配置。")
            emotion_mapping_data = {}
        except Exception as e:
            logger.error(f"💥 读取Live2D映射配置文件出错: {e}，使用默认配置。")
            emotion_mapping_data = {}
        
        # 获取指定模型的映射配置
        mapping = emotion_mapping_data.get(model_name, {})
        return {"success": True, "mapping": mapping}
    except Exception as e:
        logger.error(f"获取情感映射配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.get('/api/live2d/emotion_mapping')
async def get_all_emotion_mappings():
    """获取所有Live2D模型的情感映射配置"""
    try:
        # 动态加载Live2D情感映射配置文件
        mapping_file_path = os.path.join(os.path.dirname(__file__), 'config', 'live2d_emotion_mapping.json')
        
        try:
            with open(mapping_file_path, 'r', encoding='utf-8') as f:
                emotion_mapping_data = json.load(f)
        except FileNotFoundError:
            logger.info(f"未找到Live2D映射配置文件: {mapping_file_path}，使用默认配置。")
            emotion_mapping_data = {}
        except Exception as e:
            logger.error(f"💥 读取Live2D映射配置文件出错: {e}，使用默认配置。")
            emotion_mapping_data = {}
        
        return {"success": True, "mappings": emotion_mapping_data}
    except Exception as e:
        logger.error(f"获取所有情感映射配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.post('/api/live2d/emotion_mapping')
async def update_emotion_mapping(request: Request):
    """更新Live2D模型的情感映射配置"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "请求体不能为空"}
        
        # 动态加载Live2D情感映射配置文件
        mapping_file_path = os.path.join(os.path.dirname(__file__), 'config', 'live2d_emotion_mapping.json')
        
        # 读取现有配置
        try:
            with open(mapping_file_path, 'r', encoding='utf-8') as f:
                emotion_mapping_data = json.load(f)
        except FileNotFoundError:
            emotion_mapping_data = {}
        except Exception as e:
            logger.error(f"💥 读取Live2D映射配置文件出错: {e}，使用空配置。")
            emotion_mapping_data = {}
        
        # 更新配置
        emotion_mapping_data.update(data)
        
        # 保存配置
        with open(mapping_file_path, 'w', encoding='utf-8') as f:
            json.dump(emotion_mapping_data, f, ensure_ascii=False, indent=2)
        
        logger.info("Live2D情感映射配置已更新")
        return {"success": True, "message": "配置已更新"}
    except Exception as e:
        logger.error(f"更新情感映射配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.delete('/api/live2d/emotion_mapping/{model_name}')
async def delete_emotion_mapping(model_name: str):
    """删除指定Live2D模型的情感映射配置"""
    try:
        # 动态加载Live2D情感映射配置文件
        mapping_file_path = os.path.join(os.path.dirname(__file__), 'config', 'live2d_emotion_mapping.json')
        
        # 读取现有配置
        try:
            with open(mapping_file_path, 'r', encoding='utf-8') as f:
                emotion_mapping_data = json.load(f)
        except FileNotFoundError:
            return {"success": False, "error": "配置文件不存在"}
        except Exception as e:
            logger.error(f"💥 读取Live2D映射配置文件出错: {e}")
            return {"success": False, "error": str(e)}
        
        # 删除指定模型配置
        if model_name in emotion_mapping_data:
            del emotion_mapping_data[model_name]
            
            # 保存配置
            with open(mapping_file_path, 'w', encoding='utf-8') as f:
                json.dump(emotion_mapping_data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"已删除模型 {model_name} 的情感映射配置")
            return {"success": True, "message": f"已删除模型 {model_name} 的配置"}
        else:
            return {"success": False, "error": f"模型 {model_name} 不存在"}
    except Exception as e:
        logger.error(f"删除情感映射配置失败: {e}")
        return {"success": False, "error": str(e)}

@app.post('/api/memory/recent_file/save')
async def save_recent_file(request: Request):
    import os, json
    data = await request.json()
    filename = data.get('filename')
    chat = data.get('chat')
    file_path = os.path.join('memory/store', filename)
    if not (filename and filename.startswith('recent') and filename.endswith('.json')):
        return JSONResponse({"success": False, "error": "文件名不合法"}, status_code=400)
    arr = []
    for msg in chat:
        t = msg.get('role')
        text = msg.get('text', '')
        arr.append({
            "type": t,
            "data": {
                "content": text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": t,
                "name": None,
                "id": None,
                "example": False,
                **({"tool_calls": [], "invalid_tool_calls": [], "usage_metadata": None} if t == "ai" else {})
            }
        })
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post('/api/emotion/analysis')
async def emotion_analysis(request: Request):
    try:
        data = await request.json()
        if not data or 'text' not in data:
            return {"error": "请求体中必须包含text字段"}
        
        text = data['text']
        api_key = data.get('api_key')
        model = data.get('model')
        
        # 使用参数或默认配置
        api_key = api_key or OPENROUTER_API_KEY
        model = model or EMOTION_MODEL
        
        if not api_key:
            return {"error": "API密钥未提供且配置中未设置默认密钥"}
        
        if not model:
            return {"error": "模型名称未提供且配置中未设置默认模型"}
        
        # 创建异步客户端
        client = AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_URL)
        
        # 构建请求消息
        messages = [
            {
                "role": "system", 
                "content": emotion_analysis_prompt
            },
            {
                "role": "user", 
                "content": text
            }
        ]
        
        # 异步调用模型
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=100,
            extra_body={"enable_thinking": False}
        )
        
        # 解析响应
        result_text = response.choices[0].message.content.strip()
        
        # 尝试解析JSON响应
        try:
            import json
            result = json.loads(result_text)
            # 获取emotion和confidence
            emotion = result.get("emotion", "neutral")
            confidence = result.get("confidence", 0.5)
            
            # 当confidence小于0.3时，自动将emotion设置为neutral
            if confidence < 0.3:
                emotion = "neutral"
            
            return {
                "emotion": emotion,
                "confidence": confidence
            }
        except json.JSONDecodeError:
            # 如果JSON解析失败，返回简单的情感判断
            return {
                "emotion": "neutral",
                "confidence": 0.5
            }
            
    except Exception as e:
        logger.error(f"情感分析失败: {e}")
        return {
            "error": f"情感分析失败: {str(e)}",
            "emotion": "neutral",
            "confidence": 0.0
        }

@app.get('/memory_browser', response_class=HTMLResponse)
async def memory_browser(request: Request):
    return templates.TemplateResponse('templates/memory_browser.html', {"request": request})

@app.get("/focus/{lanlan_name}", response_class=HTMLResponse)
async def get_focus_index(request: Request, lanlan_name: str):
    # 每次动态获取角色数据
    _, _, _, lanlan_basic_config, _, _, _, _, _, _ = get_character_data()
    # 获取live2d字段
    live2d = lanlan_basic_config.get(lanlan_name, {}).get('live2d', 'mao_pro')
    # 查找所有模型
    models = find_models()
    # 根据live2d字段查找对应的model path
    model_path = next((m["path"] for m in models if m["name"] == live2d), f"/static/{live2d}/{live2d}.model3.json")
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        "lanlan_name": lanlan_name,
        "model_path": model_path,
        "focus_mode": True
    })

@app.get("/{lanlan_name}", response_class=HTMLResponse)
async def get_index(request: Request, lanlan_name: str):
    # 每次动态获取角色数据
    _, _, _, lanlan_basic_config, _, _, _, _, _, _ = get_character_data()
    # 获取live2d字段
    live2d = lanlan_basic_config.get(lanlan_name, {}).get('live2d', 'mao_pro')
    # 查找所有模型
    models = find_models()
    # 根据live2d字段查找对应的model path
    model_path = next((m["path"] for m in models if m["name"] == live2d), f"/static/{live2d}/{live2d}.model3.json")
    return templates.TemplateResponse("templates/index.html", {
        "request": request,
        "lanlan_name": lanlan_name,
        "model_path": model_path,
        "focus_mode": False
    })


# --- Run the Server ---
if __name__ == "__main__":
    import uvicorn
    import argparse
    import os
    import signal
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--open-browser",   action="store_true",
                        help="启动后是否打开浏览器并监控它")
    parser.add_argument("--page",           type=str, default="",
                        choices=["index", "chara_manager", "api_key"],
                        help="要打开的页面路由（不含域名和端口）")
    args = parser.parse_args()

    logger.info("--- Starting FastAPI Server ---")
    # Use os.path.abspath to show full path clearly
    logger.info(f"Serving static files from: {os.path.abspath('static')}")
    logger.info(f"Serving index.html from: {os.path.abspath('templates/index.html')}")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT} (or your network IP:{MAIN_SERVER_PORT})")
    logger.info("-----------------------------")

    # 1) 配置 UVicorn
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=MAIN_SERVER_PORT,
        log_level="info",
        loop="asyncio",
        reload=False,
    )
    server = uvicorn.Server(config)
    
    # Set browser mode flag if --open-browser is used
    if args.open_browser:
        # 使用 FastAPI 的 app.state 来管理配置
        start_config = {
            "browser_mode_enabled": True,
            "browser_page": args.page if args.page!='index' else '',
            'server': server
        }
        set_start_config(start_config)
    else:
        # 设置默认配置
        start_config = {
            "browser_mode_enabled": False,
            "browser_page": "",
            'server': server
        }
        set_start_config(start_config)

    print(f"启动配置: {get_start_config()}")

    # 2) 定义服务器关闭回调
    def shutdown_server():
        logger.info("收到浏览器关闭信号，正在关闭服务器...")
        os.kill(os.getpid(), signal.SIGTERM)

    # 4) 启动服务器（阻塞，直到 server.should_exit=True）
    logger.info("--- Starting FastAPI Server ---")
    logger.info(f"Access UI at: http://127.0.0.1:{MAIN_SERVER_PORT}/{args.page}")
    
    try:
        server.run()
    finally:
        logger.info("服务器已关闭")