<template>
  <div class="page policy-page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/privacy')">← 返回</button>
      <span class="topbar-title">🔒 隐私说明</span>
    </header>

    <div class="content">
      <!-- 开头：说明这是什么、怎么用；整篇朗读入口 -->
      <section class="card intro-card">
        <h1 class="policy-title">小忆是怎么记住您的话的</h1>
        <p class="policy-sub">
          一共七条，都是大白话。看不清可以让小忆念给您听，有不放心的地方就问。
        </p>
        <p class="draft-tip">当前版本：{{ POLICY_VERSION }} · {{ LEGAL_STATUS }}</p>
        <div class="read-row">
          <button class="btn btn-secondary read-btn" type="button" @click="toggleReadAll">
            {{ readingAll ? '⏹ 先停一下' : '🔊 从头念给我听' }}
          </button>
        </div>
        <p v-if="ttsError" class="tts-tip">{{ ttsError }}</p>
      </section>

      <!-- 已同意提示：只影响提示，不隐藏内容 —— 随时能重新看 -->
      <section v-if="consent" class="card consent-card">
        <p class="consent-txt">
          ✅ 您在 {{ consentTime }} 说过"我懂了"。这一版还在律师审查中，正式版出来后小忆会再问您一次。
        </p>
      </section>

      <!-- 条款：大字；每条可单读、可点"听不懂"看解释 -->
      <section
        v-for="(c, i) in CLAUSES"
        :key="c.id"
        class="card clause-card"
        :class="{ reading: readingId === c.id }"
      >
        <h2 class="clause-title">
          <span class="clause-no">{{ i + 1 }}</span>{{ c.title }}
        </h2>
        <p class="clause-text">{{ c.text }}</p>

        <div class="clause-actions">
          <button class="btn btn-secondary small-btn" type="button" @click="readOne(c)">
            🔊 念这一条
          </button>
          <button class="btn btn-secondary small-btn" type="button" @click="explainOne(c)">
            ❓ 听不懂，问问小忆
          </button>
        </div>

        <div v-if="openIds[c.id]" class="explain-box">
          <p class="explain-label">小忆的大白话</p>
          <p class="explain-text">{{ c.explain }}</p>
        </div>
      </section>

      <!-- 底部操作：我懂了 / 我还有疑问 -->
      <section class="card agree-card">
        <h2 class="section-title">您看明白了吗？</h2>
        <p class="agree-sub">看明白了就点"我懂了"；还有不放心的地方，点"我还有疑问"，小忆一条一条用大白话再讲一遍。</p>
        <div class="agree-row">
          <button class="btn btn-primary agree-btn" type="button" @click="agree">
            ✅ 我懂了
          </button>
          <button class="btn btn-secondary agree-btn" type="button" @click="askAll">
            ❓ 我还有疑问
          </button>
        </div>
        <p v-if="agreeMsg" class="agree-tip">{{ agreeMsg }}</p>
        <p v-else-if="consent" class="agree-tip ok">
          您已经同意过这一版，可以放心用；想再看一遍随时回来。
        </p>
      </section>

      <div class="bottom-gap" />
    </div>

    <!-- 正在念的内容：大字显示，听不清也能看 -->
    <div v-if="readingText" class="reading-bar">
      <p class="reading-bar-txt">{{ readingText }}</p>
    </div>

    <audio ref="audioEl" class="hidden-audio" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { CLAUSES, CONSENT_KEY, LEGAL_STATUS, POLICY_VERSION, READ_INTRO, formatTime } from '../data/privacyPolicy'

const audioEl = ref(null)

// 正在念：readingId 用于高亮对应条款，readingText 用于底部大字
const readingId = ref('')
const readingText = ref('')
const readingAll = ref(false)
const ttsError = ref('')
let readToken = 0

// 解释库的展开状态（按条款 id 记，点一次就常开，老人不用来回点）
const openIds = reactive({})

// 同意记录：只存版本和时间
const consent = ref(null)
const agreeMsg = ref('')

const consentTime = ref('')

onMounted(() => {
  try {
    const rec = JSON.parse(localStorage.getItem(CONSENT_KEY) || 'null')
    if (rec && rec.at) {
      consent.value = rec
      consentTime.value = formatTime(rec.at)
    }
  } catch (e) {
    console.warn('[privacy] 读取同意记录失败', e)
  }
})

onBeforeUnmount(() => {
  stopReading()
})

// ---------------- 朗读 -------------
// 每段单独调 /tts（后端会按句切分并拼接），念完一段再念下一段，
// 这样老人随时能停，也能看到"现在念到哪一条"。

function playUrl(url) {
  return new Promise((resolve) => {
    const el = audioEl.value
    if (!el || !url) {
      resolve()
      return
    }
    const done = () => {
      el.onended = null
      el.onerror = null
      resolve()
    }
    el.onended = done
    el.onerror = done
    el.src = url
    el.play().catch(done)
  })
}

async function speakText(text) {
  const res = await api.tts({ text })
  const url = res.data && res.data.audio_url
  if (!url) throw new Error('no_audio')
  await playUrl(url)
}

function stopReading() {
  readToken += 1
  readingId.value = ''
  readingText.value = ''
  readingAll.value = false
  const el = audioEl.value
  if (el) {
    el.pause()
    el.removeAttribute('src')
  }
}

