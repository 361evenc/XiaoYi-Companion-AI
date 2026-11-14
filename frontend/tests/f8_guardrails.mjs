/**
 * F-8 方言识别 —— 5 条固定检查
 * 运行：cd frontend && node tests/f8_guardrails.mjs
 *
 *   1) 统计口径保留（诊断用）：Top3 ∩ WER ≤ 基线×0.8 ∩ 引擎参数
 *   2) 本构建不做分方言 ASR：asr_gate 恒放行，一律普通话（覆盖多数口音）
 *   3) 前端不做方言切换：录音直接按普通话识别，不手动切换、不拒绝
 *   4) 三通道统计齐全才出 Top3：对话日志 / H-5 访谈 / 子女端表单
 *   5) 亲属声音复刻默认关、无接口（本卡不做）
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
  return fs.readFileSync(path.join(SRC, rel), 'utf8')
}
function root(rel) {
  return fs.readFileSync(path.join(ROOT, rel), 'utf8')
}

const service = root('backend/services/dialect_service.py')
const voiceSvc = root('backend/services/voice_service.py')
const voiceApi = root('backend/api/voice.py')
const dialectApi = root('backend/api/dialect_api.py')
const report = root('dialect_report.py')
const chat = read('views/Chat.vue')
const child = read('views/ChildHome.vue')
const apiSrc = read('api/index.js')
const session = read('stores/session.js')
const notes = root('数据集调研笔记.md')

// ---------------------------------------------------------------- 1
section(1, '闸门在后端：Top3 ∩ WER ∩ 引擎参数')
check(service.includes('WER_RATIO = 0.8'), '验收口径写死 WER ≤ 基线 × 0.8')
check(/def online_dialects/.test(service) && service.includes('wer_pass(e)')
      && service.includes('has_engine_hint(e)'),
      '上线清单 = Top3 ∩ WER 达标 ∩ 有引擎参数')
check(service.includes('not dist.get("complete")') && service.includes('本功能 hold，不硬上'),
      '统计不齐全 / 交集为空 → hold，不硬上')
check(!/DEFAULT_TOP3|FALLBACK_DIALECT|default_top3/i.test(service),
      '没有默认方言清单/兜底 Top3（不猜）')
check(dialectApi.includes('router = APIRouter(prefix="/api/dialect"')
      && !dialectApi.includes('clone'),
      '/api/dialect 只有配置/采集，没有复刻入口')

// ---------------------------------------------------------------- 2
section(2, '本构建不做分方言 ASR：闸门恒放行，一律普通话')
check(/def asr_gate/.test(service) && service.includes('"refused": False')
      && service.includes('"mode": "mandarin"'),
      'asr_gate 恒放行（refused 恒 False、mode 恒 mandarin）')
check(service.includes('本构建只有普通话模型'),
      '如实说明：本构建只有普通话模型（覆盖多数口音），未含各地方言模型')
check(voiceApi.includes('dialect: str | None') && voiceApi.includes('dialect=req.dialect'),
      '/api/asr 仍接收 dialect 字段（只作记录，不影响识别）')
check(/dialect_service\.asr_gate\(dialect\)/.test(voiceSvc),
      '方言闸门在 ASR 前执行（后端能力，不是前端自觉）')

// ---------------------------------------------------------------- 3
section(3, '前端不做方言切换：录音直接按普通话识别')
check(!chat.includes('chooseDialect') && !chat.includes('loadDialectConfig'),
      '聊天页已无方言切换入口（chooseDialect/loadDialectConfig 已移除）')
check(!chat.includes('dialect: session.dialect') && !chat.includes('d.refused'),
      '录音识别不传方言、无拒绝处理（一律普通话）')
check(chat.includes('api.asr({ audio_base64: audioBase64 })'),
      'ASR 请求不带 dialect 字段')
check(session.includes('setDialect'),
      '方言选择本地持久化仍保留（供统计，不影响识别）')
check(apiSrc.includes('dialectConfig') && apiSrc.includes('setDialectProfile'),
      '配置/采集接口保留（子女端表单仍收方言，只进统计）')

// ---------------------------------------------------------------- 4
section(4, '三通道统计齐全才出 Top3')
check(report.includes('channel_conversations') && report.includes('conversations.json'),
      '通道①：现有客户对话日志')
check(report.includes('channel_interview') && report.includes('dialect_interview'),
      '通道②：H-5 访谈方言记录')
check(report.includes('channel_child_form') && report.includes('老人使用的方言'),
      '通道③：子女端表单「老人使用的方言」')
check(report.includes('方言分布统计.md') && report.includes('dialect_distribution.json'),
      '第 1 周交付物：《方言分布统计》落盘')
check(report.includes('baseline_hyp.txt') && report.includes('dialect_hyp.txt'),
      'WER harness：基线/方言模型识别文件，不填默认数字')
check(child.includes('老人说的方言') && child.includes('setDialectProfile'),
      '子女端首次激活表单有方言字段（只进统计）')
check(notes.includes('四、方言 ASR') && notes.includes('没有现成可用的中文老人方言语音识别数据集'),
      '《数据集调研笔记》已补方言章节（前置有实物）')

// ---------------------------------------------------------------- 5
section(5, '声音复刻默认关、无接口')
check(/VOICE_CLONE\s*=\s*\{[\s\S]*?"enabled": False/.test(service),
      '声音复刻开关默认关')
check(service.includes('显式同意') && service.includes('伦理审查'),
      '前置写死：显式同意 + 伦理审查（红线#10）')
check(!/clone|voice_print|replicate/s.test(dialectApi + voiceApi),
      '没有声音复刻接口（本卡不做，防顺手做）')
check(report.includes('声音复刻') && report.includes('不在本卡'),
      '报告写明复刻不在本卡')

// ----------------------------------------------------------------
console.log('')
if (failed) {
  console.log(`FAILED：${failed} 项未通过`)
  process.exit(1)
} else {
  console.log('PASS：F-8 方言识别 5 条固定检查全部通过')
  process.exit(0)
}
