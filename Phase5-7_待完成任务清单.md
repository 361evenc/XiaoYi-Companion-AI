# 小忆 · 功能迁移清单（Phase 5–7）✅ 全部完成

**收尾（2026-10-28）**：旧版 Gradio 界面段已从 app.py 删除
（2502–2798 行，备份于 backups/app.py.20261028_删UI段前.bak），
app.py 保留为纯引擎库；启动小忆.bat 已改为启动新前后端（8010 + 5174）。
详见根目录「app_删Gradio界面_修改说明.txt」。

> 后端薄封装思路：复用 app.py（即 `backend.core.state` 里的 `engine`）已存在的函数，
> 只做参数规整 + 暴露成 JSON 接口，不重写底层逻辑。
> 已确认可复用的 engine 函数见文末「可复用函数对照」。

执行建议顺序：隐私删除 → 提醒 → 记忆箱 → 心情曲线 → 消息撤回 → 主动关怀推送 → PWA。

**进度（2026-10-28 更新）**：
- ✅ Phase 5A 隐私删除（forget/wipe）— 已完成并验证
- ✅ Phase 5B 提醒事项（reminders 列表/完成/取消）— 已完成并验证
- ✅ Phase 6A 记忆箱（memory 重要事件/照片/反思/画像）— 已完成并验证
- ✅ Phase 6B 心情曲线（emotion-chart）— 已完成并验证（matplotlib 可用）
- ✅ Phase 6C 消息撤回（recall，含记忆清除钩子占位说明）— 已完成并验证
- ✅ Phase 7A 主动关怀推送（WebSocket /ws + POST /api/proactive 轮询 + 提示音）— 已完成并验证
- ✅ Phase 7B PWA（vite-plugin-pwa + manifest + 图标 + 离线外壳预缓存）— 构建通过

---

## Phase 5A — 隐私删除（forget / wipe）

**目标**：隐私页的「忘掉一件事」「清空全部记忆」真正生效。

- 后端：新建 `backend/services/memory_service.py`、`backend/api/memory.py`，`main.py` 挂载
  - `POST /api/forget`  入参 `{text}` → 复用 `ai_services.forget_by_request`，出参 `{ok, message}`
  - `DELETE /api/memory/wipe` → 复用 `engine.wipe_all_memory(is_muted)`，出参 `{ok, message}`
- 前端：`src/views/Privacy.vue`
  - `doForget()` 改为调 `api.forget({text})`，成功后提示结果并清空输入
  - `confirmWipe()` 二次确认后调 `api.wipeMemory()`，成功提示
- 验收：忘记某条记忆后记忆箱对应项消失；清空后 conversations.json / memory 清空。

## Phase 5B — 提醒事项（reminders）

**目标**：左栏「提醒事项」抽屉展示真实提醒，支持完成/取消。

- 后端：新建 `backend/services/reminder_service.py`、`backend/api/reminder.py`
  - `GET /api/reminders` → 从 `engine.reminder_store` 读出列表
  - `POST /api/reminders/{id}/done`、`POST /api/reminders/{id}/cancel` → 更新状态
- 前端：`src/components/SidebarCards.vue` 提醒抽屉接 `api.getReminders()`，加「完成/取消」按钮
- 验收：抽屉能看到提醒；点完成/取消后状态变化（与后端 reminder_store 一致）。

## Phase 6A — 记忆箱（memory + 照片）

**目标**：记忆页展示「重要事件时间线 + 照片记忆箱」。

- 后端：`memory_service` 增加 `GET /api/memory` → 返回 `{events, photos}`（复用 `get_memory_box_events_html()` / `list_photos()`，或返回结构化 JSON）
- 前端：`src/views/Memory.vue` 的「重要事件」卡片接 `/api/memory`，渲染时间线 + 照片缩略图
- 验收：聊过后记忆页能看到抽取的重要事件与已上传照片。

## Phase 6B — 心情曲线（emotion-chart）

**目标**：记忆页「查看心情曲线」能出图。

- 后端：`memory_service` 增加 `GET /api/emotion-chart` → 复用 `generate_emotion_chart()`，出参 `{image_url}`（图片经 `/static/` 或 `/audio/` 同类静态路由暴露）
- 前端：`Memory.vue` 按钮点击后加载并展示心情曲线图
- 验收：点按钮能看到曲线图；无数据时给空态提示。

## Phase 6C — 消息撤回（recall）

**目标**：输入区「↩️ 撤回上一条」可撤回上一轮对话。

- 后端：`chat_service` 增加 `recall(conv_id)` → 复用 `ai_services.recall_message(conv_id, removed_user_msg)`
  - ⚠️ 该函数当前是占位（旧版 TODO，人类工程师 ⑥），无替代回复时降级为「仅移除最后一轮 user+assistant」，并返回更新后的 chat_history
- 前端：`InputCard.vue` 的 ↩️ 按钮加点击事件 → emit → `Chat.vue` 调 `api.recall`，用返回 history 刷新消息列表
- 验收：点撤回后最后一条用户+小忆回复从界面消失，并同步后端历史。

## Phase 7A — 主动关怀推送（WebSocket）

**目标**：提醒到点或触发主动关怀时，聊天页实时收到提示并能播报。

- 后端：新建 `backend/api/ws.py` 暴露 `/ws` WebSocket；接入 `reminder_queue` / proactive 后台线程的消息，推 `{type, text}`
- 前端：`Chat.vue` 连接 `ws://`（或 `wss`），收到消息弹出卡片提示（大字号）并可选播报
- 验收：到点提醒能实时弹到聊天页；断线时自动重连、不报错。

## Phase 7B — PWA（安装 / 离线）

**目标**：可「添加到桌面」，离线打开不再是白屏。

- 前端：引入 `vite-plugin-pwa`，配置 `manifest`（名称/图标/主题色）+ `registerType: autoUpdate`
- 资源：补 App 图标（192/512）
- 验收：浏览器出现「安装」入口；断网刷新仍能加载已缓存外壳。

---

## 可复用函数对照（app.py → engine.*）

| 功能 | 旧版函数 | 备注 |
|------|----------|------|
| 记忆面板 | `get_memory_panel_html()` | 已用于 /api/chat 的 panels.memory |
| 记忆箱事件 | `get_memory_box_events_html()` | Phase 6A |
| 照片列表 | `list_photos()` | Phase 6A |
| 忘记记忆 | `forget_box_event(sel)` / `ai_services.forget_by_request` | Phase 5A |
| 清空记忆 | `wipe_all_memory(is_muted)` | Phase 5A |
| 提醒面板 | `get_reminders_panel_html()` | Phase 5B 参考，底层为 `reminder_store` |
| 心情曲线 | `generate_emotion_chart()` | Phase 6B |
| 撤回 | `ai_services.recall_message(conv_id, removed_user_msg)` | Phase 6C，现为占位 |