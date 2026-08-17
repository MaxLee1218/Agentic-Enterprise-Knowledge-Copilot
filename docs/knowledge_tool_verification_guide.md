# Knowledge Tool HTTP 联调与验证指南

## 1. 适用范围与契约依据

本指南验证 Copilot 通过 HTTPX 调用 Enterprise RAG Engine 的 `GET /health` 和
`POST /ask`，并验证 RAG 来源能够经冻结的 v1.0 `knowledge_search` 契约生成
`DOCUMENT` 类型的 `EvidenceItem`。

客户端严格依据 Enterprise RAG Engine 的
`docs/rag_api_contract.md`：

```http
GET /health
POST /ask
Content-Type: application/json

{"question": "..."}
```

`/ask` 请求体只能包含 `question`；未知请求字段会被 RAG 拒绝。响应必须包含
`answer`、`sources`、`contexts`、`route`、`latency_ms` 和 `rag_trace_id`，并拒绝
Schema 外字段。本文假定 RAG 的 FastAPI 入口为 `app.api:app`；如果 Enterprise RAG
Engine 的 README 给出不同入口，以真实入口为准。

RAG 的问答扩展字段只存在于 `KnowledgeResult` 和独立 Ask CLI。进入工作流时，
`KnowledgeTool` 按冻结 v1.0 设计输出：

```text
matches / match_count / index_snapshot_id / empty_result
```

工具单次时限仍为 10 秒、整体时限仍为 25 秒。生产工作流把每次 HTTP 传输限制为
9 秒且关闭客户端内层重试，由工作流用独立 ToolResult 执行最多三次受审计重试；
独立 CLI 使用 `RAG_TIMEOUT_SECONDS` 和 `RAG_MAX_ATTEMPTS` 的完整配置值。本地企业
Compose 还会在 API 启动前通过一次性 `rag-warmup` 调用真实 `/ask`，避免首个业务任务
承担模型冷启动时间。

## 2. 环境变量

可用配置如下：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `RAG_BASE_URL` | `http://127.0.0.1:8000` | RAG 根地址，尾部 `/` 会被移除 |
| `RAG_TIMEOUT_SECONDS` | `30` | 独立 HTTP 调用超时，必须大于 0 |
| `RAG_MAX_ATTEMPTS` | `3` | HTTP 总尝试次数，包含首次请求，范围 1–3 |
| `RAG_RETRY_BASE_DELAY_SECONDS` | `0.2` | 确定性指数退避基础秒数 |
| `RAG_USER_AGENT` | `agentic-enterprise-knowledge-copilot/0.1.0` | User Agent |
| `RAG_TRACE_HEADER` | `X-Trace-ID` | Trace ID 请求和响应 Header |
| `RUN_LIVE_RAG_TESTS` | 未设置 | 只有值为 `1` 时才运行真实联调测试 |

客户端只自动重试 Timeout、Connection Reset、HTTP 502、503、504。Connection
refused、DNS 错误、其他 4xx、500、非法 JSON、Schema 错误和空 answer 不重试。

独立 Ask CLI 和生产 `KnowledgeTool` 都只向 `/ask` 发送
`{"question":"..."}`。v1.0 Tool 输入中的租户和业务范围仍由 ToolExecutor 前的策略
边界校验，不会作为未知字段发送给 RAG API。

## 3. macOS：使用两个终端

### 3.1 终端 1：启动和直接验证 RAG

```bash
cd ~/projects/Enterprise-RAG-Engine
source .venv/bin/activate
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

在另一个终端先直接检查 Health：

```bash
curl -i http://127.0.0.1:8000/health
```

预期至少得到 HTTP 200 和下列一种响应：

```json
{"status":"ok"}
```

直接检查 Ask：

```bash
curl -X POST "http://127.0.0.1:8000/ask" \
  -H "Content-Type: application/json" \
  -H "X-Trace-ID: manual-rag-test-001" \
  -d '{"question":"What is the supplier quality deviation procedure?"}'
```

预期结构：

```json
{
  "answer": "A non-empty answer",
  "sources": [],
  "contexts": [],
  "route": "rag",
  "latency_ms": 12,
  "rag_trace_id": "manual-rag-test-001"
}
```

### 3.2 终端 2：运行 Copilot

```bash
cd ~/projects/Agentic-Enterprise-Knowledge-Copilot
source .venv/bin/activate
export DATABASE_URL=sqlite:///data/database/enterprise_demo.db
export RAG_BASE_URL=http://127.0.0.1:8000
export RAG_TIMEOUT_SECONDS=30
export RAG_MAX_ATTEMPTS=3
export RAG_RETRY_BASE_DELAY_SECONDS=0.2
```

运行独立 Health Check：

```bash
python scripts/check_rag_health.py
```

指定 Trace ID 并输出 JSON：

```bash
python scripts/check_rag_health.py \
  --trace-id copilot-health-001 \
  --json
```

运行真实 Ask：

```bash
python scripts/ask_knowledge.py \
  --question "What is the supplier quality deviation procedure?"
```

运行机器可读 JSON：

```bash
python scripts/ask_knowledge.py \
  --question "What is the supplier quality deviation procedure?" \
  --json
```

验证 EvidenceItem：

```bash
python scripts/ask_knowledge.py \
  --question "What is the supplier quality deviation procedure?" \
  --show-evidence
```

当 RAG 返回两个合法来源时，预期输出形状如下：

```text
Evidence count: 2

