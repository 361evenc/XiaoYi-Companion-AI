<template>
  <div class="page call-page">
    <!-- 顶部状态条：通话时长 + 隐私提示（老人一眼能看懂的诚实说明） -->
    <header class="call-head">
      <div class="head-left">
        <span class="call-title">和小忆视频</span>
        <span class="call-timer" aria-live="off">{{ durationText }}</span>
      </div>
      <p class="call-privacy">通话不会录下来，说完就忘掉</p>
    </header>

    <main class="call-stage">
      <!-- 对方位置：小忆形象（说话时口型+点头动画） -->
      <section class="stage-panel xiaoyi-panel" :class="{ speaking, listening: recording }">
        <svg class="avatar" viewBox="0 0 200 200" role="img" aria-label="小忆">
          <!-- 头部 -->
          <ellipse class="head" cx="100" cy="104" rx="62" ry="66" />
          <!-- 耳朵 -->
          <ellipse class="ear" cx="40" cy="108" rx="9" ry="14" />
          <ellipse class="ear" cx="160" cy="108" rx="9" ry="14" />
          <!-- 眼睛：眨眼动画 -->
          <g class="eyes">
            <ellipse class="eye" cx="78" cy="94" rx="7" ry="8" />
            <ellipse class="eye" cx="122" cy="94" rx="7" ry="8" />
          </g>
          <!-- 腮红 -->
          <ellipse class="cheek" cx="66" cy="118" rx="10" ry="6" />
          <ellipse class="cheek" cx="134" cy="118" rx="10" ry="6" />
          <!-- 嘴：说话时开合 -->
          <ellipse class="mouth" cx="100" cy="130" rx="14" ry="7" />
        </svg>
        <p class="persona-name">小忆</p>
        <p class="persona-state">{{ xiaoyiState }}</p>
      </section>

      <!-- 主画面：本地摄像头预览（原生 video，画面不出浏览器） -->
      <section class="stage-panel local-panel">
        <video
          ref="videoEl"
          class="local-video"
          autoplay
          playsinline
          muted
        ></video>

        <div v-if="cameraError" class="camera-fallback">
          <p class="fallback-title">摄像头没打开</p>
          <p class="fallback-desc">{{ cameraError }}</p>
          <p class="fallback-desc">不影响说话，您照样能跟小忆聊。</p>
        </div>

        <p class="local-tag">这是您</p>
      </section>
    </main>

    <!-- 大字幕：语音为主、屏幕为辅，听不清也能看 -->
    <p v-if="caption" class="caption">{{ caption }}</p>
    <p v-else-if="error" class="caption caption-error">{{ error }}</p>

    <!-- 控制条：说话/挂断，两个按钮都是大按钮 -->
    <footer class="call-actions">
      <button
        class="btn talk-btn"
        :class="recording ? 'btn-success talking' : 'btn-primary'"
        type="button"
        :disabled="busy || hangingUp"
        @click="toggleTalk"
      >
        {{ recording ? '说完了' : busy ? '小忆在想…' : '点我说话' }}
      </button>

      <button
        class="btn btn-danger hangup-btn"
        type="button"
        :disabled="hangingUp"
        @click="hangUp('button')"
      >
        挂断
      </button>
    </footer>
  </div>
</template>

<script setup>
/* ============================================================
   H-1 视频通话页（P0 基础版）
   ------------------------------------------------------------
   设计约束（来自任务卡，改代码前先看）：
   1. P0 只做"能视频通话"——通话帧【只预览、不识别】。
      识别（在不在/看没看/表情/手持物品）拆到 H-12（P1）。
   2. 通话帧零落盘、零写入图谱/记忆箱。
      实现方式：摄像头流只赋给 <video>.srcObject，画面在浏览器内渲染；
      本页【不调用 canvas.toDataURL / 不上传任何帧 / 不调 /api/photo】。
      校验脚本：frontend/tests/h1_privacy_check.mjs
   3. 对话链路复用现有能力，不另起炉灶：
      录音（utils/audio 的 createRecorder，16k 单声道 WAV）
        → POST /api/asr → 文本
        → POST /api/chat → { reply, audio_url, conv_id, chat_history }
        → 播放 audio_url，同步口型/点头动画。
   4. 挂断两条路：一键挂断按钮 + 语音说"挂断/不说了/再见"。
      挂断必须真正停摄像头（track.stop()），不能只是离开页面。
   ============================================================ */

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { api } from '../api'
import { createRecorder } from '../utils/audio'

const router = useRouter()
const session = useSessionStore()

const videoEl = ref(null)
const recording = ref(false)
const busy = ref(false)
const hangingUp = ref(false)
const speaking = ref(false)
const caption = ref('')
const error = ref('')
const cameraError = ref('')
const seconds = ref(0)
const callId = ref('')

let mediaStream = null
let recorder = null
let audioEl = null
let tickTimer = null
let hungUp = false

