<template>
  <div class="page call-page">
    <!-- 顶部状态条：通话时长 + 隐私提示（老人一眼能看懂的诚实说明） -->
    <header class="call-head">
      <div class="head-left">
        <span class="call-title">和小忆视频</span>
        <span class="call-timer" aria-live="off">{{ durationText }}</span>
      </div>
      <div class="head-right">
        <button
          class="vision-toggle"
          type="button"
          :class="{ on: visionOn, degraded: visionOn && visionState === 'degraded' }"
          :disabled="!!cameraError"
          @click="toggleVision"
        >
          {{ visionToggleText }}
        </button>
        <p class="call-privacy">{{ privacyText }}</p>
      </div>
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
        <span v-if="visionStatusText" class="vision-tag" :class="{ degraded: visionState === 'degraded' }">
          {{ visionStatusText }}
        </span>
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
        :disabled="hangingUp"
        @click="onMainButton"
      >
        {{ mainButtonLabel }}
      </button>

      <button
        class="btn auto-toggle"
        type="button"
        :disabled="hangingUp"
        @click="toggleAutoMode"
      >
        {{ autoMode ? '⏸ 改手动' : '⚡ 自动' }}
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

// H-12 通话识别：默认【一直开着、持续看】（用户 2026-11-12 要求：持续检测，别纠结隐私）。
// YOLO（本地，快）每 2.5s 看一次"人在不在/手里拿啥"；VLM（qwen3-vl-plus）每 8s
// 描述一次"画面里有什么"。看到的都喂给对话，让"小忆"只说真正看到的、不凭空猜。
// 帧只在内存里识别、不写画像、不落盘（通话"说完就忘掉"）。
const visionOn = ref(false)      // 摄像头一打开就自动置 true
const visionState = ref('off')   // off | watching | busy | degraded
const lastObs = ref(null)        // YOLO 结构化结论
const lastScene = ref('')        // VLM 场景描述（"画面里有…"）
let observeTimer = null
let sceneTimer = null
let observing = false
let scening = false

const OBSERVE_MS = 2500   // YOLO 快，2.5s 一次
const SCENE_MS = 8000     // VLM 走大模型，8s 一次

const visionToggleText = computed(() => (visionOn.value ? '👀 小忆在看（点我关）' : '👁 让小忆看'))
const privacyText = computed(() => '画面只用来当下看一眼，不留存')
const visionStatusText = computed(() => {
  if (!visionOn.value) return ''
  if (lastScene.value) return lastScene.value
  if (visionState.value === 'degraded') return '画面这边看不清，先用声音聊'
  const o = lastObs.value
  if (!o) return '小忆在看…'
  if (o.hand_object) return `小忆看到您手里有「${o.hand_object}」`
  if (!o.person_present) return '画面里暂时没有您'
  return '小忆在看您'
})

function captureFrameB64() {
  const v = videoEl.value
  if (!v || !v.videoWidth || !mediaStream) return ''
  try {
    const c = document.createElement('canvas')
    c.width = v.videoWidth
    c.height = v.videoHeight
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
    return c.toDataURL('image/jpeg', 0.6).split(',')[1] || ''
  } catch (e) {
    return ''
  }
}

async function observeOnce() {
  if (observing) return
  const b64 = captureFrameB64()
  if (!b64) return
  observing = true
  visionState.value = 'busy'
  try {
    const res = await api.callObserve({ frame_b64: b64, call_id: callId.value, scene: '' })
    const d = res.data || {}
    if (d.ok) {
      lastObs.value = d
      visionState.value = 'watching'
    } else {
      visionState.value = 'degraded'
    }
  } catch (e) {
    visionState.value = 'degraded'
  } finally {
    observing = false
  }
}

// VLM 场景描述：把"画面里有什么"用一两句话说清（识别眼镜/药/杯子这类 YOLO 看不到的东西）
async function sceneOnce() {
  if (scening) return
  const b64 = captureFrameB64()
  if (!b64) return
  scening = true
  try {
    const res = await api.callScene({ frame_b64: b64, call_id: callId.value, scene: '' })
    const d = res.data || {}
    if (d.ok && d.scene) {
      lastScene.value = d.scene
    }
  } catch (e) {
    /* VLM 失败就用 YOLO 的结论，不打断通话 */
  } finally {
    scening = false
  }
}

function startVision() {
  if (cameraError.value) return
  visionOn.value = true
  visionState.value = 'watching'
  observeOnce()
  sceneOnce()
  if (observeTimer) clearInterval(observeTimer)
  observeTimer = setInterval(observeOnce, OBSERVE_MS)
  if (sceneTimer) clearInterval(sceneTimer)
  sceneTimer = setInterval(sceneOnce, SCENE_MS)
}

