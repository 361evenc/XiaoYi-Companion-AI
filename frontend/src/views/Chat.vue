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
        <!-- F-7 子女远程安心：首次激活，大字 + 语音明确告诉老人孩子能看到什么 -->
        <div v-if="careNotice" class="care-notice" role="alertdialog" aria-live="assertive">
          <p class="care-notice-title">跟您说个事儿</p>
          <p class="care-notice-text">
            您的孩子能看到您的<b>吃药和心情概况</b>，<b>看不到聊天内容</b>。您要是不同意，可以随时关掉。
          </p>
          <div class="care-notice-actions">
            <button type="button" class="btn btn-primary" @click="acceptCareNotice">我知道了</button>
            <button type="button" class="btn btn-secondary" @click="declineCareNotice">不让看</button>
          </div>
        </div>

        <!-- F-2：录制状态文字提示 + 一键停；首次进入先告知再开 -->
        <VisionStatus ref="visionRef" />

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

        <!-- F-8 方言：能听懂的才可选；还没学会的点了就诚实说「还没学会」，不硬上 -->
        <div class="voice-toolbar">
          <span class="voice-label">小忆听</span>
          <div class="persona-row">
            <button
              type="button"
              class="persona-chip"
              :class="{ on: session.dialect === '普通话' }"
              @click="chooseDialect('普通话')"
            >
              普通话
            </button>
            <button
              v-for="d in dialectOptions"
              :key="d.name"
              type="button"
              class="persona-chip"
              :class="{ on: session.dialect === d.name, 'dialect-not-yet': !d.supported }"
              :title="d.supported ? '' : d.reason"
              @click="chooseDialect(d.name)"
            >
              {{ d.name }}
              <span v-if="!d.supported" class="not-yet-tag">还在学</span>
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

        <!-- 找物结果卡：语音先说、屏幕后展示；下一轮对话自动收起 -->
        <FindObjectCard
          v-if="findResult && findVisible"
          :data="findResult"
        />

        <!-- F-5 反向索求任务卡：大字请老人说天气；前奏音只在“好事”播报前响 -->
        <ReverseAskCard
          :answered="weatherAnswered"
          :answer-text="weatherAnswer"
          @ask="askWeather"
        />

        <!-- F-6 拍后反馈：只提示“太暗 / 太糊”；可以重拍，也允许照发 -->
        <div v-if="photoWarn" class="photo-warn card" aria-live="polite">
          <span class="photo-warn-icon" aria-hidden="true">
            {{ photoWarn.verdict === 'dark' ? '💡' : '📷' }}
          </span>
          <p class="photo-warn-text">{{ photoWarn.message }}</p>
          <div class="photo-warn-actions">
            <button type="button" class="btn btn-primary" @click="retakePhoto">
              📸 重新拍一张
            </button>
            <button type="button" class="btn btn-secondary" @click="sendPendingPhoto">
              还是要发
            </button>
          </div>
        </div>

        <audio ref="audioEl" class="hidden-audio" @ended="audioPlaying = false" />

        <InputCard
          ref="inputRef"
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
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { api } from '../api'
import SidebarCards from '../components/SidebarCards.vue'
import ChatBubble from '../components/ChatBubble.vue'
import FindObjectCard from '../components/FindObjectCard.vue'
import InputCard from '../components/InputCard.vue'
import VisionStatus from '../components/VisionStatus.vue'
import ReverseAskCard from '../components/ReverseAskCard.vue'
import { warm } from '../services/warmDependency'
import { photoGuide } from '../data/photoGuide'

const session = useSessionStore()
const route = useRoute()

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
const visionRef = ref(null)
const inputRef = ref(null)

// F-6 拍后反馈：存着待发送的那张图（老人选"重新拍"就丢弃，选"还是要发"才发）
const photoWarn = ref(null)

// F-5：反向索求（天气）当天状态，从本地契约读，刷新页面也不丢
const weatherAnswered = ref(warm.weatherAnsweredToday())
const weatherAnswer = ref(warm.getWeatherAnswer())

// F-7：首次激活的告知开关（大字 + 语音；老人始终知道孩子能看到什么）
const careNotice = ref(false)

// F-8：方言选项全部来自后端闸门；supported=false 的只展示「还在学」，不可切过去
const dialectOptions = ref([])

