/**
 * F-8 方言识别 —— 5 条固定检查
 * 运行：cd frontend && node tests/f8_guardrails.mjs
 *
 *   1) 闸门写在后端：Top3 ∩ WER ≤ 基线×0.8 ∩ 引擎参数，交集为空 → hold
 *   2) 听不懂就诚实说：未上线方言不调引擎、不回残文
 *   3) 前端只做体验链路：方言切换 + 拒绝处理，不自己决定上线
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
section(2, '听不懂就诚实说，不硬上')
check(service.includes('我还没学会听') && service.includes('等我学好了'),
      '未上线方言有固定诚实话术')
check(/gate\.get\("refused"\)/.test(voiceSvc) && voiceSvc.includes('return {"text": ""'),
      'ASR 拒绝路径返回空文本，不把残文当老人原话')
check(/dialect_service\.asr_gate\(dialect\)/.test(voiceSvc),
      '方言闸门在 ASR 前执行（后端能力，不是前端自觉）')
check(voiceApi.includes('dialect: str | None') && voiceApi.includes('dialect=req.dialect'),
      '/api/asr 接收方言并由后端判定')
check(root('app.py').includes('def transcribe_audio(audio_input, dialect_hint=None)')
      && root('app.py').includes('if hint.get("cluster")'),
      '引擎参数只来自后端选型（dialect_hint），不在 app.py 猜')

// ---------------------------------------------------------------- 3
section(3, '前端只做体验链路：切换 + 拒绝处理')
check(chat.includes('chooseDialect') && chat.includes('小忆听'),
      '聊天页有方言切换入口')
check(chat.includes('dialect: session.dialect'),
      '录音识别时把当前方言传给后端')
check(chat.includes('d.refused') && chat.includes('d.reply'),
      '后端拒绝时前端大字幕/语音直说，不硬发')
check(chat.includes('if (!opt.supported)') && chat.includes('还在学'),
      '未上线方言只展示「还在学」，不可切过去')
check(session.includes('dialect:') && session.includes('setDialect')
      && session.includes('dialect: this.dialect'),
      '方言选择本地持久化（刷新不丢）')
check(apiSrc.includes('dialectConfig') && apiSrc.includes('setDialectProfile'),
      '前端只调配置/采集接口，不拼上线逻辑')

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
