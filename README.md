# NOVA — Offline-First Multimodal Personal AI Agent

> **Phase 2 — Real Local LLM Integration Foundation**

NOVA is an offline-first, competition-grade multimodal personal AI agent designed to eventually perceive, reason, plan, execute, verify actions, and operate seamlessly across physical (robotics, microcontrollers, sensors, smart home) and digital (OS control, applications, voice, documents, vision) environments.

---

## 🏗 System Architecture (Phase 2)

```
                    +-----------------------+
                    |     REST API Layer    |
                    |   (FastAPI Gateway)   |
                    +-----------+-----------+
                                |
                                v
+-----------------------------------------------------------------------------------+
|                                   AGENT KERNEL                                    |
|                                                                                   |
|  +----------------+     +------------+     +----------+     +------------------+  |
|  |  Intent Router +---->+  Planner   +---->+ Executor +---->+ Security/Perms   |  |
|  +----------------+     +------------+     +----+-----+     +------------------+  |
|                                                 |                                 |
|  +----------------+     +------------+          v            +------------------+  |
|  | Observability  |<----+ State/Task |<----------------------+ Tool Execution   |  |
|  +----------------+     +------------+                       +------------------+  |
+-------------------+---------------------------------------------------------------+
                    |                                 |
                    v                                 v
        +-----------------------+         +-----------------------+
        |     Model Gateway     |         |     Memory System     |
        | (Mock/Local/Remote)   |         | (Working/Conv/LT/Proj)|
        +-----------+-----------+         +-----------------------+
                    |
                    v
        +-----------------------+
        |   LocalLLMProvider    |
        |  (AsyncOpenAI Client) |
        +-----------+-----------+
                    |
                    v (OpenAI-compatible HTTP Protocol)
        +-----------------------+
        | Self-Hosted Inference |
        |  (vLLM / Ollama Server)
        +-----------------------+
```

---

## 🧠 Local LLM Provider & Future GPU Server Architecture

NOVA connects to any OpenAI-compatible inference server using `LocalLLMProvider` (`nova/app/llm/local.py`).

### Operating Modes (`LLM_MODE`)
- **`mock` (Default)**: Always uses `MockLLMProvider` for deterministic offline demonstration without external dependencies.
- **`local`**: Always uses `LocalLLMProvider` connecting to `LOCAL_LLM_BASE_URL`.
- **`auto`**: Auto-detects server health via `health_check()`. If the local inference server is online, `local` is selected; if offline, it gracefully falls back to `mock` while explicitly setting response metadata (`provider="mock"` vs `provider="local"`).

### Future GPU Machine Deployment Topology
```
                     +---------------------------------------+
                     |         NOVA Main Application         |
                     |         (Local CPU/Dev Server)        |
                     +-------------------+-------------------+
                                         |
                                         v (OpenAI-Compatible HTTP / REST)
                     +-------------------+-------------------+
                     |       Self-Hosted GPU Machine         |
                     |  (e.g., Google Cloud GPU / vLLM)      |
                     |                                       |
                     |  - Engine: vLLM / Ollama / LocalAI    |
                     |  - Model: Qwen 2.5 Coder / Llama 3    |
                     +---------------------------------------+
```

---

## 📁 Project Structure

```
nova/
├── app/
│   ├── main.py                # FastAPI entry point & lifespan initialization
│   │
│   ├── api/                   # REST API Layer
│   │   ├── dependencies.py    # Dependency injection helpers
│   │   └── routes/            # Route modules (health, chat, tasks, tools, memory, llm)
│   │       └── llm.py         # GET /api/v1/llm/status endpoint
│   │
│   ├── core/                  # Core infrastructure
│   │   ├── config.py          # Pydantic Settings & env configuration
│   │   ├── logging.py         # Structured JSON logging & context tracking
│   │   └── security.py        # Permission Manager & security levels
│   │
│   ├── agent/                 # Agent Kernel engine
│   │   ├── kernel.py          # Central lifecycle orchestrator
│   │   ├── router.py          # Intent classification router
│   │   ├── planner.py         # Structured execution plan builder
│   │   ├── executor.py        # Tool execution & permission validator
│   │   ├── state.py           # Task runtime state tracker
│   │   ├── task_manager.py    # Async task tracking & cancellation
│   │   └── prompts.py         # Centralized NOVA system prompts
│   │
│   ├── llm/                   # Model abstraction layer
│   │   ├── base.py            # Abstract LLMProvider interface & LLMHealthStatus
│   │   ├── mock.py            # Offline MockLLMProvider (Default)
│   │   ├── local.py           # LocalLLMProvider adapter using AsyncOpenAI
│   │   ├── remote.py          # Remote cloud LLM adapter (OpenAI compatible)
│   │   └── gateway.py         # Model Gateway, LLM_MODE handler, & capability routing
│   │
│   ├── memory/                # Multi-tier memory abstraction
│   │   ├── base.py            # Base memory interface
│   │   ├── working.py         # In-memory transient context
│   │   ├── conversation.py    # Dialogue history
│   │   ├── long_term.py       # Persistent SQLite key-value memory
│   │   └── project.py         # Workspace environment settings
│   │
│   ├── tools/                 # Tool System
│   │   ├── base.py            # Abstract BaseTool class
│   │   ├── registry.py        # Dynamic ToolRegistry
│   │   ├── adapter.py         # ToolSchemaAdapter (NOVA -> OpenAI function definitions)
│   │   └── builtin/           # Initial safe tools
│   │       ├── calculator.py  # Safe AST math tool (LOW_RISK_ACTION)
│   │       ├── system_info.py # Diagnostic system info (READ)
│   │       └── time.py        # System local & UTC time (READ)
│   │
│   ├── database/              # Persistence layer
│   │   ├── database.py        # Async SQLAlchemy engine & sessionmaker
│   │   └── models.py          # ORM models (Task, Memory, ToolLogs)
│   │
│   └── schemas/               # Pydantic data schemas
│       ├── agent.py           # Agent state & planning schemas
│       ├── messages.py        # API chat payload schemas
│       ├── tools.py           # Tool metadata & result schemas
│       └── tasks.py           # Task status & history schemas
│
├── tests/                     # Test Suite (pytest & pytest-asyncio)
│   ├── agent/                 # Kernel, permissions, task tests
│   ├── llm/                   # Mock LLM, LocalLLMProvider, & HTTP test double tests
│   ├── memory/                # Memory operations tests
│   ├── tools/                 # Built-in tools & registry tests
│   ├── test_health.py         # System health endpoint tests
│   └── test_api.py            # End-to-end REST API tests
│
├── .env.example               # Environment variables template
├── .gitignore                 # Git exclusion rules
├── pyproject.toml             # Project metadata & build specs
├── requirements.txt           # Python dependency requirements
└── README.md                  # Project documentation
```

