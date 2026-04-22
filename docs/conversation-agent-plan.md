# 实现计划：Conversation Agent（语音对话机器人）

## 一、功能概览

在左侧菜单栏新增 **Conversation Agent** 入口，点击进入 `/conversation` 页面：
1. **Agent 列表 + 配置界面**：可创建/管理多个对话 Agent，配置每个 Agent 的背景知识（system prompt）、使用的 LLM model、语音 prompt（voice_clone 的参考音频）等
2. **开始对话按钮**：点击进入语音对话界面
3. **对话循环**：
   - 用户按住按钮录音 / VAD 自动断句
   - 前端上传音频 → 调用现有 `/api/v1/audio/transcriptions`（Whisper/OpenAI STT）→ 得到文本
   - 文本作为用户消息调用 LLM（带 Agent 的 system prompt）→ 得到回复文本
   - 回复文本 → 调用新增后端接口 `/api/v1/audio/moss-tts` → 请求 MOSS-TTS-Nano（`POST /tts`，返回 audio/wav）→ 前端播放
   - 循环

## 二、MOSS-TTS-Nano API 摘要

来源：`/Users/jiamin/git/MOSS-TTS-Nano/api_server.py`
- `POST /tts` body `{"text": "..."}` → `audio/wav` 二进制（目前固定用 `assets/audio/en_2.wav` 作为 voice_clone prompt）
- `GET /health` → `{"status": "ok" | "loading"}`
- 只吃文本，**不支持自定义 prompt 音频**（当前实现用启动时指定的 `--prompt-audio`）

➡️ **关键决策点 1**：如果希望每个 Agent 有自己的音色，需要在 MOSS-TTS-Nano 侧扩展 API 支持请求级 prompt_audio（上传文件或 prompt_audio_path）。先版可以只支持单一全局音色，后续迭代再加。

## 三、后端改动（Python / FastAPI）

### 3.1 新增数据模型 `backend/open_webui/models/conversation_agents.py`
参照 `notes.py` 结构，表 `conversation_agent`：
- `id` (uuid, pk)
- `user_id`
- `name`（Agent 名称）
- `description`
- `system_prompt`（背景知识/人格设定，Text）
- `model_id`（使用哪个 LLM）
- `voice_config` (JSON：language、voice_prompt 文件 id、语速等预留)
- `meta` (JSON 预留)
- `access_control` (JSON)
- `created_at` / `updated_at`

提供 `ConversationAgents` CRUD 类。

### 3.2 新增路由 `backend/open_webui/routers/conversation_agents.py`
- `GET /api/v1/conversation-agents/` 列表（当前用户）
- `POST /api/v1/conversation-agents/create`
- `GET /api/v1/conversation-agents/{id}`
- `POST /api/v1/conversation-agents/{id}/update`
- `DELETE /api/v1/conversation-agents/{id}/delete`

在 `main.py` 注册 router。

### 3.3 数据库迁移
在 `backend/open_webui/migrations/versions/` 新增一个 alembic 迁移文件创建 `conversation_agent` 表。

### 3.4 MOSS-TTS 桥接 `backend/open_webui/routers/audio.py`
扩展 TTS_ENGINE 支持一个新引擎 `"moss"`（或独立新增 endpoint `POST /api/v1/audio/moss-tts`）：
- 配置项：`MOSS_TTS_API_BASE_URL`（默认 `http://localhost:8000`）
- 代理调用 `{base}/tts`，把返回的 audio/wav 转发给前端（保留缓存：按文本 sha256 落盘到 `SPEECH_CACHE_DIR`）
- 在 `config.py` / Admin Audio Settings 增加 MOSS 配置项

➡️ **关键决策点 2**：选择方案 A（扩展 TTS_ENGINE = "moss"，复用全局 `/speech` 路径）或方案 B（单独新增 `/conversation-agent/speak` 接口）。**建议方案 A**，代价最小，且 CallOverlay 等组件可直接复用。

### 3.5 后端简单代理到 LLM
对话时复用现有 `/api/chat/completions`，前端拼好 messages（注入 Agent 的 system prompt）。不需新增后端 LLM 逻辑。

## 四、前端改动（Svelte）

### 4.1 API 封装 `src/lib/apis/conversation-agents/index.ts`
对应后端 CRUD，仿照 `src/lib/apis/notes/`。

### 4.2 侧边栏入口 `src/lib/components/layout/Sidebar.svelte`
在 Notes 和 Workspace 之间新增一个入口：
- Collapsed 视图（line ~645-667 旁）：图标 + Tooltip "Conversation Agent"
- Expanded 视图（line ~880-899 旁）：图标 + 文字
- 跳转 `/conversation`

### 4.3 路由
- `src/routes/(app)/conversation/+layout.svelte`
- `src/routes/(app)/conversation/+page.svelte`：Agent 列表 + "新建 Agent" 按钮
- `src/routes/(app)/conversation/[id]/+page.svelte`：编辑 Agent（背景知识 textarea、LLM 选择、音色 prompt 上传）+ "开始对话" 按钮

### 4.4 对话组件 `src/lib/components/conversation-agent/`
- `AgentList.svelte`：卡片式列表
- `AgentEditor.svelte`：配置表单（name / description / system_prompt textarea / model picker / voice prompt uploader）
- `VoiceChat.svelte`：对话主界面（借鉴 `CallOverlay.svelte` 的录音/VAD/播放逻辑）
  - 使用 MediaRecorder + silence detection（已有 VAD util 可复用）
  - 调 `transcribeAudio()` 转文字
  - 拼 messages（system + 历史 + user）调 `/api/chat/completions`
  - LLM 回复后调 `synthesizeOpenAISpeech()`（若引擎=moss 就走到 MOSS）播放
  - 展示对话消息气泡 + 当前状态（Listening / Thinking / Speaking）

### 4.5 管理端 Audio Settings
`src/lib/components/admin/Settings/Audio.svelte` 增加 MOSS-TTS 引擎选项和 API URL 字段。

## 五、实现顺序建议

1. **后端数据层**：model + migration + router，跑通 CRUD（可 curl 验证）
2. **后端 TTS 桥接**：把 MOSS `POST /tts` 接进 `audio.py`，用 Admin Settings 切到 moss 引擎后，现有任意 TTS 入口（消息朗读按钮）能发声即成功
3. **前端侧边栏入口 + 路由骨架 + API 封装**
4. **Agent 列表 + 编辑页**（表单即可，先不连音频）
5. **VoiceChat 核心循环**（STT → LLM → TTS），先手动按钮触发单轮
6. **加入 VAD 自动断句 + 打断** 细化体验
7. **MOSS-TTS 多音色支持**（需要改 MOSS-TTS-Nano 的 API）—— 后续迭代

## 六、需要确认的几点

1. **音色定制**：先版是否可以**只用 MOSS 全局默认音色**（Agent 配置里不暴露音色选项）？如果需要每 Agent 自定义音色，得先改 MOSS-TTS-Nano 的 API 接受 `prompt_audio` 上传。
2. **STT 引擎**：直接复用现有 Admin Settings 配置的 STT（Whisper / OpenAI / Deepgram 等），对吗？还是需要新建专用 STT 入口？
3. **会话持久化**：对话历史需要保存到数据库吗？还是仅在前端内存中（刷新即丢）？保存的话需要再加一张 `conversation_agent_session` 表。
4. **TTS 引擎接入方式**：走方案 A（扩展全局 TTS_ENGINE = "moss"）还是方案 B（独立 endpoint 只服务 Conversation Agent）？倾向 A。
