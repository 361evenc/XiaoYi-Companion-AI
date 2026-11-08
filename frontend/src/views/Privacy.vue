<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🔒 隐私设置</span>
    </header>

    <div class="content">
      <!-- F-3：隐私政策大字版入口（能念给老人听，有不明白的可逐条问） -->
      <section class="card entry-card">
        <h2 class="section-title">小忆怎么记您的话</h2>
        <p class="entry-sub">大字版，能念给您听；有不放心的地方，一条一条问小忆。</p>
        <button class="btn btn-primary entry-btn" type="button" @click="$router.push('/privacy/policy')">
          📖 打开隐私说明
        </button>
        <p v-if="consentText" class="entry-consent">✅ 您已在 {{ consentText }} 说过“我懂了”。</p>
      </section>

      <!-- F-7 子女远程安心：老人始终知道孩子能看到什么，也能随时关（关掉不影响自己用） -->
      <section class="card child-watch-card">
        <h2 class="section-title">孩子能看到什么</h2>
        <p class="watch-text">
          孩子能看到您的<b>吃药和心情概况</b>，<b>看不到聊天内容</b>。
        </p>
        <p class="watch-state" :class="{ off: !childEnabled }">
          {{ childEnabled ? '现在开着：孩子能看到这些概况。' : '现在关着：孩子看不到您的概况。' }}
        </p>
        <button
          class="btn"
          :class="childEnabled ? 'btn-danger' : 'btn-secondary'"
          type="button"
          :disabled="childSaving || childLoading"
          @click="toggleChildWatch"
        >
          {{ childSaving ? '正在改…' : childEnabled ? '关掉，不让孩子看' : '打开，让孩子看' }}
        </button>
        <p class="watch-note">关掉只是孩子看不到，您该提醒还提醒、该聊天还聊天。</p>
        <p v-if="childMsg" class="watch-msg">{{ childMsg }}</p>
      </section>

      <section class="card">
        <h2 class="section-title">忘掉一件事</h2>
        <div class="row">
          <input v-model.trim="forgetText" class="input" placeholder="例如：忘掉我腿疼的事" />
          <button class="btn btn-secondary" type="button" :disabled="forgetting" @click="doForget">
            {{ forgetting ? '正在忘…' : '忘掉这件事' }}
          </button>
        </div>
        <p v-if="result" class="result-tip" :class="{ ok: resultOk }">{{ result }}</p>
      </section>

      <section class="card danger-zone">
        <h2 class="section-title">清空全部记忆</h2>
        <p class="warn-text">这会删掉所有记忆、聊天记录和提醒，删了找不回来。</p>
        <button class="btn btn-danger" type="button" :disabled="wiping" @click="confirmWipe">
          {{ wiping ? '正在清空…' : '🗑️ 清空全部记忆' }}
        </button>
        <p v-if="wipeMsg" class="result-tip">{{ wipeMsg }}</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { api } from '../api'
import { CONSENT_KEY, formatTime } from '../data/privacyPolicy'

const router = useRouter()
const session = useSessionStore()

// F-3：入口卡上显示"已经同意过"的时间（同意记录只存在本机）
const consentText = ref('')
try {
  const rec = JSON.parse(localStorage.getItem(CONSENT_KEY) || 'null')
  if (rec && rec.at) consentText.value = formatTime(rec.at)
} catch (e) {
  console.warn('[privacy] 读取同意记录失败', e)
}

const forgetText = ref('')
const forgetting = ref(false)
const result = ref('')
const resultOk = ref(false)
const wiping = ref(false)
const wipeMsg = ref('')

// F-7：老人端也能独立关掉「子女远程安心」（默认开；关掉不影响自己用）
const childEnabled = ref(false)
const childLoading = ref(true)
const childSaving = ref(false)
const childMsg = ref('')

async function loadChildWatch() {
  childLoading.value = true
  try {
    const res = await api.childConsent()
    childEnabled.value = !!(res.data && res.data.enabled)
  } catch (e) {
    childMsg.value = '没读到开关状态，请确认后端已启动。'
  } finally {
    childLoading.value = false
  }
}

async function toggleChildWatch() {
  if (childSaving.value || childLoading.value) return
  childSaving.value = true
  childMsg.value = ''
  try {
    const res = await api.setChildConsent({ enabled: !childEnabled.value })
    childEnabled.value = !!(res.data && res.data.enabled)
    childMsg.value = childEnabled.value
      ? '打开了，孩子能看到您的吃药和心情概况，还是看不到聊天内容。'
      : '关上了，孩子看不到您的概况了。'
  } catch (e) {
    childMsg.value = '没改成功，请稍后再试。'
  } finally {
    childSaving.value = false
  }
}

onMounted(loadChildWatch)

async function doForget() {
  const text = forgetText.value.trim()
  if (!text || forgetting.value) return
  forgetting.value = true
  result.value = ''
  try {
    const res = await api.forget({ text })
    resultOk.value = !!res.data.ok
    result.value = res.data.message || (res.data.ok ? '忘掉啦。' : '没找到这件事。')
    if (res.data.ok) forgetText.value = ''
  } catch (e) {
    console.error(e)
    resultOk.value = false
    result.value = '操作失败，请稍后再试。'
  } finally {
    forgetting.value = false
  }
}

async function confirmWipe() {
  if (wiping.value) return
  // 防误触：二次确认
  if (!window.confirm('确定要清空所有记忆吗？这个操作无法撤销。')) return
  wiping.value = true
  wipeMsg.value = ''
  try {
    const res = await api.wipeMemory({ is_muted: session.isMuted })
    if (!res.data.ok) {
      wipeMsg.value = res.data.message || '清空失败，请稍后再试。'
      return
    }
    // 清空成功：本地会话作废，回欢迎页重新认识
    session.setConversation(null, [])
    session.setConvId(null)
    session.initialized = false
    session.save()
    alert(res.data.message || '全都删干净啦。')
    router.push('/')
  } catch (e) {
    console.error(e)
    wipeMsg.value = '清空失败，请稍后再试。'
  } finally {
    wiping.value = false
  }
}
</script>

<style scoped>
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-width: 720px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.section-title {
  margin: 0 0 14px;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.entry-card {
  border-left: 6px solid var(--color-accent2);
}
.entry-sub {
  margin: 0 0 14px;
  font-size: var(--font-body);
  line-height: 1.7;
  color: var(--color-text-sub);
}
.entry-btn {
  min-height: 56px;
  font-size: var(--font-bubble);
}
.entry-consent {
  margin: 12px 0 0;
  font-size: var(--font-body);
  color: var(--color-accent2);
}
.row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.row .input {
  flex: 1;
  min-width: 200px;
}
.danger-zone {
  border-color: var(--color-danger);
}
.warn-text {
  color: var(--color-danger);
  font-size: var(--font-body);
  margin: 0 0 14px;
}
.result-tip {
  margin: 12px 0 0;
  font-size: var(--font-body);
  color: var(--color-danger);
}
.result-tip.ok {
  color: var(--color-accent2);
}
.child-watch-card {
  border-left: 6px solid var(--color-accent);
}
.watch-text {
  margin: 0 0 10px;
  font-size: var(--font-body);
  line-height: 1.7;
  color: var(--color-text);
}
.watch-state {
  margin: 0 0 14px;
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-accent2);
}
.watch-state.off {
  color: var(--color-danger);
}
.watch-note {
  margin: 12px 0 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.watch-msg {
  margin: 10px 0 0;
  font-size: var(--font-body);
  color: var(--color-accent2);
}
</style>