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
import { ref } from 'vue'
import { api } from '../api'

const loading = ref(false)
const error = ref('')
const events = ref([])
const photos = ref([])
const reflections = ref([])

const chartLoading = ref(false)
const chartUrl = ref('')
const chartEmpty = ref(false)

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
</style>
