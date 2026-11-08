import { createRouter, createWebHistory } from 'vue-router'
import Welcome from '../views/Welcome.vue'
import Chat from '../views/Chat.vue'
import Memory from '../views/Memory.vue'
import Timeline from '../views/Timeline.vue'
import History from '../views/History.vue'
import Privacy from '../views/Privacy.vue'
import PrivacyPolicy from '../views/PrivacyPolicy.vue'
import Call from '../views/Call.vue'
// F-5 温和依赖 UI：盲盒 + 收集瓶
import BlindBox from '../views/BlindBox.vue'
import Bottle from '../views/Bottle.vue'
// F-6 识图拍摄引导
import PhotoGuide from '../views/PhotoGuide.vue'
// F-7 子女端：同一 PWA 的「子女角色」，只读概况
import ChildHome from '../views/ChildHome.vue'

const routes = [
  { path: '/', name: 'welcome', component: Welcome },
  { path: '/chat', name: 'chat', component: Chat },
  { path: '/call', name: 'call', component: Call },
  { path: '/memory', name: 'memory', component: Memory },
  // F-4 家庭时间线：人 / 物件 / 时间 / 地方 四维检索 + 翻页
  { path: '/timeline', name: 'timeline', component: Timeline },
  { path: '/history', name: 'history', component: History },
  { path: '/privacy', name: 'privacy', component: Privacy },
  // F-3 隐私政策页（大字版 + 解释库 + 语音朗读）
  { path: '/privacy/policy', name: 'privacyPolicy', component: PrivacyPolicy },
  // F-5 温和依赖 UI（本地契约，B-7/B-8 未落地前用演示素材）
  { path: '/blindbox', name: 'blindbox', component: BlindBox },
  { path: '/bottle', name: 'bottle', component: Bottle },
  // F-6 拍摄引导页：老照片 / 说明书两套大字要点（拍前教、拍中提示）
  { path: '/photo-guide', name: 'photoGuide', component: PhotoGuide },
  // F-7 子女端（同一 PWA 的角色切换，不单独做 APP）
  { path: '/child', name: 'child', component: ChildHome },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})