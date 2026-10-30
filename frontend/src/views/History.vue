<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">💬 历史对话</span>
    </header>

    <div class="content">
      <div class="toolbar">
        <input v-model="search" class="input search" placeholder="搜索以前的对话" />
        <button class="btn btn-secondary" type="button" @click="newConversation">➕ 新对话</button>
      </div>

      <p v-if="error" class="error-tip">{{ error }}</p>

      <div v-if="loading" class="empty-hint">正在加载…</div>
      <ul v-else class="conv-list">
        <li
          v-for="c in conversations"
          :key="c.id"
          class="card conv-item"
          @click="openConversation(c)"
        >
          <div class="conv-main">
            <span class="conv-title">{{ c.title }}</span>
            <span class="conv-time">{{ c.time_str }}</span>
          </div>
          <button class="btn btn-danger conv-del" type="button" @click.stop="removeConversation(c)">
            🗑️ 删除
          </button>
        </li>
      </ul>
      <p v-if="!loading && conversations.length === 0" class="empty-hint">还没有对话，去和小忆聊聊吧。</p>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const session = useSessionStore()

const search = ref('')
const conversations = ref([])
const loading = ref(false)
const error = ref('')

async function loadList(kw) {
  loading.value = true
  error.value = ''
  try {
    const res = await api.getConversations(kw ? { search: kw } : undefined)
    conversations.value = res.data.conversations || []
  } catch (e) {
    error.value = '加载历史失败，请确认后端已启动。'
    console.error(e)
  } finally {
    loading.value = false
  }
}

// 搜索：输入防抖 300ms 再请求
let timer = null
watch(search, (v) => {
  clearTimeout(timer)
  timer = setTimeout(() => loadList(v.trim() || undefined), 300)
})

async function openConversation(c) {
  try {
    const res = await api.getConversation(c.id)
    session.setConversation(c.id, res.data.messages || [])
    router.push('/chat')
  } catch (e) {
    console.error(e)
  }
}

async function newConversation() {
  try {
    const res = await api.createConversation()
    session.setConversation(res.data.conv_id, res.data.chat_history || [])
    router.push('/chat')
  } catch (e) {
    console.error(e)
  }
}

async function removeConversation(c) {
  if (!window.confirm('确定删除这段对话吗？')) return
  try {
    await api.deleteConversation(c.id)
    loadList(search.value.trim() || undefined)
  } catch (e) {
    console.error(e)
  }
}

onMounted(() => loadList())
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
.toolbar {
  display: flex;
  gap: 10px;
}
.search {
  flex: 1;
}
.conv-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}
.conv-item:hover {
  border-color: var(--color-accent);
}
.conv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.conv-title {
  font-size: var(--font-body);
  font-weight: 600;
  color: var(--color-text);
}
.conv-time {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.conv-del {
  flex-shrink: 0;
  min-height: 48px;
  padding: 0 14px;
}
.empty-hint {
  color: var(--color-text-sub);
}
.error-tip {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-small);
}
</style>