async function loadDialectConfig() {
  try {
    const res = await api.dialectConfig()
    const d = res.data || {}
    dialectOptions.value = d.options || []
    // 之前选过的方言如果已经不在上线清单（闸门收紧），回到普通话
    const cur = dialectOptions.value.find((o) => o.name === session.dialect)
    if (session.dialect !== '普通话' && (!cur || !cur.supported)) {
      session.setDialect('普通话')
    }
  } catch (e) {
    // 方言配置读不到不影响聊天：保持普通话，下一轮再取
    console.warn('[dialect] 读取方言配置失败', e)
  }
}

function chooseDialect(name) {
  if (name === session.dialect) return
  const opt = dialectOptions.value.find((o) => o.name === name)
  if (!opt) {
    session.setDialect(name)
    return
  }
  if (!opt.supported) {
    // 诚实说：还没学会就是没学会，不假装听懂；继续用普通话
    const reply = `我还没学会听${name}，等我学好了。咱先用普通话聊，好不好？`
    messages.value.push({ role: 'assistant', content: reply })
    session.chatHistory = messages.value
    speakLocal(reply)
    return
  }
  session.setDialect(name)
  speakLocal(`${name}，我听着呢。您说吧。`)
}

// 找物结果卡：只在命中找物时出现，且等语音先开口后再展示
const findResult = ref(null)
const findVisible = ref(false)
let findRevealTimer = null

function clearFindCard() {
  if (findRevealTimer) {
    clearTimeout(findRevealTimer)
    findRevealTimer = null
  }
  findResult.value = null
  findVisible.value = false
}

function revealFindCard(d) {
  if (!d || !d.ok || !d.matched) return
  // F-5：找物成功是正反馈事件，前奏音 1:1 —— 只在命中时响一次
  warm.announcePositive('find_object_ok')
  findResult.value = d
  // 语音在前、屏幕在后：给播报留出起播时间
  findRevealTimer = setTimeout(() => {
    findVisible.value = true
    findRevealTimer = null
  }, 600)
}

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

// 本地话术（不经过 /chat）：只用于立即回应急停指令
async function speakLocal(text) {
  caption.value = text
  if (session.isMuted) return
  try {
    const res = await api.tts({ text })
    playAudio(res.data && res.data.audio_url, text)
  } catch (e) {
    playAudio(null, text)
  }
}

// 反向索求：小忆先问一句。这是请求、不是“好事”，所以不放前奏音
function askWeather() {
  speakLocal('您能告诉我今天天气怎么样吗？怎么说都行，我听着。')
}

// ---------- F-7 子女远程安心：首次告知 + 购药回执通知 ----------

async function initChildLink() {
  // 首次激活：大字 + 语音告知（后端默认开；老人点「我知道了」或「不让看」都记一次）
  try {
    const res = await api.childConsent()
    const c = res.data || {}
    if (c.enabled && !c.notified) {
      careNotice.value = true
      speakLocal(c.notice_text || '您的孩子能看到您的吃药和心情概况，看不到聊天内容。')
    }
  } catch (e) {
    // 后端没起来不挡聊天；下次进入再告知
    console.warn('[child] 读取安心开关失败', e)
  }
  await pollChildNotices()
}

function acceptCareNotice() {
  careNotice.value = false
  api.childNotified().catch(() => {})
  speakLocal('好，就这样。您要是想关，到“隐私设置”里点一下就行。')
}

async function declineCareNotice() {
  careNotice.value = false
  try {
    await api.setChildConsent({ enabled: false })
    await api.childNotified()
  } catch (e) {
    console.warn('[child] 关闭安心开关失败', e)
  }
  speakLocal('好，我已经关上了，孩子看不到这些了。')
}

// 购药回执通知：子女处理完后，老人端播报一次「孩子给您买了药」并标已读
async function pollChildNotices() {
  try {
    const res = await api.childNotices()
    const list = ((res.data && res.data.notices) || []).filter((n) => !n.acked)
    for (const n of list.slice(0, 2)) {
      messages.value.push({ role: 'assistant', content: n.text })
      session.chatHistory = messages.value
      playChime()
      speakLocal(n.text)
      await api.childNoticeAck(n.id).catch(() => {})
    }
  } catch (e) {
    /* 静默失败，不挡对话 */
  }
}

