<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import api from '../api/index.js'

// T-5 救命链路前端：大屏 120 + 状态轮询 + 触发按钮
const status = ref({})
const countdownActive = ref(false)
const is120Path = ref(false)
let pollTimer = null

async function refreshStatus() {
  try {
    const res = await api.get('/emergency/rescue/status')
    status.value = res.data || res
    const st = status.value.state || 'idle'
    countdownActive.value = (st === 'countdown' || st === 'calling')
    is120Path.value = (status.value.path === '120_first')
  } catch (e) {
    // 轮询失败不阻塞 UI
  }
}

async function triggerRescue(word) {
  try {
    await api.post('/emergency/rescue/trigger', { word })
    await refreshStatus()
  } catch (e) {
    // 触发失败不阻塞
  }
}

async function cancelCountdown() {
  try {
    await api.post('/emergency/rescue/countdown', { reply: '别打了' })
    await refreshStatus()
  } catch (e) {
    // 取消失败不阻塞
  }
}

onMounted(() => {
  refreshStatus()
  pollTimer = setInterval(refreshStatus, 2000)
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})
</script>

<template>
  <div class="emergency-page">
    <!-- 大屏 120（120 优先路径时显示） -->
    <div v-if="is120Path && countdownActive" class="big-120">
      <div class="big-120-number">120</div>
      <div class="big-120-hint">着急的事您自己按 120 最稳妥</div>
    </div>

    <!-- 倒计时可取消 -->
    <div v-if="countdownActive" class="countdown-box">
      <div class="countdown-text">正在帮您联系家人…</div>
      <button class="big-btn cancel-btn" @click="cancelCountdown">别打了（取消）</button>
    </div>

    <!-- 状态总览 -->
    <div class="status-box">
      <div class="status-row">
        <span class="status-label">状态</span>
        <span class="status-value">{{ status.state || 'idle' }}</span>
      </div>
      <div class="status-row">
        <span class="status-label">基线期</span>
        <span class="status-value">{{ status.is_baseline ? '是（前2周宽松）' : '否' }}</span>
      </div>
      <div class="status-row">
        <span class="status-label">无活动</span>
        <span class="status-value">{{ Math.round((status.idle_secs || 0) / 3600) }} 小时</span>
      </div>
      <div class="status-row">
        <span class="status-label">路径</span>
        <span class="status-value">{{ status.path || '未触发' }}</span>
      </div>
    </div>

    <!-- 触发按钮（模拟环境用） -->
    <div class="trigger-box">
      <button class="big-btn trigger-btn" @click="triggerRescue('救命')">喊"救命"</button>
      <button class="big-btn trigger-btn" @click="triggerRescue('打120')">喊"打120"</button>
    </div>
  </div>
</template>

<style scoped>
.emergency-page {
  min-height: 100vh;
  background: #fff;
  padding: 40px;
  font-family: "Microsoft YaHei", sans-serif;
}

.big-120 {
  text-align: center;
  padding: 40px;
  background: #fff;
  border: 4px solid #e53935;
  border-radius: 16px;
  margin-bottom: 24px;
}

.big-120-number {
  font-size: 160px;
  font-weight: bold;
  color: #e53935;
  line-height: 1;
}

.big-120-hint {
  font-size: 32px;
  color: #333;
  margin-top: 16px;
}

.countdown-box {
  text-align: center;
  padding: 24px;
  background: #fff3e0;
  border-radius: 12px;
  margin-bottom: 24px;
}

.countdown-text {
  font-size: 28px;
  color: #e65100;
  margin-bottom: 16px;
}

.big-btn {
  min-height: 72px;
  min-width: 200px;
  font-size: 28px;
  font-weight: bold;
  border: none;
  border-radius: 12px;
  cursor: pointer;
  padding: 0 32px;
}

.cancel-btn {
  background: #e53935;
  color: #fff;
}

.trigger-btn {
  background: #1565c0;
  color: #fff;
  margin: 8px;
}

.status-box {
  background: #f5f5f5;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
}

.status-row {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #ddd;
  font-size: 24px;
}

.status-row:last-child {
  border-bottom: none;
}

.status-label {
  color: #666;
}

.status-value {
  color: #333;
  font-weight: bold;
}

.trigger-box {
  text-align: center;
  padding: 24px;
}
</style>
