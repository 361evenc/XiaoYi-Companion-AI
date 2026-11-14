<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🧠 记忆箱</span>
    </header>

    <div class="content">
      <!-- 竖向流程图：记忆的生命周期 -->
      <section class="card flow-card">
        <h2 class="section-title">记忆是怎么来的</h2>
        <ol class="flow">
          <li class="flow-step">
            <span class="flow-icon">🗓️</span>
            <span class="flow-text">
              <strong>历史对话</strong>
              <span>您和小忆说过的话</span>
            </span>
          </li>
          <li class="flow-line"></li>
          <li class="flow-step">
            <span class="flow-icon">📦</span>
            <span class="flow-text">
              <strong>进入记忆箱</strong>
              <span>重要的事被挑出来收好</span>
            </span>
          </li>
          <li class="flow-line"></li>
          <li class="flow-step">
            <span class="flow-icon">🧠</span>
            <span class="flow-text">
              <strong>选择性进入</strong>
              <span>真正有用的才留在长期记忆里</span>
            </span>
          </li>
        </ol>
      </section>

      <p v-if="error" class="error-tip">{{ error }}</p>
      <p v-else-if="loading" class="error-tip">正在打开记忆箱…</p>

      <!-- F-4：翻旧事 = 对同一份记忆按 人/物/时间/地方 四维检索（并进记忆箱，不再单独一页） -->
      <section class="card search-card">
        <h2 class="section-title">翻旧事</h2>
        <p class="search-sub">按人、按物件、按时间、按地方，都能翻到当年的事。</p>

        <div class="dim-grid">
          <button
            v-for="d in DIMS"
            :key="d.key"
            type="button"
            class="dim-btn"
            :class="{ on: dim === d.key }"
            @click="pickDim(d.key)"
          >
            <span class="dim-icon">{{ d.icon }}</span>
            <span class="dim-name">{{ d.label }}</span>
          </button>
        </div>
        <p class="dim-hint">{{ currentDim.hint }}</p>

        <div v-if="dim === 'time'" class="chip-row">
          <button
            v-for="y in years"
            :key="y"
            type="button"
            class="chip"
            :class="{ on: query === String(y) }"
            @click="quick(String(y))"
          >
            {{ y }}年
          </button>
        </div>
        <div v-else class="chip-row">
          <button v-for="c in currentDim.chips" :key="c" type="button" class="chip" @click="quick(c)">
            {{ c }}
          </button>
        </div>

        <div class="search-row">
          <input v-model.trim="query" class="input" :placeholder="currentDim.placeholder" @keyup.enter="search" />
          <button class="btn btn-primary search-btn" type="button" :disabled="searching" @click="search">
            {{ searching ? '正在翻…' : '翻一翻' }}
          </button>
        </div>

        <p v-if="searchError" class="error-tip">{{ searchError }}</p>

        <section v-if="searched" class="search-result">
          <p v-if="note" class="note-text">{{ note }}</p>
          <div class="result-head">
            <h3 class="result-title">翻到的事</h3>
            <span class="result-count">{{ results.length ? `一共 ${results.length} 件` : '' }}</span>
          </div>
          <p v-if="searching" class="empty-hint">正在翻…</p>
          <p v-else-if="!results.length && !searchError" class="empty-hint">
            这件事还没记下来。换个说法再翻，或者跟小忆聊聊，让它记着。
          </p>
          <ol v-else class="fl-list">
            <li v-for="(it, i) in pageItems" :key="it.id || i" class="fl-item">
              <span class="fl-icon">{{ iconOf(it) }}</span>
              <span class="fl-body">
                <strong class="fl-title">{{ titleOf(it) }}</strong>
                <span v-if="subOf(it)" class="fl-sub">{{ subOf(it) }}</span>
                <span class="fl-date">{{ dateOf(it) }}</span>
              </span>
            </li>
          </ol>
          <div v-if="results.length" class="pager">
            <button class="page-btn" type="button" :disabled="page <= 1" @click="go(-1)">
              ◀ 上一页
            </button>
            <span class="page-info">第 {{ page }} / {{ totalPages }} 页</span>
            <button class="page-btn" type="button" :disabled="page >= totalPages" @click="go(1)">
              下一页 ▶
            </button>
          </div>
        </section>
      </section>

      <!-- 重要事件时间线 -->
      <section class="card">
        <h2 class="section-title">我记得的重要事</h2>
        <p v-if="!events.length && !loading" class="empty-hint">
          还没有。重要的事小忆会打 ⭐ 收在这里。
        </p>
        <ol v-else class="timeline">
          <li v-for="e in events" :key="e.id" class="tl-item">
            <span class="tl-dot">⭐</span>
            <span class="tl-body">
              <strong class="tl-text">{{ e.text }}</strong>
              <span v-if="e.quote" class="tl-quote">「{{ e.quote }}」 {{ e.date }}</span>
            </span>
          </li>
        </ol>
      </section>

      <!-- 照片记忆 -->
      <section v-if="photos.length" class="card">
        <h2 class="section-title">您给我看过的照片</h2>
        <div class="photo-grid">
          <img v-for="(p, i) in photos" :key="i" :src="p" class="photo-thumb" loading="lazy" />
        </div>
      </section>

      <!-- 小忆的理解 -->
      <section v-if="reflections.length" class="card">
        <h2 class="section-title">小忆心里的您</h2>
        <p v-for="(r, i) in reflections" :key="i" class="refl-text">💭 {{ r.text }}</p>
      </section>

      <!-- 心情曲线 -->
      <section class="card">
        <h2 class="section-title">心情曲线</h2>
        <button class="btn btn-secondary" type="button" :disabled="chartLoading" @click="loadChart">
          {{ chartLoading ? '正在画…' : '查看心情曲线' }}
        </button>
        <p v-if="chartEmpty" class="empty-hint">还没有足够的心情记录，多和小忆聊聊就有啦。</p>
        <img v-if="chartUrl" :src="chartUrl" class="emotion-chart" alt="心情曲线" />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'

