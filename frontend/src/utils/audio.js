// 语音工具：前端录音 + 转码为 16kHz 单声道 WAV base64，供后端 /api/asr 识别。
// 浏览器 MediaRecorder 产出的多为 webm/opus，火山 ASR 要求 WAV 16k 16bit 单声道，
// 因此在这里用 Web Audio API 解码并重采样到 16k，再封装成标准 WAV。

// 人格 → 火山 TTS 音色（与后端 app.PERSONA_VOICE 保持一致）
export const PERSONA_VOICE = {
  踏实务实: 'BV705_streaming',
  风趣幽默: 'BV123_streaming',
  暖心知心: 'BV405_streaming',
}

const TARGET_RATE = 16000
const CHUNK = 0x8000 // 每段 btoa 的字节数，避免超长字符串栈溢出

function writeString(view, offset, str) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i))
  }
}

// Float32 采样 → 16bit PCM ArrayBuffer
function floatTo16BitPCM(float32) {
  const buffer = new ArrayBuffer(float32.length * 2)
  const view = new DataView(buffer)
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return buffer
}

// PCM16 ArrayBuffer → 标准 WAV ArrayBuffer（单声道）
function encodeWav(pcmBuffer, sampleRate) {
  const numChannels = 1
  const bytesPerSample = 2
  const blockAlign = numChannels * bytesPerSample
  const byteRate = sampleRate * blockAlign
  const dataSize = pcmBuffer.byteLength
  const buffer = new ArrayBuffer(44 + dataSize)
  const view = new DataView(buffer)

  writeString(view, 0, 'RIFF')
  view.setUint32(4, 36 + dataSize, true)
  writeString(view, 8, 'WAVE')
  writeString(view, 12, 'fmt ')
  view.setUint32(16, 16, true) // fmt 块长度
  view.setUint16(20, 1, true) // 编码格式：PCM
  view.setUint16(22, numChannels, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, byteRate, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, bytesPerSample * 8, true) // 16 位
  writeString(view, 36, 'data')
  view.setUint32(40, dataSize, true)

  new Uint8Array(buffer, 44, dataSize).set(new Uint8Array(pcmBuffer))
  return buffer
}

// 任意浏览器音频 → 16kHz 单声道 Float32Array（用离线渲染做高质量重采样+降声道）
async function decodeTo16kMono(arrayBuffer) {
  // 先解码出原始 AudioBuffer（得到采样率/声道数/时长）
  const tmpCtx = new (window.AudioContext || window.webkitAudioContext)()
  const decoded = await tmpCtx.decodeAudioData(arrayBuffer.slice(0))
  await tmpCtx.close()

  // 用目标采样率的离线上下文做重采样 + 单声道混音，避免手动插值失真
  const offline = new OfflineAudioContext(
    1,
    Math.max(1, Math.ceil(decoded.duration * TARGET_RATE)),
    TARGET_RATE
  )
  const src = offline.createBufferSource()
  src.buffer = decoded
  src.connect(offline.destination)
  src.start()
  const rendered = await offline.startRendering()
  return rendered.getChannelData(0)
}

// ArrayBuffer → base64（分段 btoa，兼容长数据）
function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (let i = 0; i < bytes.length; i += CHUNK) {
    const slice = bytes.subarray(i, i + CHUNK)
    binary += String.fromCharCode.apply(null, slice)
  }
  return btoa(binary)
}

// 把任意音频 Blob 转成 16k 单声道 WAV 的 base64 字符串
export async function blobToWav16kBase64(blob) {
  const arrayBuffer = await blob.arrayBuffer()
  const mono = await decodeTo16kMono(arrayBuffer)
  const pcm = floatTo16BitPCM(mono)
  const wav = encodeWav(pcm, TARGET_RATE)
  return arrayBufferToBase64(wav)
}

// 录音器：start() 开始，stop() 停止并返回 16k WAV base64。
export function createRecorder() {
  let mediaRecorder = null
  let stream = null
  const chunks = []

  return {
    async start() {
      chunks.length = 0
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      mediaRecorder = new MediaRecorder(stream)
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunks.push(e.data)
      }
      mediaRecorder.start()
    },

    stop() {
      return new Promise((resolve, reject) => {
        if (!mediaRecorder) {
          reject(new Error('尚未开始录音'))
          return
        }
        mediaRecorder.onstop = async () => {
          try {
            if (stream) stream.getTracks().forEach((t) => t.stop())
            const blob = new Blob(chunks, {
              type: mediaRecorder.mimeType || 'audio/webm',
            })
            const base64 = await blobToWav16kBase64(blob)
            resolve(base64)
          } catch (err) {
            reject(err)
          }
        }
        mediaRecorder.stop()
      })
    },

    // 取消当前录音（不返回结果）
    cancel() {
      if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.onstop = null
        mediaRecorder.stop()
      }
      if (stream) stream.getTracks().forEach((t) => t.stop())
    },
  }
}