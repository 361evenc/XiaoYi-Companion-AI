<template>
  <div class="page welcome-page">
    <main class="card welcome-card">
      <h1 class="brand">小忆</h1>
      <p class="slogan">暖心陪伴，就在您身边</p>

      <form class="form" @submit.prevent="start">
        <label class="field">
          <span class="field-label">您的姓氏</span>
          <input
            v-model.trim="form.surname"
            class="input"
            placeholder="例如：张、李、王"
            required
          />
        </label>

        <fieldset class="field">
          <legend class="field-label">怎么称呼您</legend>
          <div class="radio-row">
            <label v-for="g in ['女', '男']" :key="g" class="radio-card">
              <input type="radio" :value="g" v-model="form.gender" />
              <span>{{ g }}</span>
            </label>
          </div>
        </fieldset>

        <fieldset class="field">
          <legend class="field-label">选一个小忆的性格</legend>
          <div class="radio-col">
            <label v-for="p in personalities" :key="p" class="radio-card">
              <input type="radio" :value="p" v-model="form.personality" />
              <span>{{ p }}</span>
              <button type="button" class="try-btn" @click.prevent="tryVoice(p)">试听</button>
            </label>
          </div>
        </fieldset>

        <button type="submit" class="btn btn-primary submit-btn" :disabled="loading">
          {{ loading ? '正在进入…' : '✅ 进入聊天' }}
        </button>

        <p v-if="error" class="error-tip">{{ error }}</p>
      </form>
    </main>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSessionStore } from '../stores/session'
import { api } from '../api'
import { PERSONA_VOICE } from '../utils/audio'

const router = useRouter()
const session = useSessionStore()

const personalities = ['踏实务实', '风趣幽默', '暖心知心']

const form = reactive({
  surname: '',
  gender: '女',
  personality: '踏实务实',
})

const loading = ref(false)
const error = ref('')

function tryVoice(p) {
  // 按人格音色合成一句试听；后端无火山 key 时 audio_url 为 null，给出提示
  error.value = ''
  api
    .tts({
      text: '您好，我是小忆，很高兴能陪伴您～',
      voice_type: PERSONA_VOICE[p] || '',
    })
    .then((res) => {
      const url = res.data && res.data.audio_url
      if (!url) {
        error.value = '试听暂不可用（未配置语音服务）。'
        return
      }
      const a = new Audio(url)
      a.play().catch(() => {})
    })
    .catch((e) => {
      console.error(e)
      error.value = '试听失败，请稍后再试。'
    })
}

async function start() {
  if (!form.surname || loading.value) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.setup({
      surname: form.surname,
      gender: form.gender,
      personality: form.personality,
      is_muted: false,
    })
    session.init({
      surname: form.surname,
      gender: form.gender,
      personality: form.personality,
      convId: res.data.conv_id,
      chatHistory: res.data.chat_history,
    })
    router.push('/chat')
  } catch (e) {
    error.value = '进入聊天失败，请确认后端已启动后重试。'
    console.error(e)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.welcome-page {
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--color-bg);
}
.welcome-card {
  width: 100%;
  max-width: 480px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.brand {
  margin: 0;
  font-size: 44px;
  font-weight: 800;
  color: var(--color-accent);
  text-align: center;
}
.slogan {
  margin: 0 0 12px;
  font-size: var(--font-subtitle);
  color: var(--color-text-sub);
  text-align: center;
}
.form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 0;
  padding: 0;
  border: none;
}
.field-label {
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text);
}
.radio-row {
  display: flex;
  gap: 12px;
}
.radio-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.radio-card {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: var(--btn-height);
  padding: 0 16px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
}
.radio-card input {
  width: 18px;
  height: 18px;
  accent-color: var(--color-accent);
}
.radio-card span {
  font-size: var(--font-body);
  color: var(--color-text);
}
.try-btn {
  margin-left: auto;
  min-height: 40px;
  padding: 0 14px;
  font-size: var(--font-small);
  color: var(--color-accent2);
  background: none;
  border: 1px solid var(--color-accent2);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.submit-btn {
  width: 100%;
  font-size: var(--font-body);
}
.error-tip {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--font-body);
  text-align: center;
}
</style>