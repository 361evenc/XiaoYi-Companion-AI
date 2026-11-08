<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/')">← 返回</button>
      <span class="topbar-title">👨‍👩‍👧 子女端 · 远程安心</span>
      <span class="topbar-sub">只看概况</span>
    </header>

    <div class="content">
      <p v-if="loading" class="hint">正在查看老人近况…</p>
      <p v-else-if="error" class="hint err">{{ error }}</p>

      <!-- 关掉之后：只读区整体显示「已关闭」，不显示任何概况 -->
      <section v-if="!loading && !error && !summary.enabled" class="card closed-card">
        <h2 class="section-title">已关闭</h2>
        <p class="closed-text">{{ summary.closed_hint || '老人把「子女远程安心」关上了。' }}</p>
        <p class="closed-text">老人端的功能一切照旧，只是这里看不到概况。</p>
        <button class="btn btn-primary" type="button" :disabled="saving" @click="toggleConsent(true)">
          重新打开
        </button>
      </section>

      <template v-if="!loading && !error && summary.enabled">
        <!-- 今日概况：每天生成一次，不实时（不是监控流） -->
        <section class="card overview-card">
          <h2 class="section-title">今日概况</h2>
          <p class="overview-text">{{ todayText }}</p>
          <p class="note-line">每天更新一次 · 不是实时监控</p>
        </section>

        <!-- 健康异常摘要（行为层面，不做医疗判断） -->
        <section class="card health-card" :class="{ warn: health.level === 'warn' }">
          <h2 class="section-title">
            {{ health.level === 'warn' ? '⚠️ 需要留意' : '✅ 健康异常摘要' }}
          </h2>
          <ul class="item-list">
            <li v-for="(it, i) in health.items" :key="i" :class="['item', it.level]">
              {{ it.text }}
            </li>
          </ul>
          <p class="note-line">只看行为：吃药、心情、有没有摔倒，不做病情判断。</p>
        </section>

        <!-- 情绪趋势：老人版 / 子女版两种视图 -->
        <section class="card emotion-card">
          <div class="card-head">
            <h2 class="section-title">情绪趋势</h2>
            <div class="view-tabs">
              <button
                type="button"
                class="tab"
                :class="{ on: view === 'child' }"
                @click="view = 'child'"
              >
                子女版
              </button>
              <button
                type="button"
                class="tab"
                :class="{ on: view === 'elder' }"
                @click="view = 'elder'"
              >
                老人版
              </button>
            </div>
          </div>

          <p v-if="!emotion.has_data" class="hint">
            还没有心情记录。等老人和小忆聊上几天，这里就有曲线了。
          </p>

          <template v-else>
            <!-- 子女版：按天聚合，没有原话，只有分数和情绪 -->
            <ul v-if="view === 'child'" class="trend-list">
              <li v-for="p in emotion.points" :key="p.date" class="trend-row">
                <span class="trend-date">{{ p.date }}</span>
                <span class="trend-face" aria-hidden="true">{{ face(p) }}</span>
                <span class="trend-label">{{ p.label_cn }}</span>
                <span class="trend-bar-wrap">
                  <span class="trend-bar" :style="{ width: barWidth(p) }"></span>
                </span>
                <span class="trend-des">{{ scoreText(p) }}</span>
                <span class="trend-count">聊了{{ p.count }}次</span>
              </li>
            </ul>

            <!-- 老人版：直接复用现有心情曲线图（/api/emotion-chart） -->
            <div v-else class="elder-chart">
              <img
                v-if="emotion.elder_chart_url"
                :src="emotion.elder_chart_url"
                alt="老人心情曲线"
                class="chart-img"
              />
              <p v-else class="hint">曲线图暂时生成不了，切到子女版看。</p>
            </div>
          </template>

          <p class="note-line">
            今天情绪：{{ emotion.today_label_cn || '还没有记录' }} · 只有心情概况，没有聊天内容。
          </p>
        </section>

        <!-- 购药待办：展示 + 处理回执 + 通知老人（三件套） -->
        <section class="card purchase-card">
          <h2 class="section-title">购药待办</h2>
          <p v-if="!purchaseList.length" class="hint">现在没有用药的待办。</p>
          <ul v-else class="item-list">
            <li v-for="p in purchaseList" :key="p.id" class="purchase-item">
              <div class="purchase-main">
                <p class="purchase-text">💊 {{ p.text }}</p>
                <p class="purchase-when">{{ p.when }}</p>
              </div>
              <div class="purchase-action">
                <span v-if="p.receipt" class="receipt-done">
                  ✅ 已处理 · 已告诉老人
                </span>
                <button
                  v-else
                  class="btn btn-success receipt-btn"
                  type="button"
                  :disabled="receiptingId === p.id"
                  @click="handlePurchase(p)"
                >
                  {{ receiptingId === p.id ? '正在记…' : '我来处理' }}
                </button>
              </div>
            </li>
          </ul>
          <p v-if="receiptMsg" class="receipt-msg" :class="{ ok: receiptOk }">{{ receiptMsg }}</p>
          <p class="note-line">
            您只买药、只建议；吃不吃、什么时候吃，老人自己拿主意。
          </p>
        </section>

        <!-- F-8 通道③：老人说的方言（选填）。只进统计，不决定小忆现在能不能听懂 -->
        <section class="card dialect-card">
          <h2 class="section-title">老人说的方言（选填）</h2>
          <p class="hint">填一下，小忆就知道该先学哪种方言。不填也不影响用。</p>
          <div class="dialect-form">
            <select v-model="dialectForm.dialect" class="dialect-select" aria-label="老人使用的方言">
              <option value="">还没填</option>
              <option v-for="o in dialectForm.options" :key="o.name" :value="o.name">
                {{ o.name }}
              </option>
            </select>
            <input
              v-if="dialectForm.dialect === '其他方言'"
              v-model.trim="dialectForm.other"
              class="dialect-input"
              placeholder="写一下是哪种，比如闽南语"
            />
            <button
              class="btn btn-secondary dialect-save"
              type="button"
              :disabled="dialectSaving"
              @click="saveDialect"
            >
              {{ dialectSaving ? '正在存…' : '保存' }}
            </button>
          </div>
          <p v-if="dialectMsg" class="receipt-msg" :class="{ ok: dialectOk }">{{ dialectMsg }}</p>
          <p class="note-line">
            只用于统计「该学哪些方言」；小忆还没学会时会对老人直说，不假装听懂。
          </p>
        </section>

        <!-- 隐私边界 + 安心开关 -->
        <section class="card privacy-card">
          <h2 class="section-title">这里能看到什么</h2>
          <div class="privacy-grid">
            <div class="privacy-col">
              <p class="privacy-label ok-label">✅ 能看到</p>
              <ul class="item-list">
                <li v-for="(t, i) in summary.privacy.can_see" :key="'c' + i" class="item">
                  {{ t }}
                </li>
              </ul>
            </div>
            <div class="privacy-col">
              <p class="privacy-label no-label">🚫 看不到</p>
              <ul class="item-list">
                <li v-for="(t, i) in summary.privacy.cannot_see" :key="'n' + i" class="item">
                  {{ t }}
                </li>
              </ul>
            </div>
          </div>
          <p class="privacy-text">{{ summary.privacy.text }}</p>

          <button
            class="btn btn-danger close-btn"
            type="button"
            :disabled="saving"
            @click="toggleConsent(false)"
          >
            {{ saving ? '正在关…' : '关掉「子女远程安心」' }}
          </button>
          <p class="note-line">关掉只影响这个页面，老人端该提醒还提醒、该聊天还聊天。</p>
        </section>

        <!-- 首次告知：老人端会用大字 + 语音说同一句，这里也放一份，方便子女念给老人听 -->
        <section v-if="!summary.consent.notified" class="card notice-card">
          <h2 class="section-title">第一次用，请跟老人说清楚</h2>
          <p class="notice-text">{{ summary.consent.notice_text }}</p>
          <button class="btn btn-secondary" type="button" @click="speakNotice">
            🔊 放给老人听
          </button>
          <p class="note-line">老人端首次进入聊天也会大字 + 语音告知，点「我知道了」或「不让看」都行。</p>
        </section>
      </template>
    </div>

    <audio ref="audioEl" class="hidden-audio" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const loading = ref(true)
