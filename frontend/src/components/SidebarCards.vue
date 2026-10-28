<template>
  <nav class="sidebar">
    <h2 class="sidebar-title">📚 我的小忆</h2>

    <button class="nav-card" type="button" @click="$router.push('/memory')">
      <span class="nav-icon">🧠</span>
      <span class="nav-text">
        <span class="nav-name">记忆箱</span>
        <span class="nav-sub">重要的事都收在这</span>
      </span>
    </button>

    <button class="nav-card" type="button" @click="$router.push('/history')">
      <span class="nav-icon">💬</span>
      <span class="nav-text">
        <span class="nav-name">历史对话</span>
        <span class="nav-sub">回看以前聊过的天</span>
      </span>
    </button>

    <button class="nav-card" type="button" @click="$router.push('/privacy')">
      <span class="nav-icon">🔒</span>
      <span class="nav-text">
        <span class="nav-name">隐私设置</span>
        <span class="nav-sub">想忘掉就说一声</span>
      </span>
    </button>

    <button class="nav-card" type="button" @click="toggleReminder">
      <span class="nav-icon">⏰</span>
      <span class="nav-text">
        <span class="nav-name">提醒事项</span>
        <span class="nav-sub">{{ reminderSub }}</span>
      </span>
    </button>

    <!-- 提醒事项：点一下在左栏就地展开，展示真实列表（完成/取消） -->
    <div v-if="showReminder" class="reminder-drawer card">
      <p v-if="loading" class="drawer-hint">正在查看提醒…</p>
      <p v-else-if="error" class="drawer-hint err">{{ error }}</p>
      <p v-else-if="!reminders.awaiting.length && !reminders.pending.length" class="drawer-hint">
        暂无提醒，可以说"五分钟后提醒我喝水"
      </p>

      <template v-else>
        <!-- 已到点等确认 -->
        <div
          v-for="r in reminders.awaiting"
          :key="'aw' + r.id"
          class="reminder-row row-awaiting"
        >
          <span class="reminder-txt">🔔 {{ r.desc }}（等您确认）</span>
          <button class="btn btn-success row-btn" type="button" @click="doDone(r.id)">✅ 完成</button>
        </div>
        <!-- 未到点待办 -->
        <div
          v-for="r in reminders.pending"
          :key="'pd' + r.id"
          class="reminder-row row-pending"
        >
          <span class="reminder-txt">⏰ {{ r.desc }}<i v-if="r.eta" class="reminder-eta">{{ r.eta }}</i></span>
          <button class="btn btn-danger row-btn" type="button" @click="doCancel(r.id)">✖ 取消</button>
        </div>
      </template>

      <p v-if="reminders.stats && reminders.stats.total" class="drawer-stats">
        最近30天：提醒{{ reminders.stats.total }}次 · 办妥{{ reminders.stats.done }}次 · 完成率{{ reminders.stats.rate }}%
      </p>
    </div>
  </nav>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api'

const showReminder = ref(false)
const loading = ref(false)
const error = ref('')
const reminders = reactive({ awaiting: [], pending: [], stats: null })

const reminderSub = computed(() => {
  const n = reminders.pending.length + reminders.awaiting.length
  return showReminder.value ? (n ? `还有 ${n} 件 · 点我收起` : '暂无待办 · 点我收起') : (n ? `还有 ${n} 件 · 点我展开` : '到点提醒您')
})

function toggleReminder() {
  showReminder.value = !showReminder.value
  if (showReminder.value) loadReminders()
}

async function loadReminders() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getReminders()
    const d = res.data || {}
    reminders.awaiting = d.awaiting || []
    reminders.pending = d.pending || []
    reminders.stats = d.stats || null
  } catch (e) {
    console.error(e)
    error.value = '提醒列表加载失败，请稍后再试。'
  } finally {
    loading.value = false
  }
}

async function doDone(id) {
  try {
    await api.doneReminder(id)
  } catch (e) {
    console.error(e)
  }
  loadReminders()
}

async function doCancel(id) {
  try {
    await api.cancelReminder(id)
  } catch (e) {
    console.error(e)
  }
  loadReminders()
}
</script>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sidebar-title {
  margin: 0 0 4px;
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-text);
  text-align: center;
}
.nav-card {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  min-height: 76px;
  padding: 12px 16px;
  text-align: left;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: transform 0.15s ease, border-color 0.15s ease;
}
.nav-card:hover {
  transform: translateY(-1px);
  border-color: var(--color-accent);
}
.nav-icon {
  font-size: 32px;
  line-height: 1;
}
.nav-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.nav-name {
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text);
}
.nav-sub {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.reminder-drawer {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.drawer-hint {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
  text-align: center;
}
.drawer-hint.err {
  color: var(--color-danger);
}
.reminder-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: var(--radius-sm);
}
.row-awaiting {
  border-left: 4px solid #4CAF50;
  background: #F1F8E9;
}
.row-pending {
  border-left: 4px solid #FFB74D;
  background: #FFF3E0;
}
.reminder-txt {
  flex: 1;
  min-width: 0;
  font-size: var(--font-small);
  color: var(--color-text);
  word-break: break-word;
}
.reminder-eta {
  margin-left: 6px;
  font-style: normal;
  color: var(--color-text-sub);
}
.row-btn {
  flex-shrink: 0;
  min-width: 76px;
  padding: 0 14px;
  font-size: var(--font-small);
}
.drawer-stats {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
</style>
