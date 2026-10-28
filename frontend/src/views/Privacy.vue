<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🔒 隐私设置</span>
    </header>

    <div class="content">
      <section class="card">
        <h2 class="section-title">隐私说明（大白话）</h2>
        <div class="privacy-text">
          <p>小忆会把和您聊的重要事情记在心里，这样下次才能接着陪您聊。</p>
          <p>这些记忆都保存在您自己的设备上，不会上传到别处。</p>
          <p>您随时可以说「忘掉这件事」，小忆就会立刻删掉对应的记忆。</p>
          <p>想全部清空也可以，点下面的按钮，一键删掉所有记忆和聊天记录。</p>
        </div>
      </section>

      <section class="card">
        <h2 class="section-title">忘掉一件事</h2>
        <div class="row">
          <input v-model.trim="forgetText" class="input" placeholder="例如：忘掉我腿疼的事" />
          <button class="btn btn-secondary" type="button" :disabled="forgetting" @click="doForget">
            {{ forgetting ? '正在忘…' : '忘掉这件事' }}
          </button>
        </div>
        <p v-if="result" class="result-tip" :class="{ ok: resultOk }">{{ result }}</p>
      </section>

      <section class="card danger-zone">
        <h2 class="section-title">清空全部记忆</h2>
        <p class="warn-text">这会删掉所有记忆、聊天记录和提醒，删了找不回来。</p>
        <button class="btn btn-danger" type="button" :disabled="wiping" @click="confirmWipe">
          {{ wiping ? '正在清空…' : '🗑️ 清空全部记忆' }}
        </button>
        <p v-if="wipeMsg" class="result-tip">{{ wipeMsg }}</p>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { api } from '../api'

const router = useRouter()
const session = useSessionStore()

const forgetText = ref('')
const forgetting = ref(false)
const result = ref('')
const resultOk = ref(false)
const wiping = ref(false)
const wipeMsg = ref('')

async function doForget() {
  const text = forgetText.value.trim()
  if (!text || forgetting.value) return
  forgetting.value = true
  result.value = ''
  try {
    const res = await api.forget({ text })
    resultOk.value = !!res.data.ok
    result.value = res.data.message || (res.data.ok ? '忘掉啦。' : '没找到这件事。')
    if (res.data.ok) forgetText.value = ''
  } catch (e) {
    console.error(e)
    resultOk.value = false
    result.value = '操作失败，请稍后再试。'
  } finally {
    forgetting.value = false
  }
}

async function confirmWipe() {
  if (wiping.value) return
  // 防误触：二次确认
  if (!window.confirm('确定要清空所有记忆吗？这个操作无法撤销。')) return
  wiping.value = true
  wipeMsg.value = ''
  try {
    const res = await api.wipeMemory({ is_muted: session.isMuted })
    if (!res.data.ok) {
      wipeMsg.value = res.data.message || '清空失败，请稍后再试。'
      return
    }
    // 清空成功：本地会话作废，回欢迎页重新认识
    session.setConversation(null, [])
    session.setConvId(null)
    session.initialized = false
    session.save()
    alert(res.data.message || '全都删干净啦。')
    router.push('/')
  } catch (e) {
    console.error(e)
    wipeMsg.value = '清空失败，请稍后再试。'
  } finally {
    wiping.value = false
  }
}
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
.section-title {
  margin: 0 0 14px;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.privacy-text p {
  margin: 0 0 10px;
  font-size: var(--font-body);
  color: var(--color-text);
}
.row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.row .input {
  flex: 1;
  min-width: 200px;
}
.danger-zone {
  border-color: var(--color-danger);
}
.warn-text {
  color: var(--color-danger);
  font-size: var(--font-body);
  margin: 0 0 14px;
}
.result-tip {
  margin: 12px 0 0;
  font-size: var(--font-body);
  color: var(--color-danger);
}
.result-tip.ok {
  color: var(--color-accent2);
}
</style>