// 语音挂断关键词：老人不会去点按钮，说一句就得挂
const HANGUP_WORDS = ['挂断', '挂了', '不说了', '不聊了', '再见', '拜拜', '结束通话']

const callName = computed(() => {
  const s = session.surname
  const suffix = session.gender === '女' ? '奶奶' : '爷爷'
  return s ? s + suffix : suffix
})

const durationText = computed(() => {
  const m = String(Math.floor(seconds.value / 60)).padStart(2, '0')
  const s = String(seconds.value % 60).padStart(2, '0')
  return `${m}:${s}`
})

const xiaoyiState = computed(() => {
  if (speaking.value) return '正在说话…'
  if (recording.value) return '在听您说'
  if (busy.value) return '想一下…'
  return '在呢'
})

// ---------- 摄像头：只预览，画面不出浏览器 ----------
async function openCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    cameraError.value = '这个浏览器不支持摄像头'
    return
  }
  try {
    // 只要视频，不要音频：音频走独立的录音器（16k 单声道，给 ASR 用）
    mediaStream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false,
    })
    if (videoEl.value) {
      videoEl.value.srcObject = mediaStream
    }
  } catch (e) {
    // 老人常见情况：没授权/被占用/没有摄像头。降级为语音通话，不拦着用
    if (e && (e.name === 'NotAllowedError' || e.name === 'PermissionDeniedError')) {
      cameraError.value = '您还没允许用摄像头'
    } else if (e && (e.name === 'NotReadableError' || e.name === 'TrackStartError')) {
      cameraError.value = '摄像头被别的程序占着'
    } else if (e && (e.name === 'NotFoundError' || e.name === 'DevicesNotFoundError')) {
      cameraError.value = '这台电脑上没找到摄像头'
    } else {
      cameraError.value = '摄像头打开失败'
    }
  }
}

// 真停摄像头：track.stop() 才是停，光离开页面不算
function closeCamera() {
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop())
    mediaStream = null
  }
  if (videoEl.value) {
    videoEl.value.srcObject = null
  }
}

// ---------- 语音：说话 → ASR → chat → TTS ----------
function stopAudio() {
  if (audioEl) {
    audioEl.pause()
    audioEl.removeAttribute('src')
  }
  speaking.value = false
}

function playReply(url, text) {
  stopAudio()
  caption.value = text || ''
  if (!url) return
  if (!audioEl) {
    audioEl = new Audio()
    audioEl.addEventListener('playing', () => { speaking.value = true })
    audioEl.addEventListener('ended', () => { speaking.value = false })
    audioEl.addEventListener('pause', () => { speaking.value = false })
  }
  // 无音频文件时也保留字幕，老人听不清还能看
  audioEl.src = url
  audioEl.play().catch(() => { speaking.value = false })
}

function isHangupIntent(text) {
  return HANGUP_WORDS.some((w) => text.includes(w))
}

async function toggleTalk() {
  if (hangingUp.value) return

  // 正在录 → 结束并识别
  if (recording.value) {
    recording.value = false
    busy.value = true
    error.value = ''
    try {
      const audioBase64 = await recorder.stop()
      const asrRes = await api.asr({ audio_base64: audioBase64 })
      const text = (asrRes.data.text || '').trim()

      if (!text) {
        error.value = '没听清，您再说一遍。'
        caption.value = ''
        busy.value = false
        return
      }

      // 语音挂断：先挂断，不送进对话（避免"再见"被小忆当成一句闲聊）
      if (isHangupIntent(text)) {
        caption.value = text
        hangUp('voice')
        return
      }

      caption.value = text
      const res = await api.chat({
        user_input: text,
        chat_history: session.chatHistory || [],
        surname: session.surname,
        gender: session.gender,
        is_muted: session.isMuted,
        personality: session.personality,
        conv_id: session.convId,
      })
      const d = res.data
      if (d.conv_id) session.setConversation(d.conv_id, d.chat_history || [])
      const lastBot = (d.chat_history || []).filter((m) => m.role === 'assistant').pop()
      playReply(d.audio_url, (lastBot && lastBot.content) || d.reply || '')
    } catch (e) {
      console.error(e)
      error.value = '没听清，您再说一遍。'
    } finally {
      busy.value = false
    }
    return
  }

  // 没在录 → 开始录
  error.value = ''
  caption.value = ''
  stopAudio()
  try {
    recorder = recorder || createRecorder()
    await recorder.start()
    recording.value = true
  } catch (e) {
    console.error(e)
    error.value = '麦克风打不开，您看看是不是没插好。'
  }
}