const loading = ref(false)
const error = ref('')
const events = ref([])
const photos = ref([])
const reflections = ref([])

const chartLoading = ref(false)
const chartUrl = ref('')
const chartEmpty = ref(false)

// F-4 翻旧事：四维检索（并进记忆箱，复用 /api/memory/search）
const PAGE_SIZE = 10
const DIMS = [
  { key: 'person', label: '人', icon: '👤', hint: '想翻谁的事？比如孙子、邻居、家里的小猫。', placeholder: '孙子', chips: ['我', '孙子', '邻居', '小猫'] },
  { key: 'object', label: '物件', icon: '📦', hint: '想翻什么东西？比如降压药、老花镜。', placeholder: '降压药', chips: ['降压药', '老花镜', '遥控器'] },
  { key: 'time', label: '时间', icon: '🗓️', hint: '想翻哪一年？点下面按钮就行。', placeholder: '2024', chips: [] },
  { key: 'scene', label: '地方', icon: '🏠', hint: '想翻哪儿的事？比如厨房、客厅。', placeholder: '厨房', chips: ['厨房', '客厅', '卧室'] },
]
const _now = new Date()
const years = [0, 1, 2, 3, 4].map((i) => _now.getFullYear() - i)

const dim = ref('person')
const query = ref('')
const searched = ref(false)
const searching = ref(false)
const searchError = ref('')
const results = ref([])
const resolved = ref(null)
const page = ref(1)

const currentDim = computed(() => DIMS.find((d) => d.key === dim.value) || DIMS[0])
const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / PAGE_SIZE)))
const pageItems = computed(() => {
  const s = (page.value - 1) * PAGE_SIZE
  return results.value.slice(s, s + PAGE_SIZE)
})
// 人物维度提示：只按 resolved.kind 说，不显示猜的人名
const note = computed(() => {
  if (!searched.value || dim.value !== 'person') return ''
  const r = resolved.value || {}
  if (r.kind === 'profile') return `这是您说过的「${r.name || r.label || ''}」，我记着它。`
  if (r.kind === 'fallback_visitor') return '这个人我还没有记下名字，先给您看看「有客人来过」的事。'
  if (r.kind === 'visitor') return '这些是说客人、邻居来家里的事。'
  if (r.kind === 'pet') return '这些是说家里小动物的事。'
  if (r.kind === 'self') return '这些是说您自己的事。'
  return ''
})

function pickDim(key) {
  if (dim.value === key) return
  dim.value = key
  query.value = ''
  searched.value = false
  results.value = []
  resolved.value = null
  searchError.value = ''
}

function quick(v) {
  query.value = v
  search()
}

async function search() {
  const q = query.value.trim()
  if (!q || searching.value) return
  searching.value = true
  searchError.value = ''
  searched.value = true
  page.value = 1
  try {
    const res = await api.memorySearch({ q, by: dim.value })
    const d = res.data || {}
    if (d.ok === false) {
      results.value = []
      resolved.value = null
      searchError.value = d.message || '没有翻到。换个说法再试试。'
      return
    }
    results.value = d.results || []
    resolved.value = d.resolved || null
  } catch (e) {
    console.error(e)
    results.value = []
    resolved.value = null
    searchError.value = '翻旧事没打开，请稍后再试。'
  } finally {
    searching.value = false
  }
}

function go(step) {
  const next = page.value + step
  if (next < 1 || next > totalPages.value) return
  page.value = next
}

