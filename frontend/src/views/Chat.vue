<template>
  <div class="page">
    <header class="topbar">
      <span class="topbar-title">小忆 · 暖心陪伴</span>
      <span class="topbar-sub">{{ greeting }}</span>
    </header>

    <div class="app-shell">
      <!-- 左栏：4 张入口卡片 -->
      <SidebarCards class="app-sidebar" />

      <!-- 右侧聊天区 -->
      <section class="app-chat">
        <!-- 人格切换：影响小忆说话音色 -->
        <div class="voice-toolbar">
          <span class="voice-label">小忆性格</span>
          <div class="persona-row">
            <button
              v-for="p in personalities"
              :key="p"
              type="button"
              class="persona-chip"
              :class="{ on: p === session.personality }"
              @click="setPersonality(p)"
            >
              {{ p }}
            </button>
          </div>
        </div>

        <div class="msg-list">
          <ChatBubble
            v-for="(m, i) in messages"
            :key="i"
            :role="m.role"
            :text="m.content"
          />
          <!-- 后端思考中的占位气泡 -->
          <ChatBubble v-if="loading" role="bot" text="小忆正在想…" />
        </div>

        <p v-if="error" class="error-tip">{{ error }}</p>

        <!-- 大字幕：随语音播放展示小忆最新回复 -->
        <div v-if="caption" class="caption" :class="{ playing: audioPlaying }">
          <span v-if="audioPlaying" class="caption-hint">🔊 小忆正在说</span>
          <span class="caption-text">{{ caption }}</span>
        </div>

        <audio ref="audioEl" class="hidden-audio" @ended="audioPlaying = false" />

        <InputCard
          :muted="session.isMuted"
          @send="sendMessage"
          @quick="sendMessage"
          @toggle-mute="session.toggleMute"
          @audio="onRecorderAudio"
          @recording-error="onRecorderError"
          @photo="onPhoto"
          @photo-error="onRecorderError"
          @recall="recallLast"
        />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useSessionStore } from '../stores/session'
import { api } from '../api'
import SidebarCards from '../components/SidebarCards.vue'
import ChatBubble from '../components/ChatBubble.vue'
import InputCard from '../components/InputCard.vue'

const session = useSessionStore()

const personalities = ['踏实务实', '风趣幽默', '暖心知心']

const callName = computed(() => {
  const s = session.surname
  const suffix = session.gender === '女' ? '奶奶' : '爷爷'
  return s ? s + suffix : suffix
})
const greeting = computed(() =>
  session.initialized ? `您好，${callName.value}` : '欢迎回来'
)

// 初始消息：优先用 session 传来的历史（欢迎页 setup 或历史页加载），否则给本地欢迎语
const messages = ref([])
if (session.chatHistory && session.chatHistory.length) {
  messages.value = session.chatHistory.map((m) => ({ role: m.role, content: m.content }))
} else {
  messages.value = [
    { role: 'assistant', content: `您好${callName.value}！我是小忆，很高兴能陪伴您～` },
  ]
}

const loading = ref(false)
const error = ref('')

// 语音：隐藏 <audio> + 大字幕
const audioEl = ref(null)
const caption = ref('')
const audioPlaying = ref(false)

function setPersonality(p) {
  if (p !== session.personality) session.setPersonality(p)
}

function stopAudio() {
  if (audioEl.value) {
    audioEl.value.pause()
    audioEl.value.removeAttribute('src')
  }
  audioPlaying.value = false
}

// 播放小忆回复语音：始终更新大字幕文本；仅未静音时真正出声
function playAudio(url, text) {
  stopAudio()
  caption.value = text || ''
  if (!url || session.isMuted) return
  const el = audioEl.value
  if (!el) return
  audioPlaying.value = true
  el.src = url
  el.play().catch(() => {
    audioPlaying.value = false
  })
}

async function sendMessage(text) {
  const t = (text || '').trim()
  if (!t || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.chat({
      user_input: t,
      chat_history: messages.value, // 过去历史，不含本轮输入
      surname: session.surname,
      gender: session.gender,
      is_muted: session.isMuted,
      personality: session.personality,
      conv_id: session.convId,
    })
    handleChatResponse(res.data)
  } catch (e) {
    error.value = '发送失败，请稍后再试。'
    console.error(e)
  } finally {
    loading.value = false
  }
}

// 统一处理 /api/chat、/api/photo 返回的打包：会话 + 历史 + 字幕 + 语音
function handleChatResponse(d) {
  if (d.conv_id) session.setConversation(d.conv_id, d.chat_history || [])
  messages.value = (d.chat_history || []).map((m) => ({ role: m.role, content: m.content }))
  // 最新一条小忆回复 → 大字幕，并在未静音时自动播放语音
  const lastBot = messages.value.filter((m) => m.role === 'assistant').pop()
  const replyText = (lastBot && lastBot.content) || d.reply || ''
  playAudio(d.audio_url, replyText)
}

