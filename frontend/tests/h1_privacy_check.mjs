#!/usr/bin/env node
/**
 * H-1 视频通话页 · 隐私边界代码校验
 * ------------------------------------------------------------
 * 任务卡验收要求：
 *   "/call 页 …… 通话帧零落盘、零写入图谱/记忆箱（代码验证——隐私边界）"
 *
 * 光靠人说"我没存"不算验证，所以做成脚本：扫描 Call.vue 的源码，
 * 检查它有没有任何能把画面"带出浏览器"的能力。
 *
 * 判据分两类：
 *   【禁止】出现即失败 —— 这些 API 能把帧变成文件或发出去
 *   【必须】缺少即失败 —— 少一个就说明"停摄像头"或"预览"没做到
 *
 * 用法（在仓库根目录）：
 *   node frontend/tests/h1_privacy_check.mjs
 * 退出码 0 = 通过，1 = 不通过。
 */

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const TARGET = resolve(here, '../src/views/Call.vue')

// 能把视频帧变成数据/文件/网络请求的 API —— 一律不许出现在通话页
const FORBIDDEN = [
  ['canvas.toDataURL', /toDataURL\s*\(/],
  ['canvas.toBlob', /toBlob\s*\(/],
  ['canvas.drawImage', /drawImage\s*\(/],
  ['getImageData', /getImageData\s*\(/],
  ['captureStream', /captureStream\s*\(/],
  ['canvas.getContext', /getContext\s*\(\s*['"]2d['"]/],
  ['照片上传接口 /api/photo', /api\.photo\s*\(/],
  ['照片上传接口路径 /photo', /['"`]\/api\/photo/],
  ['FormData 上传', /new\s+FormData\s*\(/],
  ['fetch 直传', /fetch\s*\(/],
]

// 必须存在的实现 —— 少了就说明关键行为没落地
const REQUIRED = [
  ['摄像头挂到 <video>.srcObject', /srcObject\s*=/],
  ['挂断时真正停摄像头 track.stop()', /\.stop\s*\(\s*\)/],
  ['复用既有 ASR 接口', /api\.asr\s*\(/],
  ['复用既有 chat 接口', /api\.chat\s*\(/],
  ['复用既有 TTS 播放', /audio_url/],
]

let src
try {
  src = readFileSync(TARGET, 'utf8')
} catch (e) {
  console.error(`[FAIL] 读不到目标文件：${TARGET}`)
  process.exit(1)
}

// 去掉注释再扫，避免"注释里写了 canvas 也算违规"这种假阳性
const code = src
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/(^|[^:])\/\/.*$/gm, '$1')

let failed = 0

console.log('H-1 隐私边界校验 —— frontend/src/views/Call.vue\n')

console.log('【禁止项】帧不许离开浏览器');
for (const [name, re] of FORBIDDEN) {
  const hit = re.exec(code)
  if (hit) {
    console.log(`  ✗ ${name}  —— 出现了：${hit[0].trim()}`)
    failed++
  } else {
    console.log(`  ✓ ${name}  —— 未出现`)
  }
}

console.log('\n【必须项】关键行为要落地')
for (const [name, re] of REQUIRED) {
  if (re.test(code)) {
    console.log(`  ✓ ${name}`)
  } else {
    console.log(`  ✗ ${name}  —— 缺失`)
    failed++
  }
}

console.log('')
if (failed === 0) {
  console.log('[PASS] 通话页没有任何把画面带出浏览器的 API；摄像头可真正停止。')
  process.exit(0)
} else {
  console.log(`[FAIL] ${failed} 项不通过，隐私边界未满足，不能算 H-1 验收通过。`)
  process.exit(1)
}
