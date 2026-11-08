<template>
  <section class="vision-card" :class="toneClass" aria-live="polite">
    <div class="vision-main">
      <div class="vision-copy">
        <p class="vision-title">{{ stateText }}</p>
        <p class="vision-audio">声音：{{ audioText }}</p>
        <p class="vision-sub">{{ subText }}</p>
      </div>

      <button
        v-if="canStop"
        class="btn btn-secondary vision-btn"
        type="button"
        :disabled="busy"
        @click="stop"
      >
        {{ busy ? '正在停…' : '🛑 别看了' }}
      </button>
      <button
        v-else-if="canStart"
        class="btn btn-primary vision-btn"
        type="button"
        :disabled="busy"
        @click="start"
      >
        {{ busy ? '正在开…' : startLabel }}
      </button>
      <button v-else class="btn btn-secondary vision-btn" type="button" disabled>
        {{ busy ? '正在…' : '请稍等' }}
      </button>
    </div>

    <p v-if="error" class="vision-error">{{ error }}</p>

    <button
      class="impact-toggle"
      type="button"
      :aria-expanded="impactOpen"
      @click="impactOpen = !impactOpen"
    >
      关掉以后，哪些功能会受影响？{{ impactOpen ? '（点我收起）' : '（点我看看）' }}
    </button>

    <ul v-if="impactOpen" class="impact-list">
      <li><strong>找物</strong>：只能回答“您上次说放在…”，看不到新位置</li>
      <li><strong>跌倒告警</strong>：不可用</li>
      <li><strong>活动水平</strong>：不可用</li>
      <li><strong>识图</strong>：仍然可用，拍照问小忆不受影响</li>
    </ul>
  </section>

  <!-- 首次激活告知：大字 + 语音，确认后才真正开摄像头 -->
  <Teleport to="body">
    <div
      v-if="showNotice"
      class="notice-mask"
      role="dialog"
      aria-modal="true"
      aria-labelledby="vision-notice-title"
    >
      <div class="notice-box">
        <h2 id="vision-notice-title" class="notice-title">小忆想跟您说件事</h2>
        <p class="notice-text">
          小忆会一直看着客厅，这样她才能帮您找东西。要是不想让她看，您说“别看了”，她就马上停。
        </p>
        <p class="notice-text notice-text-soft">
          小忆现在不听声音，只在您跟她说话的时候才听。
        </p>
        <div class="notice-actions">
          <button class="btn btn-primary notice-btn" type="button" @click="acceptNotice">
            知道了，让小忆看
          </button>
          <button class="btn btn-secondary notice-btn" type="button" @click="declineNotice">
            先别看了
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const NOTICE_KEY = 'xiaoyi.vision.notice.v1'
const CHOICE_KEY = 'xiaoyi.vision.choice.v1'
const NOTICE_TEXT =
  '小忆会一直看着客厅，这样她才能帮您找东西。要是不想让她看，您说“别看了”，她就马上停。'

const status = reactive({
  state: 'idle',
  audio_on: false,
  confirmed_off: false,
  detail: '',
})
const reachable = ref(true)
const busy = ref(false)
const error = ref('')
const showNotice = ref(false)
const impactOpen = ref(false)
// 只有 stop 接口返回 verified=released，才允许说"我现在不看啦"
const verifiedOff = ref(false)
// 摄像头被验证过已释放（可能是状态轮询补验的，不用于"我主动关掉"话术）
const hardwareReleased = ref(false)
// 故障来源：start=开不起来，stop/verify=停不下来，两者话术不同
const faultReason = ref('stop')

let pollTimer = null
let noticeAudio = null
let verifying = false

// 连不上后端时不能猜"已停"，一律按"没法确定"处理
const currentState = computed(() => (reachable.value ? status.state : 'unknown'))

const stateText = computed(() => {
  switch (currentState.value) {
    case 'recording':
      return '小忆在看'
    case 'released':
    case 'idle':
      return '没在看'
    case 'closing':
      return '正在切换…'
    default:
      return '我没法确定'
  }
})

// released 只在 H-2 verify() 返回 released 后出现，才允许说"我现在不看啦"
const subText = computed(() => {
  switch (currentState.value) {
    case 'recording':
      return '小忆在看着客厅，帮您找东西。'
    case 'released':
      if (verifiedOff.value) return '我现在不看啦，您想让我看跟我说。'
      if (hardwareReleased.value) return '没在看，摄像头已经确认释放了。'
      return '没在看，正在跟摄像头确认是不是真停了。'
    case 'fault':
      return faultReason.value === 'start'
        ? '摄像头没打开，我没法确定小忆在不在看。'
        : '我没法确定摄像头停了，请重启一下电脑。'
    case 'closing':
      return '正在切换，请稍等一下。'
    case 'idle':
      return '还没开始看。'
    default:
      return '连不上录制服务，请检查一下电脑。'
  }
})

const audioText = computed(() =>
  status.audio_on ? '在听' : '没在听（这台电脑还没接麦克风）'
)

