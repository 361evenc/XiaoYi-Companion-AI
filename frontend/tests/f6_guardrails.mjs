/**
 * F-6 识图拍摄引导 —— 5 条固定检查
 * 运行：cd frontend && node tests/f6_guardrails.mjs
 *
 *   1) 两个场景各三条要点，内容不串（老照片 ≠ 说明书）
 *   2) 拍后反馈只有“太暗/太糊”两类，无对象级“歪/摆正”，语气不训斥
 *   3) 引导开关：场景持久化、关掉后相机提示为空、URL ?guide=off 可 A/B
 *   4) 拍照链路复用压缩管线，检测只在引导开时发生，气泡可重拍/可照发
 *   5) 技术边界：后端 PIL+numpy（不引 cv2），不做识别、不产对象级文案
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  createPhotoGuide,
  FEEDBACK_KEYS,
  PHOTO_FEEDBACK,
  SCENES,
  STORAGE_KEY,
} from '../src/data/photoGuide.js'

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
function memStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  }
}
function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
}

// ---------------------------------------------------------------- 1
section(1, '两个场景各三条要点，场景内容不串')
check(SCENES.length === 2, `场景数 = 2（实际 ${SCENES.length}）`)
check(
  SCENES.map((s) => s.id).join(',') === 'old_photo,medicine',
  '场景 id：old_photo / medicine'
)
for (const s of SCENES) {
  check(s.tips.length === 3, `${s.name}：3 条要点`)
  check(s.cameraTips.length === 3, `${s.name}：相机里 3 条短提示`)
  check(
    s.tips.every((t) => ['flat', 'light', 'distance'].includes(t.art)),
    `${s.name}：示意图例齐全（放平/光线/距离）`
  )
}
const oldText = SCENES[0].tips.map((t) => t.text).join('')
const medText = SCENES[1].tips.map((t) => t.text).join('')
check(!oldText.includes('药盒') && medText.includes('药盒'), '老照片场景不混入药品话术')
check(!medText.includes('老照片') && medText.includes('反光'), '说明书场景有避反光要点')

// ---------------------------------------------------------------- 2
section(2, '拍后反馈只有两类，无对象级反馈，语气不训斥')
check(FEEDBACK_KEYS.length === 2 && FEEDBACK_KEYS.join(',') === 'dark,blurry', '只做 太暗/太糊')
const fbText = FEEDBACK_KEYS.map((k) => PHOTO_FEEDBACK[k]).join('｜')
check(/暗/.test(PHOTO_FEEDBACK.dark), '太暗文案提到暗')
check(/糊/.test(PHOTO_FEEDBACK.blurry), '太糊文案提到糊')
check(
  !/歪|摆正|倾斜|斜了/.test(fbText),
  '反馈文案无对象级“歪/摆正/倾斜”'
)
check(
  FEEDBACK_KEYS.every((k) => PHOTO_FEEDBACK[k].includes('再拍一张')),
  '两类反馈都给出可执行的下一步（再拍一张）'
)
check(
  FEEDBACK_KEYS.every((k) => PHOTO_FEEDBACK[k].includes('好不好')),
  '两类反馈都留商量余地（好不好），不训斥'
)
check(!/不行|必须|怎么这么|您怎么/.test(fbText), '不含管教腔/哄骗腔')

// ---------------------------------------------------------------- 3
section(3, '引导开关：场景持久化 + 关掉后不提示 + URL 可 A/B')
const storage = memStorage()
const g = createPhotoGuide({ storage, location: { search: '' } })
check(g.scene() === 'old_photo', '默认场景为老照片')
check(g.cameraTips().length === 3 && g.enabled() === true, '默认引导开、相机提示 3 条')
g.setScene('medicine')
check(g.scene() === 'medicine', '切到说明书场景')
check(JSON.parse(storage.getItem(STORAGE_KEY)).scene === 'medicine', '场景已写入本地存储')
check(g.setScene('不存在') === 'medicine', '无效场景不写入（不会把界面切坏）')
g.setGuideOff(true)
check(g.enabled() === false && g.cameraTips().length === 0, '关掉引导后相机提示为空')
check(g.tips().length === 3, '关掉引导后引导页内容仍可翻看')
g.setGuideOff(false)
check(g.enabled() === true, '重新打开引导')
check(JSON.parse(storage.getItem(STORAGE_KEY)).off === false, '开关状态持久化')
const urlOff = createPhotoGuide({ storage: memStorage(), location: { search: '?guide=off' } })
check(urlOff.enabled() === false && urlOff.cameraTips().length === 0, '?guide=off 可临时关掉引导')
const urlOn = createPhotoGuide({ storage: memStorage(), location: { search: '?camera=1' } })
check(urlOn.enabled() === true, '无关参数不误关引导')

// ---------------------------------------------------------------- 4
section(4, '拍照链路：复用压缩管线，先检测再发送，可重拍可照发')
const chat = fs.readFileSync(path.join(SRC, 'views/Chat.vue'), 'utf8')
const onPhotoSrc = chat.slice(
  chat.indexOf('async function onPhoto'),
  chat.indexOf('async function checkPhotoQuality')
)
check(onPhotoSrc.includes('await compressImage(base64)'), '先走现有 compressImage 压缩')
check(onPhotoSrc.indexOf('photoGuide.enabled()') >= 0, '只有引导开启时才检测（A/B 无引导臂干净）')
check(
  onPhotoSrc.indexOf('checkPhotoQuality') > onPhotoSrc.indexOf('compressImage') &&
    onPhotoSrc.indexOf('checkPhotoQuality') < onPhotoSrc.indexOf('sendPhoto('),
  '顺序：压缩 → 检测 → 发送（检测用的是压缩后的同一份图）'
)
check(
  onPhotoSrc.includes('q.verdict !== \'ok\'') && onPhotoSrc.includes('photoGuide.feedback'),
  '非 ok 才拦下并取固定文案'
)
check(
  chat.includes('📸 重新拍一张') && chat.includes('还是要发'),
  '气泡给“重新拍一张”和“还是要发”两条路（不剥夺选择权）'
)
check(chat.includes('inputRef.value?.openCamera()'), '重拍直接调起相机，不用老人再找按钮')
const inputCard = fs.readFileSync(path.join(SRC, 'components/InputCard.vue'), 'utf8')
check(inputCard.includes('camera-guide') && inputCard.includes('photoGuide.cameraTips()'), '相机覆盖层展示三条要点')
check(inputCard.includes('v-if="cameraTips.length"'), '引导关掉时整块提示不渲染')
check(inputCard.includes('defineExpose({ openCamera })'), '相机可被父组件调起')
const apiSrc = fs.readFileSync(path.join(SRC, 'api/index.js'), 'utf8')
check(apiSrc.includes("'/photo/quality'") && apiSrc.includes('photoQuality'), '前端调用 /api/photo/quality')
const routerSrc = fs.readFileSync(path.join(SRC, 'router/index.js'), 'utf8')
check(routerSrc.includes("'/photo-guide'"), '引导页路由已注册')
const sidebar = fs.readFileSync(path.join(SRC, 'components/SidebarCards.vue'), 'utf8')
check(sidebar.includes("'/photo-guide'"), '侧栏有“拍照小助手”入口')
const guidePage = fs.readFileSync(path.join(SRC, 'views/PhotoGuide.vue'), 'utf8')
check(guidePage.includes('出发') === false && guidePage.includes('去拍照'), '引导页有“去拍照”直达')
check(guidePage.includes('测试开关'), 'A/B 关掉引导的开关在页面上可操作')

// ---------------------------------------------------------------- 5
section(5, '技术边界：PIL+numpy、不识别内容、阈值可解释')
const qualitySrc = fs.readFileSync(path.join(ROOT, 'vision/photo_quality.py'), 'utf8')
const body = stripComments(qualitySrc)
check(!/(^|\s)import cv2|from cv2/.test(body), '不依赖 cv2（不新增 opencv 依赖）')
check(/from PIL import/.test(body) && /import numpy/.test(body), '用已有 PIL + numpy 实现')
check(body.includes('DARK_MEAN') && body.includes('BLUR_VAR'), '两个阈值集中定义，可校准')
check(qualitySrc.includes('verdict') && qualitySrc.includes('thresholds'), '返回机器判定 + 阈值，便于回归')
const apiBackend = fs.readFileSync(path.join(ROOT, 'backend/api/photo_scene_api.py'), 'utf8')
check(apiBackend.includes('@router.post("/quality")'), '后端 /api/photo/quality 已挂载')
check(!/identify|recognize|label_medicine/.test(apiBackend.split('@router.post("/quality")')[1]), '检测接口不调识别（F-6 不抢 H-4 的活）')
const reqSrc = fs.readFileSync(path.join(ROOT, 'requirements.txt'), 'utf8')
check(!/opencv/i.test(reqSrc), 'requirements 未被加 opencv')

// ----------------------------------------------------------------
console.log('')
if (failed) {
  console.log(`FAILED：${failed} 项未通过`)
  process.exit(1)
} else {
  console.log('PASS：F-6 拍摄引导 5 条固定检查全部通过')
  process.exit(0)
}