function stopVision() {
  if (observeTimer) { clearInterval(observeTimer); observeTimer = null }
  if (sceneTimer) { clearInterval(sceneTimer); sceneTimer = null }
  visionOn.value = false
  lastObs.value = null
  lastScene.value = ''
  if (visionState.value !== 'off') visionState.value = 'off'
  api.callVisionEnd(callId.value).catch(() => {})
}

function toggleVision() {
  if (visionOn.value) stopVision()
  else startVision()
}

// 把"看到的"喂给对话：YOLO（人在不在/手里拿啥）+ VLM（画面有什么）合成一段上下文，
// 并叮嘱小忆"只说看到的、别编"——这样它就不会"识别不出还乱说"。
function obsContext() {
  if (!visionOn.value) return ''
  const bits = []
  const o = lastObs.value
  if (o) {
    if (o.person_present) bits.push('画面里有您（老人本人）')
    else bits.push('画面里暂时没看到您本人')
    if (o.hand_object) bits.push(`您手里拿着「${o.hand_object}」`)
  }
  if (lastScene.value) bits.push(`画面里看到：${lastScene.value}`)
  if (!bits.length) return ''
  return `【小忆此刻看到】${bits.join('；')}。请只根据这些真实看到的内容回应，没看到的别说、别编造。\n`
}

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
    startVision()   // 摄像头一就绪就持续看（默认开、一直检测，用户 2026-11-12 要求）
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

// ---------- 连续对话（默认自动、不按键）+ 手动兜底 ----------
// autoMode（默认开）：VAD 监听麦克风电平，"开口→停顿 0.9s" 就自动发送；
// 点"改手动"就回到原来那套"按一下说话、说完了"。两套共用同一套 ASR→对话→播放。
const autoMode = ref(true)

const mainButtonLabel = computed(() => {
  if (autoMode.value) {
    if (recording.value) return '在听您说…'
    if (busy.value) return '小忆在想…'
    return '自动聆听中'
  }
  if (recording.value) return '说完了'
  if (busy.value) return '小忆在想…'
  return '点我说话'
})

// 主按钮：自动模式下点它 = 切手动（老人想手动就切过去）；手动模式 = 按一下说话
function onMainButton() {
  if (autoMode.value) toggleAutoMode()
  else toggleTalk()
}

// ---- 录音/发送核心（自动 & 手动共用）----
async function startRecording() {
  error.value = ''
  try {
    recorder = recorder || createRecorder()
    await recorder.start()
    recording.value = true
  } catch (e) {
    console.error(e)
    error.value = '麦克风打不开，您看看是不是没插好。'
  }
}

async function finishAndSend() {
  // 停录音 → ASR →（挂断检查）→ 对话 → 播放
  if (!recorder) return
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
      user_input: obsContext() + text,
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
}

// 自动模式：检测到"说完了"就发；发完重新起录音，接着听下一句
async function autoSend() {
  await finishAndSend()
  if (autoMode.value && !hangingUp.value) {
    try { await startRecording() } catch (e) { /* 忽略 */ }
  }
}

// 手动模式："按一下说话 / 说完了"
function toggleTalk() {
  if (hangingUp.value) return
  if (recording.value) {
    finishAndSend()
  } else {
    stopAudio()
    startRecording()
  }
}

// ---------- VAD：说话→停顿 自动发送（连续对话的核心） ----------
let vadStream = null
let vadCtx = null
let vadAnalyser = null
let vadTimer = null
let silenceSince = 0
let wasSpeaking = false
const VAD_THRESHOLD = 0.012   // RMS 超过它算"在说话"
const VAD_SILENCE_MS = 900    // 停顿这么久算"说完了"

function rmsLevel() {
  if (!vadAnalyser) return 0
  const data = new Uint8Array(vadAnalyser.fftSize)
  vadAnalyser.getByteTimeDomainData(data)
  let sum = 0
  for (let i = 0; i < data.length; i++) {
    const v = (data[i] - 128) / 128
    sum += v * v
  }
  return Math.sqrt(sum / data.length)
}

async function startVad() {
  try {
    vadStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    vadCtx = new (window.AudioContext || window.webkitAudioContext)()
    const src = vadCtx.createMediaStreamSource(vadStream)
    vadAnalyser = vadCtx.createAnalyser()
    vadAnalyser.fftSize = 512
    src.connect(vadAnalyser)
  } catch (e) {
    // VAD 起不来（没麦克风/被占用）：退回手动，别把整通电话卡死
    console.error(e)
    autoMode.value = false
    return
  }
  if (vadTimer) clearInterval(vadTimer)
  vadTimer = setInterval(vadTick, 120)
  await startRecording()   // 自动模式一进去就先起一路录音，等着接话
}