// 拍照/上传图片：发给后端 → 存记忆箱 + 视觉识别 + 注入对话
async function onPhoto({ base64, mime }) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.photo({
      photo_base64: base64,
      mime: mime || 'image/jpeg',
      chat_history: messages.value,
      surname: session.surname,
      gender: session.gender,
      is_muted: session.isMuted,
      personality: session.personality,
      conv_id: session.convId,
    })
    handleChatResponse(res.data)
  } catch (e) {
    console.error(e)
    error.value = '照片发送失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}

// 录音结束：ASR 识别 → 文本 → 复用文字发送链路
async function onRecorderAudio(audioBase64) {
  try {
    const res = await api.asr({ audio_base64: audioBase64 })
    const text = (res.data.text || '').trim()
    if (text) {
      await sendMessage(text)
    } else {
      error.value = '没听清，请再说一遍。'
    }
  } catch (e) {
    console.error(e)
    error.value = '语音识别失败，请稍后再试。'
  }
}

function onRecorderError(msg) {
  error.value = msg
}

// ---------- 主动关怀推送（WS 提醒 + 主动关怀轮询） ----------

// 到点提示音：三个中低频音符（392/523/659Hz），与旧版后端 _make_chime 频率一致，
// 避开老年性耳背敏感的高频区。静音时也保留提示音（旧版同语义：到点一定有反馈）。
let chimeCtx = null
function playChime() {
  try {
    chimeCtx = chimeCtx || new (window.AudioContext || window.webkitAudioContext)()
    const start = chimeCtx.currentTime
    ;[392, 523.25, 659.25].forEach((f, i) => {
      const osc = chimeCtx.createOscillator()
      const gain = chimeCtx.createGain()
      osc.frequency.value = f
      osc.connect(gain)
      gain.connect(chimeCtx.destination)
      const t0 = start + i * 0.34
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(0.35, t0 + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.28)
      osc.start(t0)
      osc.stop(t0 + 0.3)
    })
  } catch (e) {
    /* 浏览器限制自动播放时忽略 */
  }
}

// 收到一条到点提醒：追加消息 + 提示音 + 大字幕 + 未静音时播报
function onReminderMsg(text) {
  messages.value.push({ role: 'assistant', content: text })
  session.chatHistory = messages.value
  playChime()
  playAudio(null, text) // 无播报音频（无 key 时 TTS 恒 null），字幕仍更新
}

let ws = null
let wsRetryTimer = null

function connectWs() {
  if (!session.convId) return
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${location.host}/ws?conv_id=${encodeURIComponent(session.convId)}`
  try {
    ws = new WebSocket(url)
  } catch (e) {
    console.error(e)
    return
  }
  ws.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data)
      if (d && d.type === 'reminder' && d.text) onReminderMsg(d.text)
    } catch (e) {
      /* 忽略坏包 */
    }
  }
  ws.onclose = () => {
    // 断线重连（10 秒后），后端重启/休眠恢复后自动接上
    wsRetryTimer = setTimeout(connectWs, 10000)
  }
}

// 主动关怀：30 秒轮询一次（后端内部有 15 秒互动节流 + 触发信号节流）
let proactiveTimer = null

async function pollProactive() {
  if (!session.convId || loading.value) return
  try {
    const res = await api.proactive({
      is_muted: session.isMuted,
      conv_id: session.convId,
    })
    const d = res.data
    if (d && d.triggered) {
      handleChatResponse(d)
      playChime()
    }
  } catch (e) {
    /* 静默失败，下轮再试 */
  }
}

onMounted(() => {
  connectWs()
  proactiveTimer = setInterval(pollProactive, 30000)
})

onBeforeUnmount(() => {
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
  if (wsRetryTimer) clearTimeout(wsRetryTimer)
  if (proactiveTimer) clearInterval(proactiveTimer)
  stopAudio()
})

// 撤回最后一轮：后端删对话末轮 + 调记忆清除钩子，前端用返回历史整体替换
async function recallLast() {
  if (!session.convId || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.recall({ conv_id: session.convId })
    const d = res.data
    if (!d.ok) {
      error.value = d.message || '没有可撤回的消息。'
      return
    }
    messages.value = (d.chat_history || []).map((m) => ({ role: m.role, content: m.content }))
    session.setConversation(session.convId, messages.value)
    caption.value = ''
    stopAudio()
  } catch (e) {
    console.error(e)
    error.value = '撤回失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.topbar-sub {
  margin-left: auto;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.msg-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  overflow-y: auto;
}
.error-tip {
  margin: 0;
  padding: 4px 20px;
  color: var(--color-danger);
  font-size: var(--font-small);
}
.voice-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px 0;
}
.voice-label {
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text);
  white-space: nowrap;
}
.persona-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.persona-chip {
  min-height: 48px;
  padding: 0 16px;
  font-size: var(--font-body);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 24px;
  cursor: pointer;
}
.persona-chip.on {
  color: var(--color-accent2);
  border-color: var(--color-accent2);
  background: #EEF4F0;
}
.caption {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}
.caption.playing {
  background: var(--color-surface-soft);
}
.caption-hint {
  flex-shrink: 0;
  font-size: var(--font-small);
  font-weight: 700;
  color: var(--color-accent2);
}
.caption-text {
  font-size: var(--font-title);
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text);
  word-break: break-word;
}
.hidden-audio {
  display: none;
}
</style>