const toneClass = computed(() => {
  switch (currentState.value) {
    case 'recording':
      return 'tone-on'
    case 'released':
    case 'idle':
      return 'tone-off'
    case 'closing':
      return 'tone-busy'
    default:
      return 'tone-fault'
  }
})

const canStop = computed(() => currentState.value === 'recording')
const canStart = computed(() =>
  ['released', 'idle', 'fault', 'unknown'].includes(currentState.value)
)
const startLabel = computed(() =>
  currentState.value === 'fault' || currentState.value === 'unknown' ? '再试一次' : '让小忆看'
)

function applyStatus(s) {
  if (!s) return
  status.state = s.state || 'idle'
  status.audio_on = Boolean(s.audio_on)
  status.confirmed_off = Boolean(s.confirmed_off)
  status.detail = s.detail || ''
}

// 状态里出现 released 但本次没走过 stop 验证时，补一次硬件验证，
// 避免把"启动失败后线程退出"当成"老人主动关掉且已确认"
async function confirmOff() {
  if (verifying || busy.value) return
  verifying = true
  try {
    const res = await api.visionVerify()
    applyStatus(res.data.status)
    if (res.data.verified === 'released') {
      status.state = 'released'
      hardwareReleased.value = true
      error.value = ''
    } else if (res.data.verified === 'fault') {
      status.state = 'fault'
      hardwareReleased.value = false
      faultReason.value = 'verify'
      error.value = '我没法确定摄像头停了，请重启一下电脑。'
    } else if (res.data.verified === 'recording') {
      status.state = 'recording'
      hardwareReleased.value = false
    }
  } catch (e) {
    // 验证失败不改变状态，也不把 released 洗成"已停"
  } finally {
    verifying = false
  }
}

async function refresh() {
  try {
    const res = await api.visionStatus()
    applyStatus(res.data)
    reachable.value = true
    if (status.state !== 'released') {
      verifiedOff.value = false
      hardwareReleased.value = false
    }
    if (
      status.state === 'released' &&
      !verifiedOff.value &&
      !hardwareReleased.value
    ) {
      confirmOff()
    }
  } catch (e) {
    reachable.value = false
  }
}

async function start() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  impactOpen.value = false
  status.state = 'closing'
  verifiedOff.value = false
  hardwareReleased.value = false
  try {
    const res = await api.visionStart()
    applyStatus(res.data.status)
    reachable.value = true
    // ok=false 时不能把 released 当成"老人关掉了"：启动根本没成功
    if (res.data.ok !== true && status.state === 'released') {
      status.state = 'fault'
      faultReason.value = 'start'
      error.value = '启动没成功，我没法确定小忆在不在看。'
    } else if (status.state === 'fault') {
      faultReason.value = 'start'
      error.value = '摄像头没打开，我没法确定小忆在不在看。'
    } else if (status.state === 'released') {
      faultReason.value = 'stop'
    }
  } catch (e) {
    console.error('[F-2] 启动录制失败：', e)
    reachable.value = false
    error.value = '启动失败，我没法确定小忆在不在看。'
  } finally {
    busy.value = false
  }
}

async function stop() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  impactOpen.value = false
  status.state = 'closing'
  try {
    const res = await api.visionStop()
    const verified = res.data.verified
    applyStatus(res.data.status)
    reachable.value = true

    // 只按硬件验证结果说话，不按接口的 ok 字段猜
    if (verified === 'released') {
      status.state = 'released'
      verifiedOff.value = true
      hardwareReleased.value = true
      impactOpen.value = true
    } else if (verified === 'fault') {
      verifiedOff.value = false
      hardwareReleased.value = false
      faultReason.value = 'stop'
      status.state = 'fault'
      error.value = '我没法确定摄像头停了，请重启一下电脑。'
      console.warn('[F-2] 停帧验证失败：', res.data.verify_detail)
    } else if (verified === 'recording') {
      status.state = 'recording'
      verifiedOff.value = false
      hardwareReleased.value = false
      error.value = '小忆还在看，没能停下来。'
      console.warn('[F-2] 仍在录制：', res.data.verify_detail)
    } else {
      status.state = 'fault'
      verifiedOff.value = false
      hardwareReleased.value = false
      error.value = '我没法确定摄像头停了，请重启一下电脑。'
      console.warn('[F-2] 未知验证结果：', verified, res.data.verify_detail)
    }
    return { verified, state: status.state }
  } catch (e) {
    console.error('[F-2] 停止录制失败：', e)
    reachable.value = false
    status.state = 'unknown'
    verifiedOff.value = false
    hardwareReleased.value = false
    error.value = '我没法确认摄像头停了，请重启一下电脑。'
    return { verified: 'fault', state: 'unknown' }
  } finally {
    busy.value = false
  }
}