const error = ref('')
const saving = ref(false)
const receiptingId = ref('')
const receiptMsg = ref('')
const receiptOk = ref(false)
const view = ref('child')
const audioEl = ref(null)

// 只保留页面要用的字段：后端也只回这些（聊天内容在这里根本不存在）
const summary = reactive({
  enabled: false,
  consent: { notified: true, notice_text: '' },
  privacy: { can_see: [], cannot_see: [], text: '' },
  emotion: { points: [], has_data: false, today_label_cn: null, elder_chart_url: null },
  health: { level: 'ok', items: [] },
  purchase: [],
})
const today = reactive({ text: '' })

// F-8 通道③：子女端首次激活表单里的「老人使用的方言」（选填）
const dialectForm = reactive({ dialect: '', other: '', options: [] })
const dialectSaving = ref(false)
const dialectMsg = ref('')
const dialectOk = ref(false)

const todayText = computed(() => today.text || '今天还没有概况。')
const health = computed(() => summary.health || { level: 'ok', items: [] })
const emotion = computed(() => summary.emotion || { points: [], has_data: false })
const purchaseList = computed(() => summary.purchase || [])

function face(p) {
  if (p.label === 'low') return '😔'
  if (p.label === 'high') return '😊'
  return '🙂'
}
function barWidth(p) {
  const v = Math.max(-1, Math.min(1, Number(p.score) || 0))
  return `${Math.round(((v + 1) / 2) * 100)}%`
}
function scoreText(p) {
  const v = Number(p.score) || 0
  if (v <= -0.3) return '偏低落'
  if (v >= 0.3) return '偏开心'
  return '平稳'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.childSummary()
    const d = res.data || {}
    summary.enabled = !!d.enabled
    if (!d.enabled) {
      summary.closed_hint = d.closed_hint || ''
      return
    }
    summary.consent = d.consent || summary.consent
    summary.privacy = d.privacy || summary.privacy
    summary.health = d.health || { level: 'ok', items: [] }
    summary.emotion = d.emotion || { points: [], has_data: false }
    summary.purchase = d.purchase || []
    today.text = (d.today && d.today.text) || ''
  } catch (e) {
    console.error(e)
    error.value = '看不到数据，可能是后端没启动。'
  } finally {
    loading.value = false
  }
}

