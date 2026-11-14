import axios from 'axios'

// Phase 2 仅占位封装，不实际发起请求；Phase 3+ 逐步接入真实端点。
// 单机单人、无鉴权：会话状态由前端本地存储，通过请求体/参数传递。
const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export const api = {
  setup: (payload) => http.post('/setup', payload),
  chat: (payload) => http.post('/chat', payload),
  // 拍照链路 = 上传 + 视觉识别 + 模型生成，大图实测 40s+，单独放宽超时
  photo: (payload) => http.post('/photo', payload, { timeout: 120000 }),
  // F-6 拍中反馈：只判"太暗/太糊"（不识别内容）；超时短，异常时不挡照片发送
  photoQuality: (payload) => http.post('/photo/quality', payload, { timeout: 10000 }),
  recall: (payload) => http.post('/recall', payload),
  proactive: (payload) => http.post('/proactive', payload),
  tts: (payload) => http.post('/tts', payload),
  asr: (payload) => http.post('/asr', payload),
  getConversations: (params) => http.get('/conversations', { params }),
  getConversation: (id) => http.get(`/conversations/${id}`),
  createConversation: () => http.post('/conversations'),
  deleteConversation: (id) => http.delete(`/conversations/${id}`),
  getMemory: () => http.get('/memory'),
  // F-4 时间线四维检索（B-5）：by ∈ person|object|scene|time
  // k 上限 50（B-5 侧夹紧），一次最多取 50 条，前端再翻页
  memorySearch: (params) => http.get('/memory/search', { params: { k: 50, ...(params || {}) } }),
  // 找物（B-3）：前端与 /chat 并行调用，只取结构化结果做屏幕展示。
  // 本地确定性查询，超时压到 5s，避免接口异常时拖住下一轮输入。
  findObject: (payload) => http.post('/find_object', payload, { timeout: 5000 }),
  // H-2 录制状态：前端只读状态、调开关，不自己拼"是否已停"的判断
  visionStatus: () => http.get('/vision/status'),
  visionStart: (params) =>
    http.post('/vision/start', null, {
      params: { source: 'camera', interval: 2.0, ...(params || {}) },
      // 启动要等摄像头首帧 + 模型加载，H-2 最长等 20s
      timeout: 30000,
    }),
  visionStop: () => http.post('/vision/stop', null, { timeout: 30000 }),
  // 主动验证：只在状态出现 released 且本次没做过停止验证时调一次
  visionVerify: () => http.get('/vision/verify', { timeout: 15000 }),
  // H-12 通话识别（opt-in）：通话页"让小忆看"时上传一帧，内存识别、不落盘、不写画像。
  // 首帧要等模型加载，超时放宽；模型缺失时返回 ok:false，前端降级纯语音。
  callObserve: (payload) => http.post('/call/vision/observe', payload, { timeout: 30000 }),
  // VLM 场景描述（qwen3-vl-plus）：把"画面里有什么"说清楚喂给对话，解决"识别不出/乱说"。走大模型，放宽超时。
  callScene: (payload) => http.post('/call/vision/scene', payload, { timeout: 45000 }),
  callVisionEnd: (callId) => http.post('/call/vision/end', null, { params: { call_id: callId }, timeout: 10000 }),
  forget: (payload) => http.post('/forget', payload),
  wipeMemory: (payload) => http.delete('/memory/wipe', { data: payload || {} }),
  getEmotionChart: () => http.get('/emotion-chart'),
  getReminders: () => http.get('/reminders'),
  doneReminder: (id) => http.post(`/reminders/${id}/done`),
  cancelReminder: (id) => http.post(`/reminders/${id}/cancel`),
  // F-8 方言：能听哪些由后端闸门算（分布 Top3 ∩ WER 达标 ∩ 引擎参数），前端只展示
  // 采集表单只写「老人使用的方言」，不直接决定 ASR 上线
  dialectConfig: () => http.get('/dialect/config'),
  dialectProfile: () => http.get('/dialect/profile'),
  setDialectProfile: (payload) => http.post('/dialect/profile', payload),
  // F-7 子女端：只读摘要 + 安心开关 + 首次告知 + 购药回执 + 老人通知
  // 这里刻意没有会话/聊天接口：子女端拿不到聊天内容，是后端也不提供
  childSummary: () => http.get('/child/summary'),
  childConsent: () => http.get('/child/consent'),
  setChildConsent: (payload) => http.post('/child/consent', payload),
  childNotified: () => http.post('/child/consent/notified'),
  childPurchaseReceipt: (id) => http.post(`/child/purchase/${id}/receipt`),
  childNotices: () => http.get('/child/notices'),
  childNoticeAck: (id) => http.post(`/child/notices/${id}/ack`),
}

export default http