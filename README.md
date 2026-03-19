# Ollama2API

> Ollama 后端聚合网关 — 兼容 OpenAI API，多节点负载均衡，自动发现与管理

将多个 Ollama 实例聚合为统一的 OpenAI 兼容 API，支持智能负载均衡、健康检查、节点扫描发现和 Web 管理后台。

## 特性

- **OpenAI 兼容** — `/v1/chat/completions` + `/v1/models`，可直接对接 ChatGPT 前端、Cursor 等工具
- **多节点负载均衡** — 基于延迟、成功率、故障次数的加权评分调度
- **自动健康检查** — 定时探测节点状态，故障自动冷却与恢复
- **节点扫描发现** — 支持 masscan 高速扫描 + 纯 Python 回退，批量发现 Ollama 实例
- **代理支持** — 可选集成 Xray，通过 SOCKS5/HTTP 代理访问节点
- **API Key 管理** — 可选鉴权，支持批量创建与用量统计
- **Web 管理后台** — 节点管理、扫描控制、密钥管理、配置修改、日志查看
- **AI 运维助手** — 内置 AI Chat，自然语言管理系统
- **流式响应** — 完整 SSE 流式输出支持
- **零依赖存储** — JSON 文件存储，无需数据库

## 快速开始

### Docker 部署（推荐）

```bash
git clone https://github.com/yourname/ollama2api.git
cd ollama2api
docker-compose up -d
```

访问 `http://localhost:8001/admin` 进入管理后台。

> **安全提示**：首次部署后请立即在管理后台修改默认管理员密码。

### 本地运行

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

服务启动于 `http://localhost:8001`。

### 环境要求

| 依赖 | 必须 | 说明 |
|------|------|------|
| Python 3.10+ | 是 | 运行环境 |
| masscan | 否 | 高速端口扫描，未安装时回退纯 Python |
| Xray | 否 | 代理支持，不需代理可忽略 |

## 配置

运行时配置存储在 `data/config.json`，支持通过管理后台热修改：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `request_timeout` | 300 | 请求超时（秒） |
| `connect_timeout` | 10 | 连接超时（秒） |
| `health_check_interval` | 300 | 健康检查间隔（秒） |
| `max_retries` | 3 | 请求最大重试次数 |
| `cooldown_threshold` | 3 | 连续失败多少次后冷却 |
| `cooldown_duration` | 300 | 冷却时长（秒） |
| `scanner_concurrency` | 50 | 扫描并发数 |
| `masscan_rate` | 5000 | masscan 发包速率 |
| `cleanup_offline_hours` | 24 | 离线节点自动清理阈值（小时） |

## API

完全兼容 OpenAI Chat Completions API：

```bash
# 聊天补全（流式）
curl http://localhost:8001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{"model": "your-model", "messages": [{"role": "user", "content": "Hello"}], "stream": true}'

# 模型列表
curl http://localhost:8001/v1/models

# 健康检查
curl http://localhost:8001/health
```

> 未配置 API Key 时无需 `Authorization` 头。

## 批量扫描

独立扫描脚本 `batch_scan.py` 用于批量发现 Ollama 节点：

```bash
# 1. 创建扫描范围文件（参考 scan_ranges.example.json）
cp scan_ranges.example.json scan_ranges.json

# 2. 设置环境变量并运行
export ADMIN_PASSWORD="your-password"
python3 batch_scan.py                        # 默认读 scan_ranges.json
python3 batch_scan.py my_ranges.json         # 指定范围文件

# 3. 后台运行
nohup python3 -u batch_scan.py > scan.log 2>&1 &
```

> 首次启动时，服务会自动从 `data/hit_ips.txt` 导入种子节点。

## 项目结构

```
ollama2api/
├── main.py                  # 应用入口
├── batch_scan.py            # 批量扫描脚本
├── scan_ranges.example.json # 扫描范围示例
├── app/
│   ├── api/
│   │   ├── admin.py         # 管理后台 API
│   │   ├── proxy.py         # 代理管理 API
│   │   └── v1/
│   │       ├── chat.py      # 聊天补全接口
│   │       └── models.py    # 模型列表接口
│   ├── core/
│   │   ├── auth.py          # 认证中间件
│   │   ├── config.py        # 配置管理
│   │   ├── constants.py     # 常量（目标模型列表）
│   │   ├── logger.py        # 日志
│   │   └── storage.py       # JSON 文件存储
│   ├── models/
│   │   └── openai_models.py # OpenAI 请求/响应模型
│   ├── services/
│   │   ├── api_keys.py      # API Key 管理
│   │   ├── backend_manager.py # 节点池 + 负载均衡
│   │   ├── health_checker.py  # 健康检查
│   │   ├── ollama_client.py   # Ollama 客户端（流式/非流式）
│   │   ├── proxy_manager.py   # 代理管理
│   │   ├── request_logger.py  # 请求日志
│   │   ├── request_stats.py   # 请求统计
│   │   └── scanner.py         # 节点扫描服务
│   └── template/
│       └── login.html       # 登录页
├── data/                    # 运行时数据（自动生成，勿提交）
├── logs/                    # 日志目录
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 技术栈

| 组件 | 用途 |
|------|------|
| [FastAPI](https://fastapi.tiangolo.com/) | 异步 Web 框架 |
| [Uvicorn](https://www.uvicorn.org/) | ASGI 服务器 |
| [aiohttp](https://docs.aiohttp.org/) | 异步 HTTP 客户端 |
| [Pydantic v2](https://docs.pydantic.dev/) | 数据校验 |
| [uvloop](https://github.com/MagicStack/uvloop) | 高性能事件循环（Linux/macOS） |

## 许可证

[MIT](LICENSE)

## 注意事项

- **首次部署请立即修改默认密码**，通过环境变量 `ADMIN_PASSWORD` 设置或在管理后台修改
- **扫描工具误报**：`batch_scan.py` 和 Docker 镜像中的 masscan 为合法网络扫描工具，部分云服务商的安全策略可能将其标记为恶意软件。如遇误报，可将相关文件加入白名单，或改用纯 Python 扫描模式（不安装 masscan 即自动回退）
- 扫描功能请遵守当地法律法规，仅用于发现自有或已授权的 Ollama 实例

## 免责声明

本项目完全免费开源，仅供学习和研究用途。作者不对使用本项目所产生的任何直接或间接后果承担责任。使用者应自行承担使用风险，并遵守所在地区的法律法规。本项目与作者的其他项目、工作或身份无关。