[1]
type: DOCUMENT
source: Supplier Quality Manual.pdf
page: 24
chunk_id: chunk-001
rag_trace_id: copilot-...
```

当 `sources=[]` 时，调用仍成功并显示 `Evidence count: 0`；系统不会伪造来源。

### 3.3 macOS 测试命令

默认离线测试：

```bash
pytest
```

只运行 Knowledge 单元测试：

```bash
pytest tests/unit/knowledge -v
```

运行 CLI 帮助验证：

```bash
python scripts/check_rag_health.py --help
python scripts/ask_knowledge.py --help
```

显式运行真实 RAG 集成测试：

```bash
RUN_LIVE_RAG_TESTS=1 pytest -m live_rag -v
```

完整质量门禁：

```bash
ruff check .
ruff format --check .
mypy
pytest
python evaluation/run_eval.py --smoke
python scripts/check_docs.py
python scripts/check_architecture.py
```

## 4. Windows PowerShell：使用两个窗口

### 4.1 PowerShell 1：启动和直接验证 RAG

如果 PowerShell 阻止当前用户激活虚拟环境，可执行一次：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

不要降低 LocalMachine 范围或关闭所有脚本安全限制。

```powershell
cd C:\Users\<用户名>\projects\Enterprise-RAG-Engine
.\.venv\Scripts\Activate.ps1
uvicorn app.api:app --host 127.0.0.1 --port 8000
```

验证 Health：

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://127.0.0.1:8000/health"
```

验证 Ask：

```powershell
$headers = @{
    "Content-Type" = "application/json"
    "X-Trace-ID" = "manual-rag-test-001"
}

$body = @{
    question = "What is the supplier quality deviation procedure?"
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/ask" `
  -Headers $headers `
  -Body $body
```

### 4.2 PowerShell 2：运行 Copilot

```powershell
cd C:\Users\<用户名>\projects\Agentic-Enterprise-Knowledge-Copilot
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL = "sqlite:///data/database/enterprise_demo.db"
$env:RAG_BASE_URL = "http://127.0.0.1:8000"
$env:RAG_TIMEOUT_SECONDS = "30"
$env:RAG_MAX_ATTEMPTS = "3"
$env:RAG_RETRY_BASE_DELAY_SECONDS = "0.2"
```

运行 Health Check：

```powershell
python scripts/check_rag_health.py
```

运行 Ask：

```powershell
python scripts/ask_knowledge.py --question "What is the supplier quality deviation procedure?"
```

运行 JSON 输出：

```powershell
python scripts/ask_knowledge.py --question "What is the supplier quality deviation procedure?" --json
```

验证 EvidenceItem：

```powershell
python scripts/ask_knowledge.py --question "What is the supplier quality deviation procedure?" --show-evidence
```

运行离线测试和 Knowledge 单元测试：

```powershell
pytest
pytest tests\unit\knowledge -v
```

运行真实 RAG 集成测试：

```powershell
$env:RUN_LIVE_RAG_TESTS = "1"
pytest -m live_rag -v
```

运行 CLI 帮助：

```powershell
python scripts/check_rag_health.py --help
python scripts/ask_knowledge.py --help
```

清除本次会话的临时变量：

```powershell
Remove-Item Env:DATABASE_URL
Remove-Item Env:RAG_BASE_URL
Remove-Item Env:RAG_TIMEOUT_SECONDS
Remove-Item Env:RAG_MAX_ATTEMPTS
Remove-Item Env:RAG_RETRY_BASE_DELAY_SECONDS
Remove-Item Env:RUN_LIVE_RAG_TESTS -ErrorAction SilentlyContinue
```

## 5. CLI 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 调用成功且响应合法 |
| 2 | Settings、参数或本地配置错误 |
| 3 | `RAGUnavailableError` |
| 4 | `RAGTimeoutError` |
| 5 | `RAGAuthenticationError` |
| 6 | `RAGInvalidResponseError` |
| 7 | `RAGInternalError` |

失败默认只输出安全错误分类、Trace ID 和尝试次数。只有显式使用 `--debug` 才显示
traceback；客户端不会输出 Authorization、完整问题、完整 answer、contexts 或未截断响应。

## 6. 常见故障排查

### Connection refused

现象为 `RAGUnavailableError`，且不会自动重试。检查 RAG 进程是否已启动、监听端口
是否为 8000、`RAG_BASE_URL` 是否正确、是否误用了容器内部地址，以及 Windows
防火墙是否阻止本机端口。

### Timeout

现象为 `RAGTimeoutError`。检查 DeepSeek 或其他模型调用是否过慢、模型是否首次加载、
RAG 服务日志是否收到请求、`RAG_TIMEOUT_SECONDS` 是否过小，以及错误输出中的
`attempts` 是否已经达到 `RAG_MAX_ATTEMPTS`。

### 404

确认 RAG 路由仍为 `/ask`，并确认 `RAG_BASE_URL` 没有误带 `/api`。404 属于请求契约
错误，不会重试。

### 422

通过 RAG OpenAPI 确认请求字段仍为 `question`。422 不会重试。

### Invalid JSON 或 Invalid Response

检查是否访问了错误服务、代理是否返回 HTML、RAG Schema 是否变化、六个必填字段
是否齐全，以及 `sources`、`contexts` 的对象是否严格符合 RAG Contract。

### 401 或 403

客户端返回 `RAGAuthenticationError` 且不重试。本版本没有新增认证 Header 配置，
因为仓库此前没有批准的 RAG 认证配置；如实际 RAG 要求认证，应先按安全与配置流程
新增凭据引用，不能把 Token 写进代码、URL、日志或文档。

### Sources 为空

调用可以成功，但 Evidence 数量为 0。检查知识库是否已 ingest、检索日志是否返回
上下文，以及数据是否属于批准范围。系统不会把 answer 或 context 伪装成来源。

## 7. 停止服务

在运行 RAG 的 macOS 终端或 PowerShell 窗口按：

```text
Ctrl+C
```

独立 Copilot CLI 每次调用结束都会关闭自己创建的 HTTPX Client，无需常驻清理。