// 语音"别看了"由 Chat.vue 截住后调用；已停/没开始就直接回答，不再触发 verify
async function stopByVoice() {
  if (!reachable.value) {
    return { verified: 'fault', alreadyOff: false }
  }
  if (status.state === 'released' || status.state === 'idle') {
    return { verified: 'released', alreadyOff: true }
  }
  return stop()
}

function speakNotice() {
  api
    .tts({ text: NOTICE_TEXT })
    .then((res) => {
      const url = res.data && res.data.audio_url
      if (!url) return
      noticeAudio = new Audio(url)
      noticeAudio.play().catch(() => {
        /* 浏览器拦截自动播放时保留大字告知 */
      })
    })
    .catch(() => {
      /* 语音失败不影响文字告知 */
    })
}

function rememberNotice(choice) {
  try {
    localStorage.setItem(NOTICE_KEY, '1')
    localStorage.setItem(CHOICE_KEY, choice)
  } catch (e) {
    /* 无 localStorage 时只在本次生效 */
  }
}

function acceptNotice() {
  rememberNotice('on')
  showNotice.value = false
  if (['idle', 'released', 'fault'].includes(currentState.value)) start()
}

function declineNotice() {
  rememberNotice('off')
  showNotice.value = false
  impactOpen.value = true
  if (status.state === 'recording' || status.state === 'closing') stop()
}

onMounted(async () => {
  await refresh()

  if (!localStorage.getItem(NOTICE_KEY)) {
    showNotice.value = true
    speakNotice()
  } else if (localStorage.getItem(CHOICE_KEY) === 'on' && status.state === 'idle') {
    start()
  }

  // 只做轻量状态轮询；verify 会短暂占用摄像头，不能高频调
  pollTimer = setInterval(() => {
    if (!busy.value) refresh()
  }, 10000)
})

onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer)
  if (noticeAudio) {
    noticeAudio.pause()
    noticeAudio = null
  }
})

defineExpose({ stopByVoice })
</script>

<style scoped>
.vision-card {
  flex-shrink: 0;
  margin: 12px 20px 0;
  padding: 14px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: 6px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
}
.vision-card.tone-on {
  border-left-color: #2F6FA8;
  background: #EEF4FB;
}
.vision-card.tone-off {
  border-left-color: #C99A12;
  background: #FFF8E1;
}
.vision-card.tone-fault {
  border-left-color: var(--color-danger);
  background: #FDECEA;
}
.vision-card.tone-busy {
  border-left-color: #8C7B68;
  background: #F4EFE7;
}

.vision-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
.vision-copy {
  min-width: 0;
}
/* 状态以文字为主通道：34px 大字，颜色只作辅助 */
.vision-title {
  margin: 0;
  font-size: 34px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--color-text);
}
/* 第二行大字：在听 / 不听（H-2 当前恒为未开通） */
.vision-audio {
  margin: 4px 0 0;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text);
}
.vision-sub {
  margin: 4px 0 0;
  font-size: 20px;
  line-height: 1.5;
  color: var(--color-text-sub);
}
.vision-btn {
  flex-shrink: 0;
  min-width: 140px;
  min-height: 56px;
  font-size: 20px;
  font-weight: 700;
}

.vision-error {
  margin: 10px 0 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--color-danger);
}

.impact-toggle {
  margin-top: 10px;
  padding: 6px 0;
  min-height: 40px;
  font-size: 18px;
  color: var(--color-accent2-deep);
  background: transparent;
  border: none;
  text-decoration: underline;
  cursor: pointer;
  text-align: left;
}
.impact-list {
  margin: 4px 0 0;
  padding-left: 22px;
  font-size: 18px;
  line-height: 1.7;
  color: var(--color-text);
}
.impact-list li {
  margin-bottom: 2px;
}

.notice-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(45, 27, 14, 0.55);
}
.notice-box {
  width: 100%;
  max-width: 680px;
  padding: 26px 28px;
  background: var(--color-surface);
  border: 3px solid var(--color-accent);
  border-radius: var(--radius-lg);
  box-shadow: 0 10px 40px rgba(45, 27, 14, 0.3);
}
.notice-title {
  margin: 0 0 14px;
  font-size: 30px;
  font-weight: 700;
  color: var(--color-text);
}
.notice-text {
  margin: 0 0 12px;
  font-size: 24px;
  font-weight: 600;
  line-height: 1.6;
  color: var(--color-text);
}
.notice-text-soft {
  font-size: 20px;
  font-weight: 400;
  color: var(--color-text-sub);
}
.notice-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}
.notice-btn {
  flex: 1;
  min-width: 220px;
  min-height: 58px;
  font-size: 20px;
  font-weight: 700;
}

@media (max-width: 900px) {
  .vision-card {
    margin: 8px 10px 0;
  }
  .vision-title {
    font-size: 30px;
  }
  .vision-btn {
    width: 100%;
  }
  .notice-box {
    padding: 20px;
  }
  .notice-title {
    font-size: 26px;
  }
  .notice-text {
    font-size: 21px;
  }
}
</style>