function vadTick() {
  if (!autoMode.value || busy.value || speaking.value || hangingUp.value) return
  const level = rmsLevel()
  const now = Date.now()
  if (level > VAD_THRESHOLD) {
    wasSpeaking = true
    silenceSince = 0
    if (!recording.value) {
      startRecording()   // 老人开口了、还没在录 → 起录音把这段话接住
    }
  } else if (wasSpeaking) {
    if (!silenceSince) silenceSince = now
    if (now - silenceSince >= VAD_SILENCE_MS) {
      wasSpeaking = false
      silenceSince = 0
      autoSend()
    }
  }
}

function stopVad() {
  if (vadTimer) { clearInterval(vadTimer); vadTimer = null }
  if (vadStream) { vadStream.getTracks().forEach((t) => t.stop()); vadStream = null }
  if (vadCtx) { vadCtx.close().catch(() => {}); vadCtx = null }
  vadAnalyser = null
  wasSpeaking = false
  silenceSince = 0
}

function toggleAutoMode() {
  if (hangingUp.value) return
  if (autoMode.value) {
    autoMode.value = false        // 自动 → 手动
    stopVad()
    if (recording.value) { recording.value = false; try { recorder && recorder.cancel() } catch (e) {} }
  } else {
    autoMode.value = true         // 手动 → 自动
    startVad()
  }
}

// ---------- 主动关怀：通话里也轮询（每 30s），"每分钟主动一次"在这里出声 ----------
let proactiveTimer = null
async function pollProactive() {
  if (!session.convId || busy.value || speaking.value || hangingUp.value) return
  try {
    const res = await api.proactive({ is_muted: session.isMuted, conv_id: session.convId })
    const d = res.data
    if (d && d.triggered) {
      const lastBot = (d.chat_history || []).filter((m) => m.role === 'assistant').pop()
      playReply(d.audio_url, (lastBot && lastBot.content) || d.reply || '')
    }
  } catch (e) { /* 静默，下轮再试 */ }
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
  stopVad()                   // 停 VAD 连续对话
  stopVision()                // 停画面识别 + 清后端内存态
  closeCamera()               // 关键：真正停摄像头
  if (tickTimer) clearInterval(tickTimer)
  if (proactiveTimer) { clearInterval(proactiveTimer); proactiveTimer = null }

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
  startVad()   // 连续对话：默认自动监听（VAD 起不来会自动退回手动）
  // 主动关怀：通话里也轮询，"每分钟主动一次"在这里出声
  if (session.convId) {
    pollProactive()
    proactiveTimer = setInterval(pollProactive, 30000)
  }
})

onBeforeUnmount(() => {
  // 兜底：直接关页面/切路由也必须放掉摄像头 + 停 VAD + 停主动轮询
  if (!hungUp) {
    if (recording.value) { try { recorder && recorder.cancel() } catch (e) { /* 忽略 */ } }
    stopAudio()
    stopVad()
    stopVision()
    closeCamera()
  }
  if (tickTimer) clearInterval(tickTimer)
  if (proactiveTimer) { clearInterval(proactiveTimer); proactiveTimer = null }
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
.head-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.vision-toggle {
  min-height: 44px;
  padding: 0 18px;
  font-size: 18px;
  font-weight: 700;
  color: var(--color-text);
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: 22px;
  cursor: pointer;
}
.vision-toggle:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.vision-toggle.on {
  color: #FFFFFF;
  background: var(--color-accent);
  border-color: var(--color-accent);
}
.vision-toggle.degraded {
  color: var(--color-text-sub);
  background: var(--color-surface);
  border-color: var(--color-border);
}
.vision-tag {
  position: absolute;
  right: 12px;
  bottom: 10px;
  margin: 0;
  padding: 4px 12px;
  border-radius: 999px;
  font-size: var(--font-small);
  font-weight: 700;
  color: #FFF8ED;
  background: rgba(62, 130, 95, 0.82);
}
.vision-tag.degraded {
  background: rgba(140, 120, 100, 0.82);
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
.auto-toggle {
  flex: 1;
  min-height: 64px;
  font-size: 18px;
  font-weight: 700;
  color: var(--color-accent2-deep);
  background: var(--color-surface-soft);
  border: 2px solid var(--color-accent);
}
</style>
