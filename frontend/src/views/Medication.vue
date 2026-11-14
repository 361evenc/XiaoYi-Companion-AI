<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../api/index.js'

// T-4 用药闭环前端：余量自报 + 购药待办 + 状态（大字版，适老）
const status = ref({})
const lowMeds = ref([])
let pollTimer = null

async function refresh() {
  try {
    const res = await api.get('/medication')
    status.value = res.data || res
    lowMeds.value = status.value.low_meds || []
  } catch (e) {
    // 轮询失败不阻塞 UI
  }
}

onMounted(() => {
  refresh()
  pollTimer = setInterval(refresh, 3000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="med-page">
    <h1 class="med-title">我的药</h1>

    <!-- 余量低的药（购药提醒） -->
    <div v-if="lowMeds.length" class="low-box">
      <div class="low-text">这些药快用完了，您该去买药啦：</div>
      <div v-for="m in lowMeds" :key="m" class="low-item">{{ m }}</div>
    </div>

    <!-- 药品列表 -->
    <div v-if="status.meds && Object.keys(status.meds).length" class="med-list">
      <div v-for="(m, name) in status.meds" :key="name" class="med-item">
        <div class="med-name">{{ name }}</div>
        <div class="med-detail">
          还剩 {{ m.remaining }}{{ m.unit }}（够 {{ m.days_left }} 天）
          <span v-if="m.low" class="med-low">快用完了</span>
        </div>
      </div>
    </div>

    <div v-else class="med-empty">
      您还没记下药呢。跟我说"降压药还剩5片"就行。
    </div>
  </div>
</template>

<style scoped>
.med-page { min-height: 100vh; background: #fff; padding: 40px; font-family: "Microsoft YaHei", sans-serif; }
.med-title { font-size: 34px; color: #333; margin-bottom: 24px; }
.low-box { background: #fff3e0; border-radius: 12px; padding: 24px; margin-bottom: 24px; }
.low-text { font-size: 28px; color: #e65100; margin-bottom: 12px; }
.low-item { font-size: 28px; color: #333; padding: 8px 0; }
.med-list { background: #f5f5f5; border-radius: 12px; padding: 24px; }
.med-item { padding: 16px 0; border-bottom: 1px solid #ddd; }
.med-item:last-child { border-bottom: none; }
.med-name { font-size: 28px; color: #333; font-weight: bold; }
.med-detail { font-size: 24px; color: #666; margin-top: 8px; }
.med-low { color: #e53935; font-weight: bold; margin-left: 8px; }
.med-empty { font-size: 28px; color: #666; text-align: center; padding: 40px; }
</style>
