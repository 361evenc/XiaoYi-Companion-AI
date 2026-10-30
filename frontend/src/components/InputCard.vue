<template>
  <div class="input-card">
    <!-- 快捷语：一键发起常见话题 -->
    <div class="quick-btns">
      <button
        v-for="q in quickPhrases"
        :key="q"
        type="button"
        class="quick-btn"
        @click="$emit('quick', q)"
      >
        {{ q }}
      </button>
    </div>

    <!-- 输入区 -->
    <div class="input-row">
      <textarea
        v-model="draft"
        class="textarea input-box"
        rows="2"
        placeholder="想对小忆说点什么…"
        @keydown.enter.exact.prevent="submit"
      />
      <button type="button" class="btn btn-primary send-btn" @click="submit">发送</button>
    </div>

    <!-- 工具条：语音 / 图片 / 拍照 / 静音 / 撤回 -->
    <div class="tool-row">
      <button
        type="button"
        class="tool-btn mic-btn"
        :class="{ recording }"
        :title="recording ? '点击结束录音' : '点击开始说话'"
        @click="toggleMic"
      >
        {{ recording ? '🔴' : '🎙️' }}
      </button>
      <button type="button" class="tool-btn" title="发张照片" @click="pickPhoto">🖼️</button>
      <button type="button" class="tool-btn" title="拍照" @click="openCamera">📷</button>
      <button type="button" class="chip" :class="{ on: muted }" @click="$emit('toggle-mute')">
        {{ muted ? '🔇 已静音' : '🔊 语音开' }}
      </button>
      <button type="button" class="tool-btn" title="撤回上一条" @click="$emit('recall')">↩️</button>
    </div>

    <!-- 隐藏文件选择：上传图片 -->
    <input
      ref="fileInput"
      type="file"
      accept="image/*"
      class="hidden-input"
      @change="onPickFile"
    />

    <!-- 摄像头拍照覆盖层 -->
    <div v-if="cameraOpen" class="camera-mask">
      <div class="camera-panel card">
        <video ref="videoEl" class="camera-video" autoplay playsinline></video>
        <div class="camera-actions">
          <button type="button" class="btn btn-primary" @click="snapPhoto">📸 拍照</button>
          <button type="button" class="btn btn-secondary" @click="closeCamera">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref } from 'vue'
import { createRecorder } from '../utils/audio'

defineProps({
  muted: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'quick', 'toggle-mute', 'audio', 'recording-error', 'photo', 'photo-error', 'recall'])

const draft = ref('')
const recording = ref(false)
const recorder = createRecorder()

// 图片上传 / 摄像头拍照
const fileInput = ref(null)
const cameraOpen = ref(false)
const videoEl = ref(null)
const cameraStream = ref(null)

const quickPhrases = [
  '我今天挺好的',
  '有点想你了',
  '身体有点不舒服',
  '讲讲以前的事',
]

function submit() {
  const text = draft.value.trim()
  if (!text) return
  draft.value = ''
  emit('send', text)
}

// 麦克风：点一下开始录音，再点一下结束并交给父组件做 ASR 识别
async function toggleMic() {
  if (recording.value) {
    recording.value = false
    try {
      const base64 = await recorder.stop()
      emit('audio', base64)
    } catch (e) {
      console.error(e)
      emit('recording-error', '录音失败，请允许麦克风权限后重试。')
    }
    return
  }
  try {
    await recorder.start()
    recording.value = true
  } catch (e) {
    console.error(e)
    recording.value = false
    emit('recording-error', '无法打开麦克风，请检查浏览器权限设置。')
  }
}

// ---------- 图片上传 / 摄像头拍照 ----------

// 从 dataURL 拆出 { mime, base64 }
function dataUrlToParts(dataUrl) {
  const m = /^data:([^;]+);base64,(.*)$/s.exec(dataUrl || '')
  return m ? { mime: m[1], base64: m[2] } : null
}

function emitPhoto(parts) {
  if (parts) emit('photo', parts)
  else emit('photo-error', '图片处理失败，请重试。')
}

// 上传图片：触发隐藏的文件选择器
function pickPhoto() {
  if (fileInput.value) fileInput.value.click()
}

function onPickFile(e) {
  const file = e.target.files && e.target.files[0]
  e.target.value = '' // 允许重复选同一个文件
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => emitPhoto(dataUrlToParts(reader.result))
  reader.onerror = () => emit('photo-error', '图片读取失败，请重试。')
  reader.readAsDataURL(file)
}

// 打开摄像头预览
async function openCamera() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } })
    cameraStream.value = stream
    cameraOpen.value = true
    await nextTick()
    if (videoEl.value) videoEl.value.srcObject = stream
  } catch (e) {
    console.error(e)
    emit('photo-error', '无法打开摄像头，请检查浏览器权限。')
  }
}

function closeCamera() {
  if (cameraStream.value) cameraStream.value.getTracks().forEach((t) => t.stop())
  cameraStream.value = null
  cameraOpen.value = false
}

// 拍照：捕获当前画面为 jpeg，缩放限制在 1280px 内避免过大
function snapPhoto() {
  const video = videoEl.value
  if (!video || !video.videoWidth) {
    emit('photo-error', '摄像头还没准备好，请稍等再拍。')
    return
  }
  const scale = Math.min(1, 1280 / video.videoWidth)
  const w = Math.round(video.videoWidth * scale)
  const h = Math.round(video.videoHeight * scale)
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  canvas.getContext('2d').drawImage(video, 0, 0, w, h)
  const parts = dataUrlToParts(canvas.toDataURL('image/jpeg', 0.85))
  closeCamera()
  emitPhoto(parts)
}
</script>

<style scoped>
.input-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: stretch;
}
.input-box {
  flex: 1;
  min-height: var(--input-height);
  font-size: var(--font-body);
}
.send-btn {
  flex-shrink: 0;
  min-width: 96px;
}
.tool-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tool-btn {
  min-width: 48px;
  min-height: 48px;
  font-size: 22px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.tool-btn:hover {
  border-color: var(--color-accent);
}
.mic-btn.recording {
  color: #FFF;
  background: var(--color-danger);
  border-color: var(--color-danger);
  animation: mic-pulse 1s ease-in-out infinite;
}
@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(179, 64, 47, 0.4); }
  50% { box-shadow: 0 0 0 8px rgba(179, 64, 47, 0); }
}
.chip {
  min-height: 48px;
  padding: 0 16px;
  font-size: var(--font-body);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 24px;
  cursor: pointer;
}
.chip.on {
  color: var(--color-accent2);
  border-color: var(--color-accent2);
  background: #EEF4F0;
}
.hidden-input {
  display: none;
}
.camera-mask {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(45, 27, 14, 0.6);
}
.camera-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
  max-width: 480px;
}
.camera-video {
  width: 100%;
  height: auto;
  border-radius: var(--radius-md);
  background: #000;
}
.camera-actions {
  display: flex;
  gap: 12px;
}
.camera-actions .btn {
  flex: 1;
}
</style>