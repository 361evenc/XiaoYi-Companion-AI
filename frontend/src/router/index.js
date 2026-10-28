import { createRouter, createWebHistory } from 'vue-router'
import Welcome from '../views/Welcome.vue'
import Chat from '../views/Chat.vue'
import Memory from '../views/Memory.vue'
import History from '../views/History.vue'
import Privacy from '../views/Privacy.vue'

const routes = [
  { path: '/', name: 'welcome', component: Welcome },
  { path: '/chat', name: 'chat', component: Chat },
  { path: '/memory', name: 'memory', component: Memory },
  { path: '/history', name: 'history', component: History },
  { path: '/privacy', name: 'privacy', component: Privacy },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})