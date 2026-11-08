<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">🫙 小忆的收集瓶</span>
    </header>

    <div class="content">
      <!-- 统计只有“一共攒了几个”：没有周统计、没有连续天数 -->
      <section class="card count-card">
        <p class="count-line">
          一共攒了 <strong class="count-num">{{ summary.total }}</strong> 个美好瞬间
        </p>
        <p class="count-sub">想起来的时候，点开听听。</p>
      </section>

      <!-- 子女语音/夸赞：每天一次的正反馈，前奏音只响这一下 -->
      <section class="card family-card">
        <p class="family-title">📞 孩子留了一段话</p>
        <p class="family-sub">（演示素材，B-7 接入后换成孩子的真实语音）</p>
        <button class="btn btn-secondary family-btn" type="button" @click="playFamilyVoice">
          🔊 听听孩子的话
        </button>
        <p class="family-hint">一天听一回，听过了就留到明天。</p>
      </section>

      <section class="card list-card">
        <h2 class="section-title">攒下的瞬间</h2>
        <p v-if="!moments.length" class="empty-hint">
          瓶子还是空的。日子过着过着，就攒起来了。
        </p>
        <ol v-else class="moment-list">
          <li v-for="m in moments" :key="m.id" class="moment-item">
            <div v-if="m.photo" class="photo-wrap">
              <img class="photo" :src="m.photo" alt="这一天的照片" />
              <span class="demo-tag">{{ DEMO_TAG }}</span>
            </div>
            <p class="moment-text">{{ m.text }}</p>
            <div class="moment-foot">
              <span class="moment-date">{{ dateText(m.at) }}</span>
              <button class="btn btn-secondary replay-btn" type="button" @click="replay(m)">
                🔊 听这段回忆
              </button>
            </div>
          </li>
        </ol>
      </section>
    </div>

    <div v-if="caption" class="caption">
      <span v-if="playing" class="caption-hint">🔊 小忆正在说</span>
      <span class="caption-text">{{ caption }}</span>
    </div>

    <audio ref="audioEl" class="hidden-audio" @ended="playing = false" />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { api } from '../api'
import { warm } from '../services/warmDependency'
import { DEMO_TAG } from '../data/warmMoments'

const summary = ref(warm.getBottleSummary())
const moments = ref(warm.getMoments())

const audioEl = ref(null)
const caption = ref('')
const playing = ref(false)

function dateText(iso) {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return ''
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

// 只播语音，不加前奏音：回放已收集的瞬间不是“新的好事”
async function speakLine(text) {
  if (!text) return
  caption.value = text
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
    playing.value = false
  }
}

function replay(m) {
  speakLine(m.text)
}

// 子女语音属于正反馈事件：前奏音 1 次 → 播报 1 次；同一天只响一次
async function playFamilyVoice() {
  const r = await warm.beginPositive('family_voice', {
    speak: (feed) => speakLine(feed.text),
  })
  if (!r.ok) {
    speakLine('这段话今天听过了，我给您留着，明天再听。')
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
.count-card {
  text-align: center;
}
.count-line {
  margin: 0;
  font-size: 24px;
  line-height: 1.5;
  color: var(--color-text);
}
.count-num {
  font-size: 44px;
  font-weight: 700;
  color: var(--color-accent2-deep);
}
.count-sub {
  margin: 6px 0 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.family-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-left: 6px solid var(--color-accent2);
}
.family-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
}
.family-sub {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.family-btn {
  align-self: flex-start;
  min-height: 64px;
  font-size: var(--font-bubble);
}
.family-hint {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.list-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.section-title {
  margin: 0;
  font-size: var(--font-subtitle);
  color: var(--color-text);
}
.empty-hint {
  margin: 0;
  font-size: var(--font-body);
  color: var(--color-text-sub);
  line-height: 1.7;
}
.moment-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.moment-item {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.photo-wrap {
  position: relative;
  padding: 8px;
  background: var(--color-surface);
  border-radius: var(--radius-sm);
}
.photo {
  display: block;
  width: 100%;
  max-height: 220px;
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
.moment-text {
  margin: 0;
  font-size: 22px;
  line-height: 1.6;
  color: var(--color-text);
  word-break: break-word;
}
.moment-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.moment-date {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.replay-btn {
  min-height: 56px;
  font-size: var(--font-body);
}
.caption {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}
.caption-hint {
  flex-shrink: 0;
  font-size: var(--font-small);
  font-weight: 700;
  color: var(--color-accent2);
}
.caption-text {
  font-size: var(--font-bubble);
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text);
  word-break: break-word;
}
.hidden-audio {
  display: none;
}
</style>
