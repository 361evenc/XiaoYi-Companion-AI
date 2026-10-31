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
  recall: (payload) => http.post('/recall', payload),
  proactive: (payload) => http.post('/proactive', payload),
  tts: (payload) => http.post('/tts', payload),
  asr: (payload) => http.post('/asr', payload),
  getConversations: (params) => http.get('/conversations', { params }),
  getConversation: (id) => http.get(`/conversations/${id}`),
  createConversation: () => http.post('/conversations'),
  deleteConversation: (id) => http.delete(`/conversations/${id}`),
  getMemory: () => http.get('/memory'),
  forget: (payload) => http.post('/forget', payload),
  wipeMemory: (payload) => http.delete('/memory/wipe', { data: payload || {} }),
  getEmotionChart: () => http.get('/emotion-chart'),
  getReminders: () => http.get('/reminders'),
  doneReminder: (id) => http.post(`/reminders/${id}/done`),
  cancelReminder: (id) => http.post(`/reminders/${id}/cancel`),
}

export default http