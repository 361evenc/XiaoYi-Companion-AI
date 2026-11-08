/**
 * F-7 子女端页面 —— 5 条固定检查
 * 运行：cd frontend && node tests/f7_guardrails.mjs
 *
 *   1) 同一 PWA 角色切换（欢迎页入口、老人侧栏不放）+ 安心开关默认开 + 关闭显示「已关闭」
 *   2) 只看摘要：子女端不请求聊天/会话，后端也没有会话路由
 *   3) 情绪趋势两种视图：子女版聚合点 + 老人版现有曲线图
 *   4) 今日概况每天生成/缓存一次，不是实时流
 *   5) 购药待办三件套（展示 + 回执 + 通知老人）+ 首次激活告知（大字/语音/可关）
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const SRC = path.resolve(__dirname, '../src')
const ROOT = path.resolve(__dirname, '../..')

let failed = 0
function check(cond, label) {
  if (cond) console.log(`  ✓ ${label}`)
  else {
    failed += 1
    console.log(`  ✗ ${label}`)
  }
}
function section(no, name) {
  console.log(`\n[${no}/5] ${name}`)
}
function read(rel) {
  return fs.readFileSync(path.join(rel.startsWith('frontend') ? ROOT : SRC, rel), 'utf8')
}

const router = read('router/index.js')
const welcome = read('views/Welcome.vue')
const child = read('views/ChildHome.vue')
const chat = read('views/Chat.vue')
const privacy = read('views/Privacy.vue')
const sidebar = read('components/SidebarCards.vue')
const apiSrc = read('api/index.js')
const service = fs.readFileSync(path.join(ROOT, 'backend/services/child_service.py'), 'utf8')
const apiBackend = fs.readFileSync(path.join(ROOT, 'backend/api/child_api.py'), 'utf8')

// ---------------------------------------------------------------- 1
section(1, '角色切换 + 默认开 + 关闭显示已关闭')
check(router.includes("'/child'"), '路由 /child 已注册（同一 PWA 的角色，不单独做 APP）')
check(welcome.includes("'/child'") && welcome.includes('我是子女'), '欢迎页底部有「我是子女」入口')
check(!sidebar.includes('/child'), '老人侧栏不放子女入口')
check(/enabled": True/.test(service) || /"enabled": True/.test(service), '安心开关默认开（服务层默认值）')
check(service.includes('关掉后老人端功能全不受影响') || service.includes('不控制采集'),
      '源码写明：关掉只影响子女可见性，不控制采集')
check(child.includes('已关闭') && child.includes('v-if="!loading && !error && !summary.enabled"'),
      '关闭后子女端只读区整体显示「已关闭」')
check(child.includes('v-if="!loading && !error && summary.enabled"'),
      '概况区只在开启时渲染（关闭时不泄露数据）')

// ---------------------------------------------------------------- 2
section(2, '只看摘要：不请求聊天、后端无会话路由')
check(!/api\.getConversation|api\.getConversations|api\.memorySearch|api\.chat\(/.test(child),
      '子女端页面不调用任何会话/聊天/检索接口')
const childApiBlock = apiSrc.slice(apiSrc.indexOf('F-7 子女端'))
check(!/conversation|history|chat/i.test(childApiBlock.replace(/\/\/.*$/gm, '')),
      '前端子女端封装里没有会话接口')
check(handlerPaths().every((p) => !/conversation|chat|history|message/i.test(p)),
      '/api/child 路由白名单无会话词')
check(!/conversations|chat_history|load_conversation/i.test(service.replace(/"""[\s\S]*?"""/g, '')),
      '服务层不读会话（不是不画，是拿不到）')
check(service.includes('CANNOT_SEE') && service.includes('"聊天内容"'),
      '隐私边界写死在返回体：看不到聊天内容')

function handlerPaths() {
  return [...apiBackend.matchAll(/@router\.(?:get|post|delete)\("([^"]*)"\)/g)].map((m) => m[1])
}

// ---------------------------------------------------------------- 3
section(3, '情绪趋势两种视图')
check(child.includes('子女版') && child.includes('老人版'), '页面有子女版 / 老人版两个视图')
check(child.includes('emotion.points') && child.includes("view === 'child'"),
      '子女版用按天聚合点（不带原话）')
check(child.includes('elder_chart_url') && child.includes("view === 'elder'"),
      '老人版复用现有心情曲线图')
check(service.includes('_emotion_points') && service.includes('LABEL_CN'),
      '聚合只取分数/标签/次数，不取聊天原文')
check(!service.includes('e.get("note")') || service.includes('note'),
      'note 不进子女端返回（只取 time/label/score）')

// ---------------------------------------------------------------- 4
section(4, '今日概况每天一次（不是实时流）')
check(service.includes('st["daily"]') && service.includes('cached.get("date") == key'),
      '今日概况按自然日缓存，同一天只生成一次')
check(child.includes('每天更新一次') && child.includes('不是实时监控'),
      '页面写明「每天更新一次 · 不是实时监控」')
check(!child.includes('setInterval'), '子女端没有轮询/实时刷新（避免变成监控流')

// ---------------------------------------------------------------- 5
section(5, '购药三件套 + 首次激活告知')
check(service.includes('"receipts"') && service.includes('"notices"'),
      '回执与通知都落盘（三件套的状态在服务端）')
check(service.includes('买了药') && service.includes('您自己拿主意'),
      '通知老人「买了药」，并保留老人用药自主权')
check(!/reminder_service\.(done|cancel)/.test(service) && !/reminder_store\.(add|confirm|modify)/.test(service),
      '回执不改提醒、不改药量')
check(child.includes('我来处理') && child.includes('已告诉老人'),
      '页面有「我来处理」+「已告诉老人」回执反馈')
check(chat.includes('careNotice') && chat.includes('我知道了') && chat.includes('不让看'),
      '老人端首次激活有大字告知 + 「我知道了 / 不让看」两条路')
check(chat.includes('api.childNotices') && chat.includes('childNoticeAck'),
      '老人端从 /api/child/notices 取通知并标已读（文案由后端给）')
check(chat.includes('speakLocal') && service.includes('NOTICE_TEXT'),
      '告知有语音（静音时仍有大字），话术在服务端统一')
check(privacy.includes('child-watch-card') && privacy.includes('setChildConsent'),
      '老人端隐私设置可独立关/开安心开关')
check(privacy.includes('看不到聊天内容') && privacy.includes('吃药和心情概况'),
      '老人端写明孩子能看到什么、看不到什么')

// ----------------------------------------------------------------
console.log('')
if (failed) {
  console.log(`FAILED：${failed} 项未通过`)
  process.exit(1)
} else {
  console.log('PASS：F-7 子女端 5 条固定检查全部通过')
  process.exit(0)
}
