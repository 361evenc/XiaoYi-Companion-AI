<template>
  <div class="bubble" :class="role === 'user' ? 'bubble-user' : 'bubble-bot'">
    <div v-if="role === 'bot'" class="bubble-name">小忆</div>
    <div class="bubble-text"><slot>{{ text }}</slot></div>
  </div>
</template>

<script setup>
defineProps({
  role: { type: String, default: 'bot' }, // 'user' | 'bot'
  text: { type: String, default: '' },
})
</script>

<style scoped>
.bubble-name {
  font-size: var(--font-small);
  font-weight: 700;
  color: var(--color-accent2);
  margin-bottom: 4px;
}
.bubble-text {
  font-size: var(--font-bubble);
  line-height: 1.55;
}
</style>