async function toggleConsent(enabled) {
  if (saving.value) return
  saving.value = true
  try {
    await api.setChildConsent({ enabled })
    summary.consent = { ...summary.consent, enabled }
    summary.enabled = enabled
    if (enabled) await load()
  } catch (e) {
    console.error(e)
    error.value = '开关没改成功，请稍后再试。'
  } finally {
    saving.value = false
  }
}

async function loadDialect() {
  try {
    const res = await api.dialectProfile()
    const d = res.data || {}
    const p = d.profile || {}
    dialectForm.options = d.options || []
    dialectForm.dialect = p.elder_dialect || ''
    dialectForm.other = p.other || ''
  } catch (e) {
    console.warn('[child] 读取方言字段失败', e)
  }
}

async function saveDialect() {
  if (dialectSaving.value) return
  dialectSaving.value = true
  dialectMsg.value = ''
  try {
    const res = await api.setDialectProfile({
      dialect: dialectForm.dialect,
      other: dialectForm.other,
      by: 'child',
    })
    const d = res.data || {}
    dialectOk.value = !!d.ok
    dialectMsg.value = d.ok
      ? '记下了，小忆会按这个统计该先学哪种方言。'
      : d.message || '没存上，请再试一次。'
  } catch (e) {
    console.error(e)
    dialectOk.value = false
    dialectMsg.value = '保存失败，请稍后再试。'
  } finally {
    dialectSaving.value = false
  }
}

async function handlePurchase(item) {
  if (receiptingId.value) return
  receiptingId.value = item.id
  receiptMsg.value = ''
  try {
    const res = await api.childPurchaseReceipt(item.id)
    const d = res.data || {}
    receiptOk.value = !!d.ok
    receiptMsg.value = d.message || (d.ok ? '记下了。' : '操作失败。')
    if (d.ok) item.receipt = d.notice
    await load()
  } catch (e) {
    console.error(e)
    receiptOk.value = false
    receiptMsg.value = '处理失败，请稍后再试。'
  } finally {
    receiptingId.value = ''
  }
}

async function speakNotice() {
  const text = (summary.consent && summary.consent.notice_text) || ''
  if (!text) return
  try {
    const res = await api.tts({ text })
    const url = res.data && res.data.audio_url
    const el = audioEl.value
    if (el && url) {
      el.src = url
      el.play().catch(() => {})
    }
  } catch (e) {
    // 没配语音服务就把字读给老人听，不挡流程
    console.warn('[child] TTS 不可用', e)
  }
}

