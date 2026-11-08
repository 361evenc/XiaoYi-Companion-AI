<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🗂️ 家庭时间线</span>
    </header>

    <div class="content">
      <!-- 四维入口：人 / 物件 / 时间 / 地方，四个大按钮，一眼看懂 -->
      <section class="card dim-card">
        <h2 class="section-title">想按什么翻？</h2>
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

        <!-- 时间维度：年份按钮，老人不用打字 -->
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

        <!-- 人 / 物件 / 地方：给几个常见说法，也可以自己输入 -->
        <div v-else class="chip-row">
          <button
            v-for="c in currentDim.chips"
            :key="c"
            type="button"
            class="chip"
            @click="quick(c)"
          >
            {{ c }}
          </button>
        </div>

        <div class="search-row">
          <input
            v-model.trim="query"
            class="input"
            :placeholder="currentDim.placeholder"
            @keyup.enter="search"
          />
          <button class="btn btn-primary search-btn" type="button" :disabled="loading" @click="search">
            {{ loading ? '正在翻…' : '翻一翻' }}
          </button>
        </div>
      </section>

      <!-- 人物维度：档案优先，没档案只显"有客人来过"，不猜人名 -->
      <section v-if="note" class="card note-card">
        <p class="note-text">{{ note }}</p>
      </section>

      <p v-if="error" class="error-tip">{{ error }}</p>

      <!-- 结果：一页最多 10 条，靠翻页看后面（不滚长列表） -->
      <section v-if="searched" class="card result-card">
        <div class="result-head">
          <h2 class="section-title">翻到的事</h2>
          <span class="result-count">{{ results.length ? `一共 ${results.length} 件` : '' }}</span>
        </div>

        <p v-if="loading" class="empty-hint">正在翻…</p>
        <p v-else-if="!results.length" class="empty-hint">
          这件事还没记下来。可以换个说法再翻，或者跟小忆聊聊，让它记着。
        </p>
        <ol v-else class="tl-list">
          <li v-for="(it, i) in pageItems" :key="it.id || i" class="tl-item">
            <span class="tl-icon">{{ iconOf(it) }}</span>
            <span class="tl-body">
              <strong class="tl-title">{{ titleOf(it) }}</strong>
              <span v-if="subOf(it)" class="tl-sub">{{ subOf(it) }}</span>
              <span class="tl-date">{{ dateOf(it) }}</span>
            </span>
          </li>
        </ol>

        <!-- 翻页：按钮 64px，老人独立可用 -->
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
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'

const PAGE_SIZE = 10

const DIMS = [
  {
    key: 'person',
    label: '人',
    icon: '👤',
    hint: '想翻谁的事？比如孙子、邻居、家里的小猫。',
    placeholder: '孙子',
    chips: ['我', '孙子', '邻居', '小猫'],
  },
  {
    key: 'object',
    label: '物件',
    icon: '📦',
    hint: '想翻什么东西？比如降压药、老花镜。',
    placeholder: '降压药',
    chips: ['降压药', '老花镜', '遥控器'],
  },
  {
    key: 'time',
    label: '时间',
    icon: '🗓️',
    hint: '想翻哪一年？点下面按钮就行。',
    placeholder: '2024',
    chips: [],
  },
  {
    key: 'scene',
    label: '地方',
    icon: '🏠',
    hint: '想翻哪儿的事？比如厨房、客厅。',
    placeholder: '厨房',
    chips: ['厨房', '客厅', '卧室'],
  },
]

const now = new Date()
const years = [0, 1, 2, 3, 4].map((i) => now.getFullYear() - i)

const dim = ref('person')
const query = ref('')
const loading = ref(false)
const searched = ref(false)
const error = ref('')
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
  if (r.kind === 'fallback_visitor') {
    return '这个人我还没有记下名字，先给您看看「有客人来过」的事。'
  }
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
  error.value = ''
}

function quick(v) {
  query.value = v
  search()
}

async function search() {
  const q = query.value.trim()
  if (!q || loading.value) return
  loading.value = true
  error.value = ''
  searched.value = true
  page.value = 1
  try {
    const res = await api.memorySearch({ q, by: dim.value })
    const d = res.data || {}
    if (d.ok === false) {
      results.value = []
      resolved.value = null
      error.value = d.message || '没有翻到。换个说法再试试。'
      return
    }
    results.value = d.results || []
    resolved.value = d.resolved || null
  } catch (e) {
    console.error(e)
    results.value = []
    resolved.value = null
    error.value = '时间线没打开，请稍后再试。'
  } finally {
    loading.value = false
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
  if (it.type === 'fact') {
    return `${it.key || '这事'}：${it.value || ''}`
  }
  return it.text || ''
}

function subOf(it) {
  if (!it.quote) return ''
  // 原话是老人自己说的，照原样显示，不改写
  return `「${it.quote}」`
}

function dateOf(it) {
  const ts = Number(it.ts || 0)
  if (!ts) return '时间不详'
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}
</script>

<style scoped>
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
.section-title {
  margin: 0;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.dim-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
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
.note-card {
  border-left: 6px solid var(--color-accent2);
  background: var(--color-surface-soft);
}
.note-text {
  margin: 0;
  font-size: 20px;
  line-height: 1.7;
  color: var(--color-text);
}
.error-tip {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-danger);
}
.result-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.result-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.result-count {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.empty-hint {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
  line-height: 1.7;
}
.tl-list {
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
  padding: 12px 14px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.tl-icon {
  font-size: 24px;
  line-height: 1.3;
}
.tl-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.tl-title {
  font-size: 22px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-word;
}
.tl-sub {
  font-size: var(--font-body);
  color: var(--color-text-sub);
  word-break: break-word;
}
.tl-date {
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