async function readList(items) {
  stopReading()
  const token = readToken
  ttsError.value = ''
  for (const it of items) {
    if (token !== readToken) return
    readingId.value = it.id
    readingText.value = it.text
    try {
      await speakText(it.text)
    } catch (e) {
      if (token !== readToken) return
      ttsError.value = '现在念不出来（语音服务没接上）。您先看大字，或者叫家里人帮您念。'
      break
    }
  }
  if (token === readToken) {
    readingId.value = ''
    readingText.value = ''
    readingAll.value = false
  }
}

function toggleReadAll() {
  if (readingAll.value) {
    stopReading()
    return
  }
  readingAll.value = true
  const items = [
    { id: 'intro', text: READ_INTRO },
    ...CLAUSES.map((c) => ({ id: c.id, text: `${c.title}。${c.text}` })),
  ]
  readList(items)
}

function readOne(c) {
  readingAll.value = false
  readList([{ id: c.id, text: `${c.title}。${c.text}` }])
}

// ---------------- 解释库 -------------

function explainOne(c) {
  openIds[c.id] = true
  readingAll.value = false
  ttsError.value = ''
  // 解释也念出来：视力不好的老人不用盯着屏幕找
  readList([{ id: c.id, text: `您问的是「${c.title}」。${c.explain}` }])
}

function askAll() {
  agreeMsg.value = ''
  for (const c of CLAUSES) openIds[c.id] = true
  const items = CLAUSES.map((c) => ({ id: c.id, text: `「${c.title}」：${c.explain}` }))
  readingAll.value = false
  readList(items)
}

// ---------------- 同意 -------------

function agree() {
  const rec = { version: POLICY_VERSION, at: new Date().toISOString() }
  try {
    localStorage.setItem(CONSENT_KEY, JSON.stringify(rec))
  } catch (e) {
    console.warn('[privacy] 保存同意记录失败', e)
  }
  consent.value = rec
  consentTime.value = formatTime(rec.at)
  agreeMsg.value = '记下了：您说"我懂了"。谢谢您信得过小忆。'
  stopReading()
}
</script>

<style scoped>
.policy-page {
  position: relative;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.policy-title {
  margin: 0 0 10px;
  font-size: 30px;
  font-weight: 800;
  color: var(--color-text);
  line-height: 1.35;
}
.policy-sub {
  margin: 0 0 10px;
  font-size: var(--font-bubble);
  color: var(--color-text-sub);
  line-height: 1.7;
}
.draft-tip {
  margin: 0 0 14px;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.read-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.read-btn {
  font-size: var(--font-bubble);
}
.tts-tip {
  margin: 12px 0 0;
  font-size: var(--font-body);
  color: var(--color-danger);
}
.consent-card {
  border-color: var(--color-accent2);
  border-left-width: 6px;
}
.consent-txt {
  margin: 0;
  font-size: var(--font-bubble);
  color: var(--color-accent2);
  line-height: 1.7;
}
.clause-card {
  border-left: 6px solid var(--color-border);
  transition: border-color 0.2s ease, background 0.2s ease;
}
.clause-card.reading {
  border-left-color: var(--color-accent);
  background: var(--color-surface-soft);
}
.clause-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 0 0 12px;
  font-size: 24px;
  color: var(--color-text);
}
.clause-no {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  font-size: var(--font-body);
  font-weight: 700;
  color: #FFFFFF;
  background: var(--color-accent);
  border-radius: 50%;
}
.clause-text {
  margin: 0 0 14px;
  font-size: 24px;
  line-height: 1.75;
  color: var(--color-text);
}
.clause-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.small-btn {
  font-size: var(--font-body);
  min-height: var(--btn-height);
}
.explain-box {
  margin-top: 14px;
  padding: 14px 16px;
  background: var(--color-surface-soft);
  border-left: 5px solid var(--color-accent2);
  border-radius: var(--radius-sm);
}
.explain-label {
  margin: 0 0 6px;
  font-size: var(--font-small);
  font-weight: 700;
  color: var(--color-accent2);
}
.explain-text {
  margin: 0;
  font-size: 22px;
  line-height: 1.75;
  color: var(--color-text);
}
.agree-card {
  border-color: var(--color-accent);
}
.section-title {
  margin: 0 0 10px;
  font-size: 24px;
  color: var(--color-text);
}
.agree-sub {
  margin: 0 0 16px;
  font-size: var(--font-bubble);
  line-height: 1.7;
  color: var(--color-text-sub);
}
.agree-row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.agree-btn {
  flex: 1;
  min-width: 200px;
  min-height: 60px;
  font-size: 22px;
}
.agree-tip {
  margin: 14px 0 0;
  font-size: var(--font-body);
  color: var(--color-text);
}
.agree-tip.ok {
  color: var(--color-accent2);
}
.bottom-gap {
  height: 8px;
}
.reading-bar {
  position: sticky;
  bottom: 0;
  padding: 14px 20px;
  background: var(--color-accent2);
  border-top: 2px solid var(--color-accent2-deep);
}
.reading-bar-txt {
  margin: 0 auto;
  max-width: 760px;
  font-size: var(--font-bubble);
  line-height: 1.7;
  color: #FFFFFF;
}
.hidden-audio {
  display: none;
}
</style>