function iconOf(it) {
  if (it.type === 'fact') return '📌'
  if (it.type === 'reflection') return '💭'
  return '💬'
}
function titleOf(it) {
  if (it.type === 'fact') return `${it.key || '这事'}：${it.value || ''}`
  return it.text || ''
}
function subOf(it) {
  return it.quote ? `「${it.quote}」` : ''
}
function dateOf(it) {
  const ts = Number(it.ts || 0)
  if (!ts) return '时间不详'
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getMemory()
    const d = res.data || {}
    events.value = (d.events || []).map((e) => ({
      ...e,
      date: e.ts ? formatDate(e.ts) : '',
    }))
    photos.value = d.photos || []
    reflections.value = d.reflections || []
  } catch (e) {
    console.error(e)
    error.value = '记忆箱打开失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}

async function loadChart() {
  if (chartLoading.value) return
  chartLoading.value = true
  chartEmpty.value = false
  try {
    const res = await api.getEmotionChart()
    const url = res.data && res.data.image_url
    if (url) {
      chartUrl.value = url
    } else {
      chartEmpty.value = true
    }
  } catch (e) {
    console.error(e)
    chartEmpty.value = true
  } finally {
    chartLoading.value = false
  }
}

function formatDate(ts) {
  const d = new Date(ts * 1000)
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${m}-${day}`
}

load()
</script>

<style scoped>
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 720px;
  margin: 0 auto;
  width: 100%;
}
.section-title {
  margin: 0 0 14px;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.entry-line {
  border-left: 6px solid var(--color-accent2);
}
.entry-line .section-title {
  margin-bottom: 8px;
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
.flow {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.flow-step {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.flow-icon {
  font-size: 28px;
}
.flow-text {
  display: flex;
  flex-direction: column;
}
.flow-text strong {
  font-size: var(--font-body);
  color: var(--color-text);
}
.flow-text span {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.flow-line {
  width: 2px;
  height: 16px;
  margin-left: 26px;
  background: var(--color-accent);
}
.empty-hint {
  margin: 0;
  color: var(--color-text-sub);
  font-size: var(--font-small);
}
.error-tip {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-small);
}
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tl-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding-left: 4px;
  border-left: 2px solid var(--color-accent);
}
.tl-dot {
  font-size: 18px;
  line-height: 1.4;
}
.tl-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tl-text {
  font-size: var(--font-body);
  color: var(--color-text);
}
.tl-quote {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.photo-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.photo-thumb {
  width: 96px;
  height: 96px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}
.refl-text {
  margin: 0 0 8px;
  font-size: var(--font-body);
  color: var(--color-text);
}
.emotion-chart {
  margin-top: 14px;
  width: 100%;
  border-radius: var(--radius-sm);
}

/* F-4 翻旧事：四维检索（并进记忆箱） */
.search-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.search-sub {
  margin: 0 0 14px;
  font-size: var(--font-body);
  line-height: 1.7;
  color: var(--color-text-sub);
}
.dim-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.dim-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 72px;
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
}
.dim-btn.on {
  border-color: var(--color-accent);
  background: var(--color-surface-soft);
  color: var(--color-accent-deep);
}
.dim-icon {
  font-size: 28px;
  line-height: 1;
}
.dim-hint {
  margin: 0;
  font-size: var(--font-bubble);
  color: var(--color-text-sub);
  line-height: 1.6;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
.chip {
  min-height: 64px;
  padding: 0 22px;
  font-size: 20px;
  color: var(--color-accent2);
  background: var(--color-surface);
  border: 2px solid var(--color-accent2);
  border-radius: 32px;
  cursor: pointer;
}
.chip.on {
  color: #FFFFFF;
  background: var(--color-accent2);
}
.search-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.search-row .input {
  flex: 1;
  min-width: 180px;
  min-height: 64px;
  font-size: 22px;
}
.search-btn {
  min-width: 140px;
  min-height: 64px;
  font-size: var(--font-bubble);
}
.search-result {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--color-border);
}
.note-text {
  margin: 0;
  font-size: 20px;
  line-height: 1.7;
  color: var(--color-text);
}
.result-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.result-title {
  margin: 0;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.result-count {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.fl-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.fl-item {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 14px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.fl-icon {
  font-size: 24px;
  line-height: 1.3;
}
.fl-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.fl-title {
  font-size: 22px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-word;
}
.fl-sub {
  font-size: var(--font-body);
  color: var(--color-text-sub);
  word-break: break-word;
}
.fl-date {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.page-btn {
  min-height: 64px;
  min-width: 128px;
  padding: 0 18px;
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
  background: var(--color-surface);
  border: 2px solid var(--color-accent);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.page-info {
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
</style>
