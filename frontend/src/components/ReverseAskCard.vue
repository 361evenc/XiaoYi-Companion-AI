<template>
  <section class="ask-card" :class="{ done: answered }" aria-live="polite">
    <div class="ask-head">
      <span class="ask-icon" aria-hidden="true">📮</span>
      <span class="ask-label">小忆想请您帮个小忙</span>
    </div>

    <p v-if="!answered" class="ask-task">告诉我今天天气怎么样？</p>
    <p v-else class="ask-task done-text">记下了：您说「{{ answerText }}」</p>

    <p class="ask-hint">
      {{ answered ? '今天说过了，小忆记着呢。' : '对着话筒说就行，怎么说都算。' }}
    </p>

    <button
      v-if="!answered"
      class="btn btn-secondary ask-btn"
      type="button"
      @click="$emit('ask')"
    >
      🔊 小忆先问一句
    </button>
  </section>
</template>

<script setup>
defineProps({
  answered: { type: Boolean, default: false },
  answerText: { type: String, default: '' },
})
defineEmits(['ask'])
</script>

<style scoped>
.ask-card {
  margin: 0 20px 12px;
  padding: 16px 18px;
  background: #FFF8EA;
  border: 2px solid #D99A45;
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ask-card.done {
  background: #F3F8F4;
  border-color: var(--color-accent2);
}
.ask-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ask-icon {
  font-size: 26px;
  line-height: 1;
}
.ask-label {
  font-size: var(--font-body);
  font-weight: 700;
  color: var(--color-text-sub);
}
/* 任务是大字：老人一眼看清要说什么 */
.ask-task {
  margin: 0;
  font-size: 30px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--color-text);
  word-break: break-word;
}
.done-text {
  font-size: 24px;
}
.ask-hint {
  margin: 0;
  font-size: var(--font-small);
  color: var(--color-text-sub);
}
.ask-btn {
  align-self: flex-start;
  min-height: 56px;
  font-size: var(--font-body);
}
</style>
