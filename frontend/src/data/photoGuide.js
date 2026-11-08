/**
 * F-6 识图拍摄引导 —— 文案与本地状态（前端唯一出处）
 *
 * 两件事刻意集中在这里：
 *   1) 两个场景各三条要点。老人看的是这三条，验收复核（H-6）也只看这三条；
 *      散在多个组件里就改不齐。
 *   2) 拍中反馈只有"太暗 / 太糊"两类。**没有对象级反馈**（"有点歪，摆正一点"
 *      不在 MVP），所以这里也不留任何"歪/摆正/倾斜"的文案位，避免以后被顺手加回来。
 *
 * A/B 对照（同一老人有/无引导各 20 张）需要一个"关掉引导"的本地开关：
 * 关掉后相机内提示、拍后气泡都不出现，但照片照常发送。支持 ?guide=off。
 */

export const STORAGE_KEY = 'xy_photo_guide_v1'

export const DEFAULT_SCENE = 'old_photo'

export const SCENES = [
  {
    id: 'old_photo',
    name: '拍老照片',
    icon: '🖼️',
    sub: '旧相片、老合影',
    tips: [
      {
        art: 'flat',
        title: '把照片放平',
        text: '平放在桌子上，四个角都露出来，别用手拿着。',
      },
      {
        art: 'light',
        title: '光线照得匀',
        text: '把台灯照在照片上，手和身子别挡住光、别留影子。',
      },
      {
        art: 'distance',
        title: '离一拃远',
        text: '手机离照片大约 20 厘米，对准照片正中间。',
      },
    ],
    // 相机里只留三句短的：老人举着手机时读不了长句
    cameraTips: ['放平在桌上', '光线足，别挡影子', '离一拃（约20厘米）'],
  },
  {
    id: 'medicine',
    name: '拍说明书',
    icon: '💊',
    sub: '药盒、说明书小字',
    tips: [
      {
        art: 'flat',
        title: '把盒子放稳',
        text: '药盒平放在桌上，要看的那一面朝上，别斜着拿。',
      },
      {
        art: 'light',
        title: '躲开反光',
        text: '到窗边或灯下拍，慢慢转一下盒子，避开亮亮的一片反光。',
      },
      {
        art: 'distance',
        title: '让字占满屏幕',
        text: '手机离盒子大约 20 厘米，让说明书上的字占满屏幕。',
      },
    ],
    cameraTips: ['盒子放稳、别斜拿', '避开反光', '让字占满屏幕'],
  },
]

// 拍后反馈：只这两类。语气是"我看不清 + 请您再拍一张"，不训斥、不哄骗
export const PHOTO_FEEDBACK = {
  dark: '这张有点暗，我有点看不清。您到亮一点的地方，再拍一张好不好？',
  blurry: '这张有点糊，上面的字我看不清。您把手机拿稳，等对准了再按，再拍一张好不好？',
}

export const FEEDBACK_KEYS = ['dark', 'blurry']

export function sceneById(id) {
  return SCENES.find((s) => s.id === id) || SCENES[0]
}

/**
 * 可注入 storage / location 的工厂：Node 测试里不碰真实 localStorage。
 */
export function createPhotoGuide({ storage, location } = {}) {
  const store =
    storage !== undefined
      ? storage
      : typeof localStorage !== 'undefined'
        ? localStorage
        : null
  const loc =
    location !== undefined
      ? location
      : typeof window !== 'undefined'
        ? window.location
        : null

  function read() {
    let data = null
    try {
      const raw = store ? store.getItem(STORAGE_KEY) : null
      data = raw ? JSON.parse(raw) : null
    } catch (e) {
      data = null
    }
    const scene = data && sceneById(data.scene).id === data.scene ? data.scene : DEFAULT_SCENE
    return { scene, off: Boolean(data && data.off) }
  }

  function write(next) {
    try {
      if (store) store.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch (e) {
      /* 隐私模式/配额满：不挡拍照，内存里这次仍生效 */
    }
  }

  // 测试用 URL 开关：?guide=off 强制关闭（不写本地状态，刷新即恢复）
  function forcedOffByUrl() {
    const search = (loc && loc.search) || ''
    return /(?:^|[?&])guide=off(?:&|$)/.test(search)
  }

  return {
    getState: read,
    scene() {
      return read().scene
    },
    setScene(id) {
      const s = read()
      if (sceneById(id).id !== id) return s.scene
      s.scene = id
      write(s)
      return id
    },
    setGuideOff(off) {
      const s = read()
      s.off = Boolean(off)
      write(s)
      return s.off
    },
    enabled() {
      return !read().off && !forcedOffByUrl()
    },
    sceneInfo() {
      return sceneById(read().scene)
    },
    // 相机覆盖层用：关掉引导时返回空数组，组件据此整块不渲染
    cameraTips() {
      if (!this.enabled()) return []
      return sceneById(read().scene).cameraTips
    },
    // 引导页用：即使引导被关，页面内容也照常展示（老人自己翻看不算"引导"）
    tips() {
      return sceneById(read().scene).tips
    },
    feedback(verdict) {
      return PHOTO_FEEDBACK[verdict] || ''
    },
  }
}

export const photoGuide = createPhotoGuide()