// 天气类说法都算老人帮了忙；一天只记一次，前奏音也只响这一次
const WEATHER_RE = /(天气|下雨|下雪|刮风|晴天|阴天|多云|温度|冷不冷|热不热|降温|升温)/

async function noteWeatherIfAny(text) {
  if (warm.weatherAnsweredToday()) return
  if (!WEATHER_RE.test(text)) return
  warm.markWeatherAnswered(text)
  weatherAnswer.value = text
  weatherAnswered.value = true
  // 前奏音先响、再播报回复：await 保证两路声音不叠在一起
  await warm.announcePositive('weather_answered')
}

async function sendMessage(text) {
  const t = (text || '').trim()
  if (!t || loading.value) return

  // F-5 语音层防沉迷：整句“再放一个”由本地契约接管，不交给对话模型
  //（有额度就放；到上限只回 T-6 固定话术；两种路径都不含禁用句式）
  const replay = warm.handleVoiceRequest(t)
  if (replay) {
    messages.value.push({ role: 'user', content: t })
    messages.value.push({ role: 'assistant', content: replay.phrase })
    session.chatHistory = messages.value
    speakLocal(replay.phrase)
    return
  }

  loading.value = true
  error.value = ''
  clearFindCard()
  clearPhotoWarn()
  await noteWeatherIfAny(t)
  // 与聊天并行问一次找物接口：只读、确定性，不写记忆；不在找物句式时 matched=false
  const findPromise = api.findObject({ text: t }).catch(() => null)
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
    const fr = await findPromise
    revealFindCard(fr && fr.data)
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

// 发送前压缩：手机原图动辄 5-10MB（base64 后更大），上传慢且识别链路
// 实测 40s+ 必超时；压到长边 1280 / JPEG 75% 后体积降一个量级，识别精度足够
function compressImage(base64, maxSide = 1280, quality = 0.75) {
  return new Promise((resolve) => {
    try {
      const img = new Image()
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height))
        if (scale >= 1) return resolve(base64)   // 本来就小，不压
        const canvas = document.createElement('canvas')
        canvas.width = Math.round(img.width * scale)
        canvas.height = Math.round(img.height * scale)
        canvas.getContext('2d').drawImage(img, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', quality).split(',')[1])
      }
      img.onerror = () => resolve(base64)   // 压缩失败用原图，别挡着发不出
      img.src = `data:image/jpeg;base64,${base64}`
    } catch (e) {
      resolve(base64)
    }
  })
}

