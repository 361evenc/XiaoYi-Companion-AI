import { defineStore } from 'pinia'

// 单机单人会话状态：本地 localStorage 持久化（无登录、无 token）。
// chatHistory 仅在内存中传递（Welcome→Chat / History→Chat），不落盘 ——
// 完整聊天记录已持久化在后端 conversations.json，这里只做会话内接力。
const STORAGE_KEY = 'xiaoyi-session'

function load() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}
  } catch {
    return {}
  }
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    surname: '',            // 老人姓氏
    gender: '女',          // 称呼性别
    personality: '踏实务实', // 小忆性格（音色随之切换）
    isMuted: false,         // 静音开关
    convId: null,           // 当前对话 id
    initialized: false,     // 是否已走完欢迎页
    chatHistory: [],        // 当前会话消息（内存接力，不持久化）
    ...load(),
  }),
  actions: {
    init({ surname, gender, personality, convId = null, chatHistory = [] }) {
      this.surname = surname
      this.gender = gender
      this.personality = personality
      this.convId = convId
      this.chatHistory = chatHistory
      this.initialized = true
      this.save()
    },
    setConversation(convId, chatHistory = []) {
      this.convId = convId
      this.chatHistory = chatHistory
    },
    setConvId(id) {
      this.convId = id
    },
    setPersonality(p) {
      this.personality = p
      this.save()
    },
    toggleMute() {
      this.isMuted = !this.isMuted
      this.save()
    },
    save() {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          surname: this.surname,
          gender: this.gender,
          personality: this.personality,
          isMuted: this.isMuted,
          convId: this.convId,
          initialized: this.initialized,
        })
      )
    },
  },
})