// ---------- 挂断 ----------
function hangUp(reason) {
  if (hungUp) return
  hungUp = true
  hangingUp.value = true

  if (recording.value) {
    recording.value = false
    try { recorder && recorder.cancel() } catch (e) { /* 忽略 */ }
  }
  stopAudio()
  closeCamera()               // 关键：真正停摄像头
  if (tickTimer) clearInterval(tickTimer)

  // 通话本身不写记忆：这里只在本页内存里收个尾，不落盘、不发后端
  const summary = { call_id: callId.value, seconds: seconds.value, ended_by: reason }

  // 稍微留一下画面，让老人看清"挂了"
  setTimeout(() => {
    if (router) router.push('/chat')
  }, 260)
  return summary
}

onMounted(() => {
  // call_id 仅用于本次会话内标识，不上传、不落盘（H-12 才会用到通话记录）
  callId.value = `call_${Date.now().toString(36)}`
  openCamera()
  tickTimer = setInterval(() => { seconds.value += 1 }, 1000)
})

onBeforeUnmount(() => {
  // 兜底：直接关页面/切路由也必须放掉摄像头
  if (!hungUp) {
    if (recording.value) { try { recorder && recorder.cancel() } catch (e) { /* 忽略 */ } }
    stopAudio()
    closeCamera()
  }
  if (tickTimer) clearInterval(tickTimer)
})
</script>

<style scoped>
.call-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
  padding: 16px;
}

/* ---------- 顶部 ---------- */
.call-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.head-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.call-title {
  font-size: var(--font-title);
  font-weight: 700;
  color: var(--color-text);
}
.call-timer {
  font-size: var(--font-body);
  color: var(--color-text-sub);
  font-variant-numeric: tabular-nums;
}
.call-privacy {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-accent2-deep);   /* 深青绿：这是"让您放心"的信息，不是警告 */
}

/* ---------- 主画面：左右两栏，窄屏自动上下 ---------- */
.call-stage {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(240px, 1.35fr);
  gap: 12px;
  flex: 1;
  min-height: 320px;
}
@media (max-width: 720px) {
  .call-stage {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr 1fr;
  }
}

.stage-panel {
  position: relative;
  border-radius: var(--radius-lg);
  overflow: hidden;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 180px;
}

/* 小忆形象区 */
.xiaoyi-panel {
  background: linear-gradient(180deg, #FFF7EA 0%, #FAEEDC 100%);
  gap: 6px;
  padding: 12px;
}
.avatar {
  width: min(52%, 180px);
  height: auto;
  transition: transform 0.25s ease;
}
.head {
  fill: #F6D9B8;
  stroke: var(--color-accent);
  stroke-width: 3;
}
.ear {
  fill: #F0C9A0;
  stroke: var(--color-accent);
  stroke-width: 2;
}
.eye {
  fill: #3A2413;
  transform-origin: center;
  animation: blink 4.6s infinite;
}
.cheek {
  fill: #EFA9A0;
  opacity: 0.55;
}
.mouth {
  fill: #8C4A2F;
  transition: ry 0.12s ease;
}

/* 说话：口型开合 + 轻轻点头 */
.xiaoyi-panel.speaking .mouth {
  animation: talk 0.26s infinite alternate;
}
.xiaoyi-panel.speaking .avatar {
  animation: nod 1.6s ease-in-out infinite;
}
/* 在听：形象微微前倾，提示"我在听" */
.xiaoyi-panel.listening .avatar {
  transform: scale(1.04);
}

@keyframes talk {
  from { ry: 4; }
  to   { ry: 12; }
}
@keyframes nod {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(4px); }
}
@keyframes blink {
  0%, 92%, 100% { transform: scaleY(1); }
  95%           { transform: scaleY(0.1); }
}

.persona-name {
  margin: 0;
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-text);
}
.persona-state {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
  min-height: 1.6em;
}

/* 本地预览区 */
.local-panel {
  background: #201811;
}
.local-video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform: scaleX(-1);   /* 镜像：像照镜子，老人更习惯 */
  display: block;
}
.local-tag {
  position: absolute;
  left: 12px;
  bottom: 10px;
  margin: 0;
  padding: 2px 10px;
  border-radius: var(--radius-sm);
  font-size: var(--font-small);
  color: #FFF8ED;
  background: rgba(32, 24, 17, 0.66);
}

.camera-fallback {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 16px;
  text-align: center;
  background: var(--color-surface-soft);
}
.fallback-title {
  margin: 0;
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-text);
}
.fallback-desc {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}

/* ---------- 字幕 ---------- */
.caption {
  margin: 0;
  padding: 10px 16px;
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow);
  font-size: var(--font-bubble);
  line-height: 1.5;
  color: var(--color-text);
  min-height: 2.6em;
}
.caption-error {
  color: var(--color-danger);
}

/* ---------- 控制条 ---------- */
.call-actions {
  display: flex;
  gap: 12px;
}
.talk-btn {
  flex: 2;
  min-height: 64px;
  font-size: 22px;
}
.talk-btn.talking {
  box-shadow: 0 0 0 4px rgba(62, 130, 95, 0.25);
}
.hangup-btn {
  flex: 1;
  min-height: 64px;
  font-size: 20px;
}
</style>
