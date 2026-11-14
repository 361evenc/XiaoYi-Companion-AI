/**
 * 温和依赖 UI 套件的本地契约（F-5）
 * ------------------------------------------------------------
 * 目标：把防沉迷护栏写进代码，而不是只写在文档里。
 *   - 盲盒：一天 1 个，限额到了只陈述，没有“再来一次”入口
 *   - 收集瓶：对外只暴露 total（不提供周统计/连续天数/缺席数据）
 *   - 前奏音：仅 3 类正反馈事件，1 次事件响 1 次，播报前响
 *   - 语音诱导：说“再放一个”有额度就放；到上限用 T-6 固定话术
 *
 * 状态落在 localStorage（单机单人），按自然日重置额度；瓶子不清空。
 * 不使用 Math.random 决定内容：盲盒按日期取，同一天重复打开看到的是同一条。
 */

import { T6_PARAMS } from '../data/t6Params.js'
import { DEMO_MOMENTS, BOX_MOMENTS } from '../data/warmMoments.js'

export const STORAGE_KEY = 'xiaoyi.warm.v1'

export const LIMITS = T6_PARAMS.limits
export const PRELUDE_EVENTS = T6_PARAMS.preludeEvents
export const FIXED_LIMIT_PHRASE = T6_PARAMS.limitPhrase
export const EXTRA_PLAY_PREFIX = T6_PARAMS.extraPlayPrefix
export const FORBIDDEN_PHRASES = T6_PARAMS.forbiddenPhrases

// 3 类正反馈事件对应的“好事”文案（真实播报内容由 B-7 素材库替换）
export const POSITIVE_FEEDBACK = {
  weather_answered: {
    label: '天气答对',
    text: '您说得真清楚，我听着心里就踏实。谢谢您跟我说这些。',
  },
  find_object_ok: {
    label: '找物成功',
    text: '找着了，您这记性真不错。',
  },
  family_voice: {
    label: '孩子的语音',
    text: '(演示)孩子说：妈，天凉了，出门记得添件衣裳。',
  },
}

// “再放一个”只说这一句，不做内容复述之外的承诺
const REPLAY_RE =
  /^(?:小忆|小忆啊|好|嗯|那|那就)?(?:再|在)(?:放|来|开|播|听)(?:一|一个|一遍|一回|次|一下)(?:个|遍|回|次|下)?(?:吧|呀|啊|呗|嘛)?$/

function dayKey(d) {
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function createMemoryStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  }
}

function defaultStorage() {
  try {
    if (typeof localStorage !== 'undefined') return localStorage
  } catch {
    /* 隐私模式下 localStorage 可能抛错 */
  }
  return createMemoryStorage()
}

// 默认前奏音：三个中低频音符，与到点提示音同一音区，避开老年性耳背的高频区
let sharedCtx = null
function defaultPreludePlayer() {
  try {
    const Ctx =
      typeof window !== 'undefined'
        ? window.AudioContext || window.webkitAudioContext
        : null
    if (!Ctx) return
    sharedCtx = sharedCtx || new Ctx()
    const start = sharedCtx.currentTime
    ;[392, 523.25, 659.25].forEach((f, i) => {
      const osc = sharedCtx.createOscillator()
      const gain = sharedCtx.createGain()
      osc.frequency.value = f
      osc.connect(gain)
      gain.connect(sharedCtx.destination)
      const t0 = start + i * 0.34
      gain.gain.setValueAtTime(0.0001, t0)
      gain.gain.exponentialRampToValueAtTime(0.28, t0 + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.28)
      osc.start(t0)
      osc.stop(t0 + 0.3)
    })
  } catch {
    /* 浏览器限制自动播放时忽略 */
  }
}

function pickBoxMoment(key) {
  let h = 0
  for (let i = 0; i < key.length; i += 1) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return BOX_MOMENTS[h % BOX_MOMENTS.length]
}

