/**
 * F-5 本地契约的示例素材（B-7 素材库 / B-8 后端未落地前的占位）
 * ------------------------------------------------------------
 * 这些不是真实发生的事，UI 上必须打「演示素材」标签，不得冒充老人的回忆。
 * B-7/B-8 接入后，把 DEMO_MOMENTS / BOX_MOMENTS 换成后端返回即可，
 * 前端其余逻辑不用改。
 */
const img = (prompt, size = 'landscape_4_3') =>
  `https://trae-api-cn.mchost.guru/api/ide/v1/text_to_image?prompt=${encodeURIComponent(prompt)}&image_size=${size}`

export const DEMO_TAG = '演示素材'

// 收集瓶里已有的瞬间（3 条，用于界面可验证）
export const DEMO_MOMENTS = [
  {
    id: 'demo-1',
    at: '2026-11-06T10:20:00+08:00',
    text: '今天您把阳台的花浇了，说“今年开得比去年好”。',
    photo: img(
      'sunlit balcony with red geraniums in ceramic pots, soft morning light, cozy Chinese home, no people, realistic photo'
    ),
  },
  {
    id: 'demo-2',
    at: '2026-11-05T16:40:00+08:00',
    text: '您给我讲了年轻时在厂里学手艺的事，讲得可带劲。',
    photo: img(
      'old black and white photo of a 1970s Chinese factory workshop bench with tools, no people, nostalgic'
    ),
  },
  {
    id: 'demo-3',
    at: '2026-11-03T09:10:00+08:00',
    text: '早上您说睡得踏实，还哼了两句小曲。',
    photo: img(
      'steaming cup of tea on a wooden table beside a window with morning sunlight, cozy, no people, realistic'
    ),
  },
]

// 盲盒内容池：按日期确定性选取（同一天重复打开只会看到同一条，不是抽卡）
export const BOX_MOMENTS = [
  {
    id: 'box-1',
    text: '去年秋天，您和孙子在公园看银杏，您说叶子像小扇子。',
    photo: img(
      'golden ginkgo leaves along a quiet park path, warm autumn afternoon light, no people, realistic photo'
    ),
  },
  {
    id: 'box-2',
    text: '前年腊月，您包的饺子个个像元宝，孩子们抢着吃。',
    photo: img(
      'plate of homemade Chinese dumplings on a wooden table, warm kitchen light, no people, realistic photo'
    ),
  },
  {
    id: 'box-3',
    text: '有一回您在楼下晒被子，邻居夸您晒得蓬松，您高兴了一下午。',
    photo: img(
      'sunlit courtyard with quilt hanging on a clothesline, blue sky, warm light, no people, realistic photo'
    ),
  },
  {
    id: 'box-4',
    text: '去年您把老收音机修好了，调到戏曲台，听了整整一下午。',
    photo: img(
      'vintage Chinese radio on a windowsill beside a teacup, warm afternoon sun, no people, nostalgic photo'
    ),
  },
  {
    id: 'box-5',
    text: '夏天傍晚，您和邻居在院口坐着聊天，说风一吹就凉快了。',
    photo: img(
      'quiet Chinese neighborhood courtyard at summer dusk, wooden stools, warm light, no people, realistic photo'
    ),
  },
]
