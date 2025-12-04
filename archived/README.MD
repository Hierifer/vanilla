# vanilla :seedling:  
一个语音原生的全场景 AI 伙伴（低门槛 / 低延迟 / 多端同步）

<div align="center">

![vanilla Logo](assets/xiaoba_logo.jpg)

[English Version](docs/README_en.md)

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active-brightgreen.svg)]()
[![百度云](https://custom-icon-badges.demolab.com/badge/百度云-Link-4169E1?style=flat&logo=baidunetdisk)](https://pan.baidu.com/s/1qb9XVV94c2FwhIeQO2De5A?pwd=kuro)
[![QQ群](https://custom-icon-badges.demolab.com/badge/QQ群-1048307485-00BFFF?style=flat&logo=tencent-qq)](https://qm.qq.com/q/mxDoz0TnGg)

**开箱即用 · 极简配置 · 我奶奶都能 3 分钟跑起来的全场景语音 AI 伙伴**

*Say hello to vanilla.*

</div>

---

## 项目简介
vanilla 是一个新手友好、开箱即用的多模态（听觉 / 视觉 / 工具调用 / 多端同步）实时 AI 伙伴。核心目标：

1. **极致低延迟**：语音交互优先，一切设计围绕降低首 token & 流式语音延迟；任何耗时逻辑不得阻塞对话主循环。
2. **全场景同步**：同一只 vanilla 可同时出现在 PC / 手机 / 可穿戴设备 / 多显示器上，行为与状态保持一致。
3. **轻量化可维护**：只引入真正改善体验的组件，避免繁冗配置与插件堆砌。

### 技术路线（当前实现）
- 后端：FastAPI + OpenAI Realtime 兼容协议（跨服务商）
- 实时会话：多进程 + 内存路由（memory_server / main_server 协同）
- 记忆系统：短期记忆 / 语义摘要 / 时间索引（见 `memory/`）
- 前端：原生 H5 + JS，支持 Live2D（可 Electron / PWA 包装）

---
## 快速开始（开发者）

```bash
# 1. 创建 Python 3.12 虚拟环境（示例）
python3.12 -m venv venv && source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 初始化 API 配置
cp config/api_template.py config/api.py  # 修改其中的 API Key / 模型等

# 4. 启动记忆服务（建议单独终端）
python memory_server.py

# 5. 启动主服务
python main_server.py

# 6. 访问（默认）
http://0.0.0.0:48911
```

> 一键包 / 发行版用户：后续将提供预打包脚本（TODO）。

### 必要配置
在 `config/api.py` 中填写：
- CORE_API_KEY / CORE_MODEL（核心实时多模态模型，如 Qwen-Omni-Realtime / GLM-Realtime / GPT-4o-Realtime）
- AUDIO_API_KEY（可选：语音克隆 / TTS）
- 其他：记忆、情绪模型、OpenRouter 兼容入口等

---
## 角色与人设管理

访问：`/chara_manager`（示例：`http://0.0.0.0:48911/chara_manager`）

- 初始角色默认名称可直接修改；尽量保持核心设定精炼，避免上下文膨胀导致延迟增加。
- 进阶设置包含：
  - Live2D 模型：将模型目录置于 `static/` 下；通过界面管理与拖拽定位。
  - 自定义声音：准备 ~15 秒干净语音，进入语音设置页面上传即完成克隆。
  - system_prompt：不建议随意重写；如需实验可在此完全替换系统指令。

---
## API / 模型切换
访问：`/api_key` 切换核心实时模型与辅助（记忆 / 语音）服务商。当前支持 OpenAI Realtime 兼容协议的多家模型（Qwen / GLM / GPT-4o 等）。

---
## 记忆系统
访问：`/memory_browser` 审阅：
- 近期短期记忆（recent）
- 语义聚合摘要（semantic）
- 时间线索（timeindex）

可用于校对幻觉、缓解复读、纠正角色认知。

核心模块：`memory/recent.py` / `memory/semantic.py` / `memory/timeindex.py`。

---
## 目录结构速览
```
main_server.py        # 主交互服务（Web / 实时会话）
memory_server.py      # 记忆与状态服务
main_helper/          # 会话管理 & 实时流处理
memory/               # 记忆体系实现
static/               # 前端静态资源 + Live2D 模型
templates/            # Jinja2 模板页
config/               # API & Prompt & 模型配置
utils/                # 前端定位 / 音频工具 / 偏好
```

---
## 设计要点
| 目标 | 说明 |
|------|------|
| 低延迟 | 控制上下文体积 + 流式增量拼接 + 避免阻塞工具调用 |
| 多端同步 | 利用会话管理器广播状态（见 `core.LLMSessionManager`） |
| 内存分层 | 近期 -> 语义摘要 -> 时间索引，减少上下文冗余 |
| 可替换性 | API 层抽象，支持不同 Realtime 服务商切换 |

---
## TODO（节选）
A. 高优先级
1. 工具调用 / MCP 协议增强（异步非阻塞执行）
2. Live2D 表情 / 动作精细控制

B. 中优先级
1. vanilla 网络（多实例互通）
2. 移动端链路优化（直连实时模型 + 远程记忆）
3. Memory Server MCP 化（跨生态复用）
4. 外部软件接入（QQ / cursor 等作为工具层）

---
## 常见问答（Q&A 摘要）
> 为什么默认推荐某些模型？
实时多模态链路 + 首 token 延迟是优先指标。

> 感觉模型“变笨”？
项目只负责调度与上下文组织；智能水平取决于所选模型。可在 `config/api.py` 替换更高级模型（如 GPT-4o 系列）。

> Live2D 口型不同步？
已兼容两种主流口型同步形式；多数问题来自模型资源自身。

> 是否支持工具 / MCP？
协议层已预留；受制于当前国内部分实时接口暂不全量开放，后续跟进。

> 支持哪些 Realtime 模型？
Qwen-Omni-Realtime / GLM-Realtime / GPT-4o-Realtime（以及未来兼容的同类 OpenAI Realtime 协议实现）。

---
## 贡献
欢迎 PR / Issue。建议：
1. 描述问题 / 改动动机
2. 提供最小复现上下文
3. 避免引入重量依赖

---
## 致谢
感谢早期内测 / 反馈的所有贡献者与提供素材的朋友们。项目在持续演进，期待你的参与。

---
## 许可
本项目以 MIT 协议开源，详见 `LICENSE`。

---
如果你读到这里，说明你可能已经准备好让 vanilla 陪你继续探索接下来的可能性。祝使用愉快。