export function createWarmDependency(options = {}) {
  const storage = options.storage || defaultStorage()
  const nowFn = options.now || (() => new Date())
  const preludeWaitMs =
    options.preludeWaitMs !== undefined
      ? options.preludeWaitMs
      : T6_PARAMS.prelude.durationMs
  let player = options.player || defaultPreludePlayer

  function read() {
    try {
      const raw = storage.getItem(STORAGE_KEY)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  }

  function write(s) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(s))
    } catch {
      /* 存不下就算了，不阻断 UI */
    }
  }

  // 读取当天状态：跨天重置当日额度，保留收集瓶
  function load() {
    const today = dayKey(nowFn())
    const raw = read()
    const s = {
      date: today,
      boxUsed: 0,
      extraReplayUsed: 0,
      weatherAnswered: false,
      weatherAnswer: '',
      moments: [],
      positiveFired: {},
      ...(raw || {}),
    }
    if (raw && raw.date === today) return s
    s.date = today
    s.boxUsed = 0
    s.extraReplayUsed = 0
    s.weatherAnswered = false
    s.weatherAnswer = ''
    s.positiveFired = {}
    write(s)
    return s
  }

  function quotaOf(s) {
    return {
      boxUsed: s.boxUsed,
      boxLimit: LIMITS.boxPerDay,
      boxRemaining: Math.max(0, LIMITS.boxPerDay - s.boxUsed),
      extraReplayUsed: s.extraReplayUsed,
      extraReplayLimit: LIMITS.extraReplayPerDay,
      extraReplayRemaining: Math.max(0, LIMITS.extraReplayPerDay - s.extraReplayUsed),
    }
  }

  function allMoments() {
    const s = load()
    const seen = new Set()
    const out = []
    for (const m of [...(s.moments || []), ...DEMO_MOMENTS]) {
      if (!m || !m.id || seen.has(m.id)) continue
      seen.add(m.id)
      out.push(m)
    }
    return out.sort((a, b) => String(b.at || '').localeCompare(String(a.at || '')))
  }

  // 收集瓶对外只给“一共几个”，不派生周/连续/缺席任何指标
  function getBottleSummary() {
    return { total: allMoments().length }
  }

  function getMoments() {
    return allMoments()
  }

  function addMoment(moment) {
    if (!moment || !moment.text) return { ok: false, total: allMoments().length }
    const s = load()
    const id = moment.id || `m_${nowFn().getTime()}`
    if (!s.moments.some((m) => m.id === id)) {
      s.moments.unshift({
        id,
        at: moment.at || nowFn().toISOString(),
        text: moment.text,
        photo: moment.photo || '',
        voice: moment.voice_url || '',
      })
      write(s)
    }
    return { ok: true, total: allMoments().length }
  }

  // 盲盒内容：当天打开过的取当时存下的那条，没打开过的按日期取
  function boxMomentFor(s) {
    const base = pickBoxMoment(s.date)
    return s.moments.find((m) => m.id === base.id) || base
  }

  function peekBox() {
    const s = load()
    return {
      opened: s.boxUsed >= LIMITS.boxPerDay,
      moment: boxMomentFor(s),
      quota: quotaOf(s),
    }
  }

  function openBox() {
    const s = load()
    const moment = boxMomentFor(s)
    if (s.boxUsed >= LIMITS.boxPerDay) {
      return {
        ok: false,
        alreadyOpen: true,
        moment,
        phrase: T6_PARAMS.boxAlreadyPhrase,
        quota: quotaOf(s),
      }
    }
    s.boxUsed += 1
    const opened = { ...moment, at: nowFn().toISOString() }
    if (!s.moments.some((m) => m.id === moment.id)) s.moments.unshift(opened)
    write(s)
    return { ok: true, moment: opened, quota: quotaOf(s) }
  }

  function getQuota() {
    return quotaOf(load())
  }

  // 语音层额度：到上限只回固定话术，不再放内容
  function requestExtraPlay() {
    const s = load()
    if (s.extraReplayUsed >= LIMITS.extraReplayPerDay) {
      return { ok: false, phrase: FIXED_LIMIT_PHRASE, quota: quotaOf(s) }
    }
    s.extraReplayUsed += 1
    write(s)
    return { ok: true, phrase: EXTRA_PLAY_PREFIX, quota: quotaOf(s) }
  }

  /**
   * 语音诱导拦截：只有整句就是“再放一个/再放一遍”这类请求才拦，
   * 带内容的指令（如“再放一个戏曲”）照常交给对话模型。
   * 返回 null 表示没拦。
   */
  function handleVoiceRequest(text) {
    const norm = String(text || '')
      .replace(/[\s，,。.！!？?、~～]/g, '')
      .trim()
    if (!norm || !REPLAY_RE.test(norm)) return null

    const moment = allMoments()[0]
    if (!moment) {
      return { kind: 'empty', phrase: T6_PARAMS.bottleEmptyPhrase, quota: getQuota() }
    }
    const r = requestExtraPlay()
    if (!r.ok) {
      return { kind: 'limit', phrase: FIXED_LIMIT_PHRASE, quota: r.quota }
    }
    return {
      kind: 'replay',
      phrase: `${EXTRA_PLAY_PREFIX}${moment.text}`,
      moment,
      quota: r.quota,
    }
  }

  function playPrelude() {
    try {
      if (typeof player === 'function') player()
    } catch {
      /* 出声失败不影响流程 */
    }
    return new Promise((resolve) => setTimeout(resolve, preludeWaitMs))
  }

  /**
   * 播报一个正反馈事件：前奏音 1 次 → （可选）播报 1 次。
   * 非正反馈事件直接拒绝，保证“无好事不响”。
   */
  async function announcePositive(event, opts = {}) {
    if (!PRELUDE_EVENTS.includes(event)) {
      return { ok: false, reason: 'not_positive', preludePlayed: 0, spoken: 0 }
    }
    await playPrelude()
    let spoken = 0
    if (typeof opts.speak === 'function') {
      try {
        await opts.speak(POSITIVE_FEEDBACK[event])
        spoken = 1
      } catch {
        spoken = 0
      }
    }
    return { ok: true, event, preludePlayed: 1, spoken }
  }

  // 同一事件同一天只播一次（用于天气、孩子语音这类每天一次的“好事”）
  async function beginPositive(event, opts = {}) {
    const s = load()
    if (s.positiveFired && s.positiveFired[event] === s.date) {
      return { ok: false, reason: 'already_today', preludePlayed: 0, spoken: 0 }
    }
    s.positiveFired = { ...(s.positiveFired || {}), [event]: s.date }
    write(s)
    return announcePositive(event, opts)
  }

  function weatherAnsweredToday() {
    return Boolean(load().weatherAnswered)
  }

  function getWeatherAnswer() {
    return load().weatherAnswer || ''
  }

  function markWeatherAnswered(text) {
    const s = load()
    s.weatherAnswered = true
    s.weatherAnswer = String(text || '').slice(0, 60)
    write(s)
  }

  function setPreludePlayer(fn) {
    player = fn
  }

  // ============================================================
  // T-6 定稿：机制5 自然回忆触发 + 量化指标埋点（B-10 验收引用）
  // ============================================================

  // 量化指标埋点（防成瘾效果量化）
  const metrics = {
    reverseAsk: { initiated: 0, responded: 0 },
    blindBox: { played: 0, positive: 0, negativeFeedback: 0 },
    bottle: { collected: 0, replayed: 0 },
    prelude: { played: 0, mismatch: 0 },
    naturalRecall: { triggered: 0, positiveDialogMinutes: [] },
  }

  function recordMetric(mechanism, field, value = 1) {
    if (metrics[mechanism] && field in metrics[mechanism]) {
      if (typeof metrics[mechanism][field] === 'number') {
        metrics[mechanism][field] += value
      } else if (Array.isArray(metrics[mechanism][field])) {
        metrics[mechanism][field].push(value)
      }
    }
  }

  function getMetrics() {
    // 机制1：反向索取 → 回应率（统计前提≥20次发起）
    const ra = metrics.reverseAsk
    // 机制2：盲盒 → 正向占比 + 负面反馈率（统计前提≥50次播放）
    const bb = metrics.blindBox
    // 机制3：收集瓶 → 回放率（初值，P1实测后回填）
    const bt = metrics.bottle
    // 机制4：前奏音 → 零误响（20次播放抽查）
    const pl = metrics.prelude
    // 机制5：自然回忆 → 回忆后正向对话时长中位数（初值，P1实测后回填）
    const nr = metrics.naturalRecall
    const mins = nr.positiveDialogMinutes.slice().sort((a, b) => a - b)
    const median = mins.length > 0
      ? mins[Math.floor(mins.length / 2)].toFixed(1) + 'min' : 'N/A'
    return {
      reverseAsk: {
        responseRate: ra.initiated > 0
          ? (ra.responded / ra.initiated * 100).toFixed(1) + '%' : 'N/A',
        initiated: ra.initiated, responded: ra.responded,
      },
      blindBox: {
        positiveRatio: bb.played > 0
          ? (bb.positive / bb.played * 100).toFixed(1) + '%' : 'N/A',
        negativeFeedbackRate: bb.played > 0
          ? (bb.negativeFeedback / bb.played * 100).toFixed(1) + '%' : 'N/A',
        played: bb.played,
      },
      bottle: {
        replayRate: bt.collected > 0
          ? (bt.replayed / bt.collected * 100).toFixed(1) + '%' : 'N/A',
        collected: bt.collected, replayed: bt.replayed,
      },
      prelude: { mismatch: pl.mismatch, played: pl.played },
      naturalRecall: {
        positiveDialogDurationMedian: median,
        triggered: nr.triggered,
      },
    }
  }

  // 机制5：自然回忆触发（老人提到某个人/物/事 → 触发回忆）
  // 护栏：频率硬上限（一天 maxPerDay 次）/ 无连续（30分钟内不连触发）/ 无悬念
  function triggerNaturalRecall(text, opts = {}) {
    const s = load()
    const nr = T6_PARAMS.naturalRecall
    if (!nr.enabled) return { ok: false, reason: 'disabled' }
    const todayCount = (s.naturalRecallCount && s.naturalRecallCount[s.date]) || 0
    if (todayCount >= nr.maxPerDay) {
      return { ok: false, reason: 'limit_reached', phrase: T6_PARAMS.limitPhrase }
    }
    if (s.lastNaturalRecallTs && (Date.now() - s.lastNaturalRecallTs) < 30 * 60 * 1000) {
      return { ok: false, reason: 'too_soon', phrase: T6_PARAMS.limitPhrase }
    }
    s.naturalRecallCount = { ...(s.naturalRecallCount || {}), [s.date]: todayCount + 1 }
    s.lastNaturalRecallTs = Date.now()
    write(s)
    recordMetric('naturalRecall', 'triggered')
    return { ok: true, phrase: nr.phrase, keyword: opts.keyword || '' }
  }

  // 记录自然回忆后的正向对话时长（分钟，供中位数计算）
  function recordRecallDialog(minutes) {
    recordMetric('naturalRecall', 'positiveDialogMinutes', Number(minutes) || 0)
  }

  return {
    peekBox,
    openBox,
    getQuota,
    requestExtraPlay,
    handleVoiceRequest,
    getBottleSummary,
    getMoments,
    addMoment,
    playPrelude,
    announcePositive,
    beginPositive,
    weatherAnsweredToday,
    getWeatherAnswer,
    markWeatherAnswered,
    setPreludePlayer,
    // T-6 定稿：机制5 自然回忆触发 + 量化指标
    triggerNaturalRecall,
    recordRecallDialog,
    recordMetric,
    getMetrics,
  }
}

// 浏览器里共用一个实例；测试可自行用 createWarmDependency 造独立实例
export const warm = createWarmDependency()
