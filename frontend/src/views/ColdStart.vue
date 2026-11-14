<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../api/index.js'

// T-3 冷启动编排前端：Day1-7 脚本 + 任务完成/跳过（大字适老，一天学一件事）
const status = ref({})
let pollTimer = null

async function refresh() {
  try {
    const res = await api.get('/cold_start')
    status.value = res.data || res
  } catch (e) {
    // 轮询失败不阻塞 UI
  }
}

async function done(taskId) {
  try { await api.post('/cold_start/done', { task_id: taskId }); await refresh() } catch (e) {}
}

async function skip(taskId) {
  try { await api.post('/cold_start/skip', { task_id: taskId }); await refresh() } catch (e) {}
}

onMounted(() => { refresh(); pollTimer = setInterval(refresh, 3000) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<template>
  <div class="cs-page" v-if="status.in_cold_start">
    <h1 class="cs-day">第 {{ status.current_day }} 天</h1>
    <h2 class="cs-theme">{{ status.day_theme }}</h2>
    <div v-if="status.next_task" class="cs-task">
      <div class="cs-task-text">{{ status.next_task.text }}</div>
      <div class="cs-btns">
        <button class="big-btn done-btn" @click="done(status.next_task.id)">我弄好了</button>
        <button v-if="!status.next_task.essential" class="big-btn skip-btn"
                @click="skip(status.next_task.id)">这个先不弄</button>
      </div>
    </div>
    <div v-else class="cs-done">今天的事弄完啦，明天见。</div>
  </div>
  <div v-else class="cs-page">
    <h1 class="cs-theme">日常陪伴</h1>
    <div class="cs-done">这一周过去了，以后我陪着您，有事您随时喊我。</div>
  </div>
</template>

<style scoped>
.cs-page { min-height: 100vh; background: #fff; padding: 40px; font-family: "Microsoft YaHei", sans-serif; }
.cs-day { font-size: 34px; color: #1565c0; margin-bottom: 12px; }
.cs-theme { font-size: 30px; color: #333; margin-bottom: 24px; }
.cs-task { background: #f5f5f5; border-radius: 12px; padding: 24px; }
.cs-task-text { font-size: 28px; color: #333; margin-bottom: 24px; }
.cs-btns { display: flex; gap: 16px; }
.big-btn { min-height: 72px; min-width: 200px; font-size: 28px; font-weight: bold; border: none; border-radius: 12px; cursor: pointer; padding: 0 32px; }
.done-btn { background: #2e7d32; color: #fff; }
.skip-btn { background: #9e9e9e; color: #fff; }
.cs-done { font-size: 28px; color: #666; text-align: center; padding: 40px; }
</style>