---

## 🔒 Security Philosophy & Safety Guards

1. **Strict Permission Control**: Every tool declares its required permission level:
   - `READ`: Safe read-only operations (e.g. system diagnostics, time).
   - `LOW_RISK_ACTION`: Safe computation (e.g. math calculator).
   - `CONFIRM_REQUIRED`: Operations requiring explicit user authorization.
   - `HIGH_RISK_ACTION`: Potentially disruptive actions.
   - `BLOCKED`: Forbidden execution paths.
2. **Zero Arbitrary Execution**: No arbitrary shell execution or python `eval()` is granted to the model.
3. **Execution Guardrails**:
   - `MAX_PLANNING_ITERATIONS` (Default: 5)
   - `MAX_TOOL_CALLS` (Default: 10)
   - `PER_TOOL_TIMEOUT_SECONDS` (Default: 10s)
   - `TOTAL_TASK_TIMEOUT_SECONDS` (Default: 60s)
4. **Credential Safety**: No secret logging, authorization header logging, or key leakage in LLM status endpoints.

---

## ⚡ Quick Start & Installation

### 1. Requirements
- Python 3.11+
- Virtual environment recommended

### 2. Setup Environment

```bash
git clone <repository_url>
cd "AI Asistant"

# Create & activate virtual environment (optional)
python -m venv venv
# On Windows:
venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Running the Server

Start the FastAPI application using Uvicorn:

```bash
python -m uvicorn iris.app.main:app --host 127.0.0.1 --port 8000 --reload
```

Interactive API documentation is available at:
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

---

## 🧪 Running the Test Suite

Run the full automated test suite using `pytest`:

```bash
python -m pytest -v
```

---

## 🌐 REST API Endpoints & Verification Examples

### Health Check

```http
GET /health
```

### System Status

```http
GET /api/v1/status
```

### LLM Status (Phase 2)

```http
GET /api/v1/llm/status
```

**Response:**
```json
{
  "mode": "mock",
  "provider": "mock",
  "model": "mock-model",
  "available": true,
  "base_url": "in-process://mock",
  "latency_ms": 0.1,
  "capabilities": [
    "chat",
    "reasoning",
    "coding",
    "fast"
  ],
  "error": null
}
```

### Chat Endpoint with Provider Metadata

```http
POST /api/v1/chat
Content-Type: application/json

{
  "message": "What is 25 multiplied by 47?"
}
```

**Response:**
```json
{
  "task_id": "task_a1b2c3d4e5f6",
  "correlation_id": "cid_9876543210fe",
  "response": "Result: 1175",
  "intent_detected": "calculator",
  "tools_executed": [
    {
      "tool_name": "calculator",
      "arguments": { "expression": "25 * 47" },
      "success": true,
      "result": { "expression": "25 * 47", "result": 1175, "formatted": "25 * 47 = 1175" },
      "error": null
    }
  ],
  "status": "COMPLETED",
  "provider": "mock",
  "model": "mock-model",
  "mode": "reasoning",
  "error": null
}
```

---

## 🔮 Roadmap & Future Phases

- **Phase 1**: Offline-first Agent Kernel, Tool Registry, Task Manager, Memory, Mock LLM.
- **Phase 2 (Current)**: AsyncOpenAI Local LLM Provider, OpenAI-compatible HTTP integration, Model Gateway fallback (`mock`, `local`, `auto`), Tool Schema Adapter, System Prompts, LLM status endpoint.
- **Phase 3**: Advanced Multi-step Planning & Recursive Tool Chains with real local model tool-calling.
- **Phase 4**: Vector Store, Embeddings & RAG Long-Term Memory.
- **Phase 5**: Offline Voice Pipeline (Whisper STT, Piper TTS, Wake Word).
- **Phase 6**: OS & Application Automation.
- **Phase 7**: Multimodal Vision (Object Detection, Screen Understanding).
- **Phase 8**: Home Automation & Smart Device Integrations.
- **Phase 9**: ESP32 Microcontroller Protocols (MQTT / Serial).
- **Phase 10**: Humanoid Robotics Control & Kinematics.
- **Phase 11**: Navigation, Mapping & Person Following.
- **Phase 12**: Advanced Multi-Device Ecosystem Orchestration.
