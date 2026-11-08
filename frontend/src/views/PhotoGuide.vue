<template>
  <div class="page">
    <header class="topbar">
      <button class="back-link" type="button" @click="$router.push('/chat')">← 返回</button>
      <span class="topbar-title">📷 拍照小助手</span>
      <span class="topbar-sub">拍清楚，小忆才认得准</span>
    </header>

    <div class="content">
      <!-- 两种场景各一套：老照片 / 说明书 -->
      <div class="scene-tabs">
        <button
          v-for="s in SCENES"
          :key="s.id"
          type="button"
          class="scene-tab"
          :class="{ on: s.id === sceneId }"
          @click="pick(s.id)"
        >
          <span class="tab-icon" aria-hidden="true">{{ s.icon }}</span>
          <span class="tab-name">{{ s.name }}</span>
          <span class="tab-sub">{{ s.sub }}</span>
        </button>
      </div>

      <section class="card tips-card">
        <h2 class="tips-title">{{ current.name }}，照这三条拍</h2>

        <ol class="tips">
          <li v-for="(t, i) in current.tips" :key="t.art" class="tip">
            <span class="tip-no" aria-hidden="true">{{ i + 1 }}</span>

            <!-- 示意图例：放平 / 光线 / 距离 -->
            <svg class="tip-art" viewBox="0 0 96 96" aria-hidden="true">
              <template v-if="t.art === 'flat'">
                <line x1="8" y1="72" x2="88" y2="72" />
                <rect x="24" y="36" width="48" height="30" rx="4" />
                <line x1="31" y1="45" x2="65" y2="45" />
                <line x1="31" y1="53" x2="56" y2="53" />
                <path d="M48 18 v10 M43 23 l5 5 l5 -5" />
              </template>
              <template v-else-if="t.art === 'light'">
                <circle cx="26" cy="24" r="8" />
                <path d="M26 40 v6 M11 24 h6 M40 24 h6 M16 14 l4 4 M36 14 l-4 4" />
                <rect x="26" y="54" width="44" height="24" rx="3" />
                <line x1="32" y1="62" x2="64" y2="62" />
                <line x1="32" y1="69" x2="56" y2="69" />
              </template>
              <template v-else>
                <rect x="8" y="42" width="34" height="26" rx="3" />
                <rect x="70" y="30" width="18" height="40" rx="3" />
                <line x1="44" y1="55" x2="68" y2="55" stroke-dasharray="4 4" />
                <path d="M44 55 l5 -3 M44 55 l5 3 M68 55 l-5 -3 M68 55 l-5 3" />
                <text x="40" y="44" class="art-label">20厘米</text>
              </template>
            </svg>

            <div class="tip-text">
              <span class="tip-title">{{ t.title }}</span>
              <p class="tip-desc">{{ t.text }}</p>
            </div>
          </li>
        </ol>

        <button class="btn btn-primary go-btn" type="button" @click="goShoot">
          📸 知道了，去拍照
        </button>
      </section>

      <!-- A/B 对照用的本地开关：不写进老人日常动线，放在页面最底下、字号小一级 -->
      <section class="card test-card">
        <p class="test-title">测试开关（平时不用动）</p>
        <p class="test-desc">
          关掉后，拍照时不显示要点提示，也不弹“太暗/太糊”气泡，照片照常发送。
          做 A/B 对照（同一老人有/无引导各 20 张）时用，测完记得打开。
        </p>
        <button class="btn btn-secondary" type="button" @click="toggleOff">
          {{ off ? '拍照引导已关 · 点我打开' : '拍照引导开着 · 点我关掉' }}
        </button>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { photoGuide, SCENES } from '../data/photoGuide'

const router = useRouter()

const sceneId = ref(photoGuide.scene())
const off = ref(photoGuide.getState().off)

const current = computed(() => SCENES.find((s) => s.id === sceneId.value) || SCENES[0])

function pick(id) {
  sceneId.value = photoGuide.setScene(id)
}

function toggleOff() {
  off.value = photoGuide.setGuideOff(!off.value)
}

// 带着场景去聊天页，聊天页会顺手把相机打开（不用老人再找一次拍照按钮）
function goShoot() {
  photoGuide.setScene(sceneId.value)
  router.push({ path: '/chat', query: { camera: '1' } })
}
</script>

<style scoped>
.topbar-sub {
  margin-left: auto;
  font-size: var(--font-body);
  color: var(--color-text-sub);
}
.content {
  flex: 1;
  width: 100%;
  max-width: 860px;
  margin: 0 auto;
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.scene-tabs {
  display: flex;
  gap: 12px;
}
.scene-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-height: 72px;
  padding: 12px;
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
}
.scene-tab.on {
  border-color: var(--color-accent);
  background: var(--color-surface-soft);
}
.tab-icon {
  font-size: 26px;
  line-height: 1.1;
}
.tab-name {
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-text);
}
.tab-sub {
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.tips-card {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.tips-title {
  margin: 0;
  font-size: var(--font-title);
  color: var(--color-text);
}
.tips {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.tip {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px;
  background: var(--color-surface-soft);
  border-radius: var(--radius-md);
}
.tip-no {
  flex-shrink: 0;
  width: 36px;
  height: 36px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 700;
  color: #FFFFFF;
  background: var(--color-accent2);
  border-radius: 50%;
}
.tip-art {
  flex-shrink: 0;
  width: 72px;
  height: 72px;
  stroke: var(--color-accent-deep);
  stroke-width: 3;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.art-label {
  stroke: none;
  fill: var(--color-text-sub);
  font-size: 11px;
}
.tip-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.tip-title {
  font-size: var(--font-subtitle);
  font-weight: 700;
  color: var(--color-text);
}
.tip-desc {
  margin: 0;
  font-size: var(--font-body);
  line-height: 1.55;
  color: var(--color-text-sub);
}
.go-btn {
  align-self: stretch;
  font-size: var(--font-subtitle);
}
.test-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.test-title {
  margin: 0;
  font-size: var(--font-small);
  font-weight: 700;
  color: var(--color-text-sub);
}
.test-desc {
  margin: 0;
  font-size: var(--font-small);
  line-height: 1.5;
  color: var(--color-text-sub);
}
@media (max-width: 768px) {
  .tip-art {
    width: 56px;
    height: 56px;
  }
  .topbar-sub {
    display: none;
  }
}
</style>