// 拍照/上传图片：压缩 → F-6 拍中反馈 → 发给后端（存记忆箱 + 视觉识别 + 注入对话）
async function onPhoto({ base64, mime }) {
  if (loading.value) return
  loading.value = true
  error.value = ''
  clearFindCard()
  clearPhotoWarn()
  try {
    base64 = await compressImage(base64)
    // F-6：检测用的是压缩后的同一份 base64，复用现有压缩管线，不另传一张原图
    if (photoGuide.enabled()) {
      const q = await checkPhotoQuality(base64, mime || 'image/jpeg')
      if (q && q.verdict !== 'ok') {
        photoWarn.value = {
          verdict: q.verdict,
          message: photoGuide.feedback(q.verdict),
          photo: { base64, mime: mime || 'image/jpeg' },
        }
        return
      }
    }
    await sendPhoto(base64, mime)
  } catch (e) {
    console.error(e)
    error.value = '照片发送失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}

// 只问"能不能拿去看"（太暗/太糊），异常时按原图发，绝不因为检测失败卡住老人
async function checkPhotoQuality(base64, mime) {
  try {
    const res = await api.photoQuality({ photo_base64: base64, mime })
    return res.data || null
  } catch (e) {
    console.warn('拍照质量检测失败，按原图发送', e)
    return null
  }
}

async function sendPhoto(base64, mime) {
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
}

function clearPhotoWarn() {
  photoWarn.value = null
}

// "重新拍一张"：丢弃这张，直接调起相机（InputCard 暴露 openCamera）
function retakePhoto() {
  clearPhotoWarn()
  inputRef.value?.openCamera()
}

// "还是要发"：老人有权不重拍，照发；失败只提示，不反复拦
async function sendPendingPhoto() {
  const p = photoWarn.value && photoWarn.value.photo
  if (!p || loading.value) return
  clearPhotoWarn()
  loading.value = true
  error.value = ''
  try {
    await sendPhoto(p.base64, p.mime)
  } catch (e) {
    console.error(e)
    error.value = '照片发送失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}

// 录音结束：ASR 识别 → 文本 → 复用文字发送链路
// F-2：整句"别看了"截在前端，直接停视觉，不交给聊天模型
// 带区域的"卧室别看了"不匹配，仍走 B-4 的免看清单
const STOP_VISION_RE =
  /^(小忆)?[，,。！!\s]*(别看了|不要看了|别再看了|你别看了|关掉摄像头|把摄像头关了|摄像头关了吧)[，,。！!\s]*$/

async function handleVoiceStop() {
  clearFindCard()
  const r = (await visionRef.value?.stopByVoice()) || { verified: 'fault' }
  let reply = '好，我不看了。您想让我看，跟我说一声。'
  if (r.alreadyOff) {
    reply = '我现在本来就没在看，您放心。'
  } else if (r.verified === 'fault' || r.verified === 'unknown') {
    reply = '我没法确定摄像头停了，请重启一下电脑。'
  } else if (r.verified === 'recording') {
    reply = '我还在看，没能停下来。'
  }
  messages.value.push({ role: 'assistant', content: reply })
  session.chatHistory = messages.value
  speakLocal(reply)
}

async function onRecorderAudio(audioBase64) {
  try {
    const res = await api.asr({ audio_base64: audioBase64, dialect: session.dialect })
    const d = res.data || {}
    // F-8：后端闸门没放行的方言，不把残文当老人原话，直说「还没学会」
    if (d.refused) {
      const reply = d.reply || '我还没学会听这个方言，等我学好了。'
      messages.value.push({ role: 'assistant', content: reply })
      session.chatHistory = messages.value
      speakLocal(reply)
      return
    }
    const text = (d.text || '').trim()
    if (!text) {
      error.value = '没听清，请再说一遍。'
      return
    }
    if (STOP_VISION_RE.test(text)) {
      await handleVoiceStop()
      return
    }
    await sendMessage(text)
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
  initChildLink()
  loadDialectConfig()
  proactiveTimer = setInterval(pollProactive, 30000)
  // 从"拍照小助手"点"知道了，去拍照"过来：直接把相机打开
  if (route.query.camera === '1') {
    nextTick(() => inputRef.value?.openCamera())
  }
})

onBeforeUnmount(() => {
  if (ws) {
    ws.onclose = null
    ws.close()
    ws = null
  }
  if (wsRetryTimer) clearTimeout(wsRetryTimer)
  if (proactiveTimer) clearInterval(proactiveTimer)
  clearFindCard()
  stopAudio()
})

// 撤回最后一轮：后端删对话末轮 + 调记忆清除钩子，前端用返回历史整体替换
async function recallLast() {
  if (!session.convId || loading.value) return
  loading.value = true
  error.value = ''
  clearFindCard()
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
.dialect-not-yet {
  color: var(--color-text-sub);
  border-style: dashed;
}
.not-yet-tag {
  margin-left: 6px;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.photo-warn {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 0 20px 10px;
  padding: 14px 16px;
  border-left: 6px solid var(--color-accent);
}
.care-notice {
  margin: 14px 20px 0;
  padding: 18px 20px;
  background: var(--color-surface-soft);
  border: 2px solid var(--color-accent);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.care-notice-title {
  margin: 0;
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-accent-deep);
}
.care-notice-text {
  margin: 0;
  font-size: 24px;
  line-height: 1.6;
  color: var(--color-text);
}
.care-notice-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.photo-warn-icon {
  flex-shrink: 0;
  font-size: 30px;
  line-height: 1;
}
.photo-warn-text {
  flex: 1;
  margin: 0;
  font-size: var(--font-body);
  line-height: 1.55;
  color: var(--color-text);
}
.photo-warn-actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
@media (max-width: 768px) {
  .photo-warn {
    flex-wrap: wrap;
    margin: 0 12px 8px;
  }
  .photo-warn-actions {
    width: 100%;
    flex-direction: row;
  }
  .photo-warn-actions .btn {
    flex: 1;
  }
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