onMounted(() => {
  load()
  loadDialect()
})
</script>

<style scoped>
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-width: 820px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.topbar-sub {
  margin-left: auto;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.section-title {
  margin: 0 0 12px;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.hint {
  margin: 0;
  padding: 16px;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.hint.err {
  color: var(--color-danger);
}
.note-line {
  margin: 12px 0 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.overview-card {
  border-left: 6px solid var(--color-accent2);
}
.overview-text {
  margin: 0;
  font-size: 24px;
  line-height: 1.6;
  font-weight: 700;
  color: var(--color-text);
}
.item-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.item {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-soft);
  font-size: var(--font-body);
  color: var(--color-text);
}
.item.warn {
  background: #FBEfE9;
  border-left: 4px solid var(--color-danger);
}
.item.ok {
  border-left: 4px solid var(--color-accent2);
}
.health-card.warn {
  border-color: var(--color-danger);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 4px;
}
.view-tabs {
  display: flex;
  gap: 8px;
}
.tab {
  min-height: 48px;
  padding: 0 16px;
  font-size: var(--font-body);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 24px;
  cursor: pointer;
}
.tab.on {
  color: var(--color-accent2);
  border-color: var(--color-accent2);
  background: #EEF4F0;
  font-weight: 700;
}
.trend-list {
  margin: 8px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.trend-row {
  display: grid;
  grid-template-columns: 92px 28px 56px 1fr 72px 82px;
  align-items: center;
  gap: 10px;
  font-size: var(--font-small);
  color: var(--color-text);
}
.trend-bar-wrap {
  height: 10px;
  background: var(--color-surface-soft);
  border-radius: 6px;
  overflow: hidden;
}
.trend-bar {
  display: block;
  height: 100%;
  background: var(--color-accent2);
  border-radius: 6px;
}
.trend-des,
.trend-count,
.trend-date {
  color: var(--color-text-sub);
}
.elder-chart {
  margin-top: 8px;
  text-align: center;
}
.chart-img {
  width: 100%;
  max-width: 520px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
}
.purchase-item {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--color-surface-soft);
}
.purchase-main {
  flex: 1;
  min-width: 0;
}
.purchase-text {
  margin: 0;
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text);
  word-break: break-word;
}
.purchase-when {
  margin: 4px 0 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.purchase-action {
  flex-shrink: 0;
}
.receipt-btn {
  min-width: 120px;
}
.receipt-done {
  font-size: var(--font-body);
  color: var(--color-accent2);
  font-weight: 700;
}
.receipt-msg {
  margin: 12px 0 0;
  font-size: var(--font-body);
  color: var(--color-danger);
}
.receipt-msg.ok {
  color: var(--color-accent2);
}
.privacy-grid {
  display: flex;
  gap: 20px;
  flex-wrap: wrap;
}
.privacy-col {
  flex: 1;
  min-width: 220px;
}
.privacy-label {
  margin: 0 0 8px;
  font-size: var(--font-body);
  font-weight: 700;
}
.ok-label {
  color: var(--color-accent2);
}
.no-label {
  color: var(--color-danger);
}
.privacy-text {
  margin: 14px 0 0;
  font-size: var(--font-body);
  line-height: 1.7;
  color: var(--color-text-sub);
}
.close-btn {
  margin-top: 16px;
  width: 100%;
}
.closed-card {
  border-left: 6px solid var(--color-danger);
}
.dialect-card {
  border-left: 6px solid var(--color-accent2);
}
.dialect-form {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.dialect-select,
.dialect-input {
  min-height: var(--btn-height);
  padding: 0 14px;
  font-size: var(--font-body);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.dialect-input {
  flex: 1;
  min-width: 180px;
}
.dialect-save {
  min-width: 112px;
}
.closed-text {
  margin: 0 0 12px;
  font-size: var(--font-body);
  line-height: 1.7;
  color: var(--color-text);
}
.notice-card {
  border-left: 6px solid var(--color-accent);
}
.notice-text {
  margin: 0 0 14px;
  font-size: 24px;
  line-height: 1.6;
  font-weight: 700;
  color: var(--color-text);
}
@media (max-width: 640px) {
  .trend-row {
    grid-template-columns: 78px 24px 48px 1fr 64px;
  }
  .trend-count {
    display: none;
  }
}
</style>
