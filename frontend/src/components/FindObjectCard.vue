<template>
  <section class="find-card" :class="toneClass" aria-live="polite">
    <div class="find-head">
      <span class="find-label">{{ sourceLabel }}</span>
      <span v-if="whenText" class="find-when">{{ whenText }}</span>
    </div>

    <!-- 有依据：显示位置 + 时间（图有则显、无则不显） -->
    <template v-if="!isNone && !isTooOld">
      <p class="find-position">{{ positionText }}</p>
      <p v-if="isStale" class="find-stale">好久没见它了，还在不？</p>
      <div v-if="imageSrc" class="find-photo-wrap">
        <img
          class="find-photo"
          :src="imageSrc"
          :alt="`${objectName}的照片`"
        />
        <span v-if="isDemoImage" class="find-demo-tag">演示图</span>
      </div>
    </template>

    <!-- 超过 180 天：不主动给位置，避免把旧信息说成现在 -->
    <template v-else-if="isTooOld">
      <p class="find-position">太久没见着了，我不敢瞎说。</p>
      <p class="find-sub">{{ objectName }}还在原先那儿不？您要是见着了跟我说一声。</p>
    </template>

    <!-- 找不到：不显示空白结果，先陪老人一起回忆 -->
    <template v-else>
      <p class="find-position">我没瞅见{{ objectName }}</p>
      <p class="find-sub">您最后放哪儿了？咱一起想想。</p>
      <p class="find-hint">想不起来也没事，您说一声放哪了，我记上，下次就找得快。</p>
    </template>
  </section>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  data: { type: Object, required: true },
})

// 演示开关：默认关闭。需要演示裁剪图时，启动前设置
//   PowerShell: $env:VITE_FIND_DEMO='1'; python run.py
// 只对 graph（"我瞅见过"）生效，并在图上明确标"演示图"，不冒充真实隐私检测结果。
const DEMO_ON = import.meta.env.VITE_FIND_DEMO === '1'
const DEMO_IMG = '/demo/find_crop_demo.jpg'

const tier = computed(() => props.data.tier || 'none')
const isNone = computed(() => tier.value === 'none')
const isTooOld = computed(() => Boolean(props.data.too_old))
const isStale = computed(() => Boolean(props.data.stale))
const objectName = computed(() => props.data.object || '东西')

const sourceLabel = computed(() => {
  if (isNone.value) return '🤔 这次没找着'
  if (props.data.source === 'text') return '💬 您上次说的'
  return '🧐 我瞅见过'
})

const positionText = computed(() => props.data.position || '')

// 只有 graph 层才可能有照片；text 层是"您说过"，没有画面，不能配图
const realCrop = computed(() => {
  const d = props.data
  if (d.has_clean_crop !== true) return ''
  return typeof d.crop_url === 'string' ? d.crop_url : ''
})

const imageSrc = computed(() => {
  if (isNone.value || isTooOld.value || props.data.source !== 'graph') return ''
  if (realCrop.value) return realCrop.value
  return DEMO_ON ? DEMO_IMG : ''
})

const isDemoImage = computed(() => Boolean(imageSrc.value) && !realCrop.value)

const toneClass = computed(() => {
  if (isNone.value) return 'tone-none'
  if (isStale.value || isTooOld.value) return 'tone-stale'
  return 'tone-graph'
})

// 时间展示：老人不看"2026-11-08 18:32"，要看"昨天傍晚""3天前"
const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

function periodOf(d) {
  const h = d.getHours()
  if (h < 5) return '夜里'
  if (h < 11) return '早上'
  if (h < 13) return '中午'
  if (h < 17) return '下午'
  if (h < 19) return '傍晚'
  if (h < 23) return '晚上'
  return '夜里'
}

const whenText = computed(() => {
  if (isNone.value || isTooOld.value) return ''
  const ts = Number(props.data.last_seen_at)
  if (!Number.isFinite(ts) || ts <= 0) return ''
  const then = new Date(ts * 1000)
  const diffDays = Math.round((startOfDay(new Date()) - startOfDay(then)) / DAY_MS)
  if (diffDays <= 0) return `今天${periodOf(then)}`
  if (diffDays === 1) return `昨天${periodOf(then)}`
  if (diffDays < 30) return `${diffDays}天前`
  return `${then.getMonth() + 1}月${then.getDate()}日`
})
</script>

<style scoped>
.find-card {
  margin: 0 20px 12px;
  padding: 16px 18px;
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  animation: find-in 0.28s ease-out;
}
.find-card.tone-graph {
  border-color: var(--color-accent2);
  background: #F3F8F4;
}
.find-card.tone-stale {
  border-color: #D99A45;
  background: #FFF8EA;
}
.find-card.tone-none {
  border-color: var(--color-accent);
  background: #FFF6F0;
}

.find-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}
.find-label {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
}
.find-when {
  flex-shrink: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--color-accent2-deep);
}

/* 适老红线：位置 ≥32px、加粗、高对比，1 米外可读 */
.find-position {
  margin: 0 0 6px;
  font-size: 34px;
  font-weight: 700;
  line-height: 1.25;
  color: var(--color-text);
  word-break: break-word;
}
.find-sub {
  margin: 0;
  font-size: 24px;
  line-height: 1.5;
  color: var(--color-text);
}
.find-hint {
  margin: 8px 0 0;
  font-size: 20px;
  line-height: 1.5;
  color: var(--color-text-sub);
}
.find-stale {
  margin: 4px 0 0;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.4;
  color: #8A5A12;
}

.find-photo-wrap {
  position: relative;
  margin-top: 12px;
  padding: 8px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.find-photo {
  display: block;
  width: 100%;
  max-height: 200px;
  object-fit: contain;
  border-radius: var(--radius-sm);
}
.find-demo-tag {
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

@keyframes find-in {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
@media (prefers-reduced-motion: reduce) {
  .find-card {
    animation: none;
  }
}
</style>
