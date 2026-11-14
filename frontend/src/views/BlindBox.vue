<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🎁 今天的盲盒</span>
      <span class="topbar-sub">一天一个</span>
    </header>

    <div class="content">
      <section class="card stage" :class="{ opening: opening }">
        <!-- 未打开：只有一个“打开今天的盲盒”，没有“再来一次” -->
        <div v-if="!revealed" class="closed">
          <div class="gift" aria-hidden="true">🎁</div>
          <p class="big-hint">一天一个小惊喜</p>
          <button
            class="btn btn-primary open-btn"
            type="button"
            :disabled="opening"
            @click="startOpen"
          >
            {{ opening ? '正在打开…' : '打开今天的盲盒' }}
          </button>
          <p class="small-hint">开出什么，今天都是它。明天再来看新的。</p>
        </div>

        <!-- 已打开：大字记忆 + 照片 + 念给我听（没有重开入口）；素材是精选演示内容，如实标注 -->
        <div v-else class="revealed" aria-live="polite">
          <span class="tag">今天的小惊喜</span>
          <p class="memory-text">{{ moment.text }}</p>
          <p class="demo-note">{{ DEMO_TAG }}·我们为您精选的暖心瞬间，不是您真实的回忆</p>
          <div v-if="moment.photo && !photoBroken" class="photo-wrap">
            <img class="photo" :src="moment.photo" alt="今天的小惊喜照片" @error="photoBroken = true" />
            <span class="demo-tag">{{ DEMO_TAG }}</span>
          </div>
          <p class="when-text">{{ dateText }}</p>
          <button class="btn btn-secondary speak-btn" type="button" @click="speakMemory">
            🔊 念给我听
          </button>
        </div>
      </section>

      <section class="card note-card">
        <p v-if="alreadyOpen" class="note-text">
          今天已经开过了，明天还有一个小惊喜。
        </p>
        <p class="note-text">
          想再听一遍，跟小忆说一声“再放一个”，它给您念。
        </p>
        <button
          class="btn btn-secondary bottle-btn"
          type="button"
          @click="$router.push('/bottle')"
        >
          🫙 去收集瓶看看
        </button>
      </section>
    </div>

    <audio ref="audioEl" class="hidden-audio" @ended="playing = false" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
import { warm } from '../services/warmDependency'
import { DEMO_TAG } from '../data/warmMoments'

const peek = warm.peekBox()

// 打开状态：进页面时只读（不消耗额度），点按钮才真正开
const revealed = ref(peek.opened)
const alreadyOpen = ref(peek.opened)
const moment = ref(peek.moment || { text: '', photo: '' })
const opening = ref(false)

const audioEl = ref(null)
const playing = ref(false)
// 演示照片是远程图片：加载失败就整块隐藏，不留裂图
const photoBroken = ref(false)

const dateText = computed(() => {
  const at = moment.value && moment.value.at
  const d = at ? new Date(at) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}月${d.getDate()}日`
})

function startOpen() {
  if (opening.value || revealed.value) return
  opening.value = true
  const r = warm.openBox()
  // 开启动画：1.3 秒后揭晓；动画只是视觉，额度判定在 openBox 里
  setTimeout(() => {
    opening.value = false
    if (r.moment) moment.value = r.moment
    if (r.ok) {
      alreadyOpen.value = false
    } else {
      // 别的标签页已经开过：那就直接展示同一条，不给重开
      alreadyOpen.value = true
    }
    revealed.value = true
  }, 1300)
}

async function speakMemory() {
  const text = (moment.value && moment.value.text) || ''
  if (!text) return
  const el = audioEl.value
  try {
    const res = await api.tts({ text })
    const url = res.data && res.data.audio_url
    if (el && url) {
      el.src = url
      playing.value = true
      el.play().catch(() => {
        playing.value = false
      })
    }
  } catch (e) {
    // 没有 TTS 时不挡流程，字已经在屏幕上
    playing.value = false
  }
}
</script>

<style scoped>
.content {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.stage {
  min-height: 380px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  transition: transform 0.15s ease;
}
.opening .gift {
  animation: box-shake 1.2s ease-in-out;
}
@keyframes box-shake {
  0%,
  100% {
    transform: rotate(0) scale(1);
  }
  15% {
    transform: rotate(-7deg) scale(1.04);
  }
  35% {
    transform: rotate(7deg) scale(1.06);
  }
  55% {
    transform: rotate(-5deg) scale(1.08);
  }
  75% {
    transform: rotate(4deg) scale(1.1);
  }
  90% {
    transform: rotate(0) scale(1.14);
  }
}
.gift {
  font-size: 88px;
  line-height: 1;
  margin-bottom: 18px;
}
.big-hint {
  margin: 0 0 20px;
  font-size: 30px;
  font-weight: 700;
  color: var(--color-text);
}
.open-btn {
  min-height: 72px;
  min-width: 240px;
  font-size: 24px;
}
.small-hint {
  margin: 18px 0 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
  line-height: 1.7;
}
.revealed {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  width: 100%;
  animation: reveal-in 0.35s ease-out;
}
@keyframes reveal-in {
  from {
    opacity: 0;
    transform: scale(0.96);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}
@media (prefers-reduced-motion: reduce) {
  .opening .gift,
  .revealed {
    animation: none;
  }
}
.tag {
  padding: 4px 18px;
  font-size: 20px;
  font-weight: 700;
  color: #FFFFFF;
  background: var(--color-accent2);
  border-radius: 999px;
}
/* 大字记忆：34px，1 米外可读 */
.memory-text {
  margin: 0;
  font-size: 34px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text);
  word-break: break-word;
}
.photo-wrap {
  position: relative;
  width: 100%;
  padding: 8px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.photo {
  display: block;
  width: 100%;
  max-height: 260px;
  object-fit: contain;
  border-radius: var(--radius-sm);
}
.demo-tag {
  position: absolute;
  right: 14px;
  bottom: 14px;
  padding: 2px 10px;
  font-size: var(--font-small);
  font-weight: 700;
  color: #FFFFFF;
  background: rgba(45, 27, 14, 0.72);
  border-radius: 999px;
}
.demo-note {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.when-text {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.speak-btn {
  min-height: 64px;
  font-size: var(--font-bubble);
}
.note-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.note-text {
  margin: 0;
  font-size: 20px;
  line-height: 1.7;
  color: var(--color-text);
}
.bottle-btn {
  min-height: 64px;
  font-size: var(--font-bubble);
}
.hidden-audio {
  display: none;
}
</style>
