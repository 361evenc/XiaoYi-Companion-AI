import { createRouter, createWebHistory } from 'vue-router'
import Welcome from '../views/Welcome.vue'
import Chat from '../views/Chat.vue'
import Memory from '../views/Memory.vue'
import History from '../views/History.vue'
import Privacy from '../views/Privacy.vue'
import PrivacyPolicy from '../views/PrivacyPolicy.vue'
import Call from '../views/Call.vue'
// F-5 温和依赖 UI：收集瓶
import Bottle from '../views/Bottle.vue'
// F-5 温和依赖 UI：盲盒惊喜（精选素材、按日期确定性选取，不是抽奖）
import BlindBox from '../views/BlindBox.vue'
// F-6 识图拍摄引导
import PhotoGuide from '../views/PhotoGuide.vue'
// F-7 子女端：同一 PWA 的「子女角色」，只读概况
import ChildHome from '../views/ChildHome.vue'
// T-5 救命链路（大屏 120 + 状态轮询 + 触发按钮）
import Emergency from '../views/Emergency.vue'
// T-4 用药闭环（余量自报 + 购药待办 + 状态，大字适老）
import Medication from '../views/Medication.vue'
// T-3 冷启动编排（Day1-7 脚本，每天学一件事，大字适老）
import ColdStart from '../views/ColdStart.vue'

const routes = [
  { path: '/', name: 'welcome', component: Welcome },
  { path: '/chat', name: 'chat', component: Chat },
  { path: '/call', name: 'call', component: Call },
  { path: '/memory', name: 'memory', component: Memory },
  // F-4 家庭时间线已并进记忆箱（/memory 内的"翻旧事"检索），不再单独路由
  { path: '/history', name: 'history', component: History },
  { path: '/privacy', name: 'privacy', component: Privacy },
  // F-3 隐私政策页（大字版 + 解释库 + 语音朗读）
  { path: '/privacy/policy', name: 'privacyPolicy', component: PrivacyPolicy },
  // F-5 温和依赖 UI（本地契约，B-7/B-8 未落地前用演示素材）
  { path: '/bottle', name: 'bottle', component: Bottle },
  // F-5：盲盒惊喜（精选演示素材，按日期确定性选取，不是抽奖）
  { path: '/blindbox', name: 'blindbox', component: BlindBox },
  // F-6 拍摄引导页：老照片 / 说明书两套大字要点（拍前教、拍中提示）
  { path: '/photo-guide', name: 'photoGuide', component: PhotoGuide },
  // F-7 子女端（同一 PWA 的角色切换，不单独做 APP）
  { path: '/child', name: 'child', component: ChildHome },
  // T-5 救命链路（大屏 120 + 状态轮询 + 触发按钮）
  { path: '/emergency', name: 'emergency', component: Emergency },
  // T-4 用药闭环（余量自报 + 购药待办 + 状态）
  { path: '/medication', name: 'medication', component: Medication },
  // T-3 冷启动编排（Day1-7 脚本，每天学一件事）
  { path: '/cold-start', name: 'coldStart', component: ColdStart },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})