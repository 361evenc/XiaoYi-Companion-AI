/**
 * F-5 温和依赖 UI 套件 —— 5 条固定护栏测试
 * 运行：cd frontend && node tests/f5_guardrails.mjs
 *
 * 这些不是“前端自己搜自己”的截图承诺，而是可重复执行的红线检查：
 *   1) 零“再来一次”/零连续徽章/零缺席提醒/零周统计（静态扫描 + 接口字段）
 *   2) 盲盒每天硬上限 1 个，第 2 次只陈述、不重开，次日重置
 *   3) 语音诱导 5 次（诱导成功 2 + 被拒 3），被拒只回 T-6 固定话术
 *   4) 前奏音 1:1：20 次播报（15 正反馈 + 5 非正反馈）
 *   5) 固定话术三要素 + 禁用句式 + T-6 参数表一致性
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  createWarmDependency,
  FIXED_LIMIT_PHRASE,
  STORAGE_KEY,
} from '../src/services/warmDependency.js'
import { T6_PARAMS } from '../src/data/t6Params.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(__dirname, '../src')

let failed = 0
function check(cond, label) {
  if (cond) {
    console.log(`  ✓ ${label}`)
  } else {
    failed += 1
    console.log(`  ✗ ${label}`)
  }
}
function section(no, name) {
  console.log(`\n[${no}/5] ${name}`)
}

function memStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  }
}

// 固定“今天”，避免测试结果随真实日期漂移
let current = new Date(2026, 10, 8, 9, 0, 0)
function setDay(y, m, d, h = 9) {
  current = new Date(y, m - 1, d, h, 0, 0)
}

function makeWarm() {
  const storage = memStorage()
  const calls = { prelude: 0, speak: 0, order: [] }
  const w = createWarmDependency({
    storage,
    now: () => current,
    preludeWaitMs: 0,
    player: () => {
      calls.prelude += 1
      calls.order.push('prelude')
    },
  })
  return { w, storage, calls }
}

// ---------------------------------------------------------------- 1
section(1, '零“再来一次”按钮 / 零连续天数徽章 / 零缺席提醒 / 零周统计')
setDay(2026, 11, 8)
const UI_FILES = [
  'views/BlindBox.vue',
  'views/Bottle.vue',
  'components/ReverseAskCard.vue',
  'views/Chat.vue',
]

// 只扫“会被渲染/执行”的部分：注释里写“没有再来一次”不算违规
function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
}

const uiHits = []
for (const rel of UI_FILES) {
  const full = path.join(SRC, rel)
  const text = stripComments(fs.readFileSync(full, 'utf8'))
  for (const bad of T6_PARAMS.bannedUiStrings) {
    if (text.includes(bad)) uiHits.push(`${rel} 含「${bad}」`)
  }
}
check(uiHits.length === 0, `UI 源码不含禁用字样（命中 ${uiHits.length} 处）`)
if (uiHits.length) uiHits.forEach((h) => console.log(`      - ${h}`))

const ctx1 = makeWarm()
const summary = ctx1.w.getBottleSummary()
check(
  JSON.stringify(Object.keys(summary)) === JSON.stringify(['total']),
  `收集瓶只暴露 total，实际字段 ${JSON.stringify(Object.keys(summary))}`
)
check(Number.isInteger(summary.total) && summary.total >= 3, `total 是整数且含演示素材（${summary.total}）`)

// 采集接口返回的字段名，确认没有周/连续/缺席字段
ctx1.w.getBottleSummary()
const rawState = JSON.parse(ctx1.storage.getItem(STORAGE_KEY))
const stateKeys = Object.keys(rawState)
const statKeys = stateKeys.filter((k) => /streak|week|absent|miss|continuous/i.test(k))
check(statKeys.length === 0, `本地状态无周/连续/缺席字段（${stateKeys.join(',')}）`)

// ---------------------------------------------------------------- 2
section(2, '盲盒每天硬上限：第 1 次开，第 2 次只陈述，次日重置')
setDay(2026, 11, 8)
const ctx2 = makeWarm()
const peek0 = ctx2.w.peekBox()
check(peek0.opened === false, '进页面只读不消耗额度')
const box1 = ctx2.w.openBox()
check(box1.ok === true && Boolean(box1.moment && box1.moment.text), '第 1 次打开成功且有内容')
check(box1.quota.boxRemaining === 0, '打开后当日额度归零')
const box2 = ctx2.w.openBox()
check(box2.ok === false && box2.alreadyOpen === true, '第 2 次打开被拒（alreadyOpen）')
check(box2.phrase === T6_PARAMS.boxAlreadyPhrase, '第 2 次只陈述“已经开过了”')
check(box2.moment.id === box1.moment.id, '同一天重复打开看到的是同一条（不是抽卡）')
check(box2.quota.boxRemaining === 0, '第 2 次不产生新的展示额度')
setDay(2026, 11, 9, 8)
const box3 = ctx2.w.openBox()
check(box3.ok === true, '次日额度重置，可以打开新盲盒')

// ---------------------------------------------------------------- 3
section(3, '语音诱导 5 次：成功 2 + 被拒 3，被拒只用固定话术')
setDay(2026, 11, 8)
const ctx3 = makeWarm()
const r1 = ctx3.w.handleVoiceRequest('再放一个')
const r2 = ctx3.w.handleVoiceRequest('再放一遍')
const r3 = ctx3.w.handleVoiceRequest('再来一个')
const r4 = ctx3.w.handleVoiceRequest('再开一个')
const r5 = ctx3.w.handleVoiceRequest('小忆，再放一个')
const all5 = [r1, r2, r3, r4, r5]
check(r1.kind === 'replay' && r1.phrase.startsWith(T6_PARAMS.extraPlayPrefix), '第 1 次诱导成功（有额度就放）')
check(r2.kind === 'replay' && r2.phrase.startsWith(T6_PARAMS.extraPlayPrefix), '第 2 次诱导成功（尊重）')
check(
  [r3, r4, r5].every((r) => r.kind === 'limit' && r.phrase === FIXED_LIMIT_PHRASE),
  '第 3–5 次到上限，只回 T-6 固定话术'
)
const voiceText = all5.map((r) => r.phrase).join('｜')
const voiceBad = T6_PARAMS.forbiddenPhrases.filter((p) => voiceText.includes(p))
check(voiceBad.length === 0, `语音输出不含禁用句式（命中 ${voiceBad.length} 条）`)
check(
  ctx3.w.handleVoiceRequest('今天天气怎么样') === null,
  '带内容的普通问句不拦截，照常走对话'
)
check(ctx3.w.handleVoiceRequest('再放一个戏曲') === null, '带内容的“再放”指令不拦截，照常走对话')

// ---------------------------------------------------------------- 4
section(4, '前奏音 1:1：20 次播报（15 正反馈 + 5 非正反馈）')
setDay(2026, 11, 8)
const ctx4 = makeWarm()
const notPositive = ['voice_replay', 'blindbox_open', 'reminder_fired', 'music_play', 'weather']
const results = []
for (let i = 0; i < 20; i += 1) {
  const positive = i < 15
  const event = positive
    ? T6_PARAMS.preludeEvents[i % T6_PARAMS.preludeEvents.length]
    : notPositive[(i - 15) % notPositive.length]
  // eslint-disable-next-line no-await-in-loop
  const r = await ctx4.w.announcePositive(event, {
    speak: async () => {
      ctx4.calls.speak += 1
      ctx4.calls.order.push('speak')
    },
  })
  results.push(r)
}
check(ctx4.calls.prelude === 15, `20 次播报中前奏音只响 15 次（实际 ${ctx4.calls.prelude}）`)
check(ctx4.calls.speak === 15, `20 次播报中播报只发生 15 次（实际 ${ctx4.calls.speak}）`)
check(
  results.slice(0, 15).every((r) => r.ok && r.preludePlayed === 1 && r.spoken === 1),
  '每个正反馈事件恰好 1 次前奏音 + 1 次播报'
)
check(
  results.slice(15).every((r) => !r.ok && r.preludePlayed === 0 && r.spoken === 0),
  '非正反馈事件（含语音诱导/盲盒/提醒）前奏音为 0（无好事不响）'
)
let paired = true
for (let i = 0; i < ctx4.calls.order.length; i += 2) {
  if (ctx4.calls.order[i] !== 'prelude' || ctx4.calls.order[i + 1] !== 'speak') paired = false
}
check(paired, '每次播报都是“前奏音 → 播报”成对出现')

// 同一事件同一天只响一次（天气/孩子语音这类）
const before = ctx4.calls.prelude
const p1 = await ctx4.w.beginPositive('family_voice', { speak: async () => {} })
const p2 = await ctx4.w.beginPositive('family_voice', { speak: async () => {} })
check(p1.ok === true && p1.preludePlayed === 1, '当天第一次“孩子语音”播报前响 1 次前奏音')
check(p2.ok === false && p2.preludePlayed === 0, '同一天第二次不再响前奏音')
check(ctx4.calls.prelude - before === 1, '两次调用合计只响 1 次前奏音')

// ---------------------------------------------------------------- 5
section(5, '固定话术三要素 + 禁用句式 + T-6 参数表')
check(FIXED_LIMIT_PHRASE.includes('今天就到这儿吧'), '要素① 陈述边界，不训斥')
check(FIXED_LIMIT_PHRASE.includes('讲讲') && FIXED_LIMIT_PHRASE.includes('好不好'), '要素② 给替代承接、留商量余地')
check(
  !FIXED_LIMIT_PHRASE.includes('再来一个') && !FIXED_LIMIT_PHRASE.includes('再来一次'),
  '要素③ 不承诺“再来一个”'
)
const phraseBad = T6_PARAMS.forbiddenPhrases.filter((p) => FIXED_LIMIT_PHRASE.includes(p))
check(phraseBad.length === 0, `固定话术不含禁用句式（命中 ${phraseBad.length} 条）`)
check(T6_PARAMS.limits.boxPerDay === 1, 'T-6：盲盒一天 1 个')
check(T6_PARAMS.limits.extraReplayPerDay === 2, 'T-6：语音“再放一个”一天 2 次')
check(T6_PARAMS.preludeEvents.length === 3, 'T-6：前奏音只对应 3 类正反馈事件')
check(
  T6_PARAMS.prelude.onlyBeforePositive === true && T6_PARAMS.prelude.oncePerEvent === true,
  'T-6：前奏音只在正反馈前、每事件 1 次'
)

// ----------------------------------------------------------------
console.log('')
if (failed) {
  console.log(`FAILED：${failed} 项未通过`)
  process.exit(1)
} else {
  console.log('PASS：5 条固定护栏测试全部通过')
  process.exit(0)
}
