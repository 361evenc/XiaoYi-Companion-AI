/**
 * T-6 温和依赖参数表（代码即参数）
 * ------------------------------------------------------------
 * 本文件是 F-5 唯一的口径来源：频率上限、前奏音适用范围、
 * 到上限固定话术、禁用句式、以及 UI 上禁止出现的字样。
 * 话术审核、第二人 code review、固定测试脚本都以此为准。
 *
 * 约定：改这里等于改产品承诺，必须重新过 B-6 伦理审查。
 */
export const T6_PARAMS = {
  version: 'T-6 / F-5 v1',

  // 每日硬上限：盲盒一天 1 个；语音“再放一个”一天额外 2 次
  limits: {
    boxPerDay: 1,
    extraReplayPerDay: 2,
  },

  // 前奏音只允许出现在这三类正反馈事件之前，1:1、只响一次
  preludeEvents: ['weather_answered', 'find_object_ok', 'family_voice'],
  prelude: {
    oncePerEvent: true,
    onlyBeforePositive: true,
    durationMs: 1100,
  },

  // 到上限固定话术：① 陈述边界 ② 给替代承接 ③ 不承诺“再来一个”
  limitPhrase:
    '今天就到这儿吧，明天还有——我给您讲讲之前那件有意思的事好不好？',

  // 禁用句式（话术审核以此为准）：管教腔 / 哄骗腔
  forbiddenPhrases: ['不行，今天次数用完了', '您怎么这么倔呀'],

  // UI 上零容忍的字样：再来一次按钮、连续天数徽章、缺席提醒、周统计
  bannedUiStrings: [
    '再来一次',
    '连续天数',
    '连续签到',
    '断签',
    '本周',
    '这周',
    '缺席',
    '好几天没',
    '坚持了',
    '签到',
  ],

  // 有额度时的承接话术前缀（不承诺额外内容，只承接这一下）
  extraPlayPrefix: '好，再给您放一个：',

  // 当天盲盒已开时的说明（只陈述，不提供重开入口）
  boxAlreadyPhrase: '今天这个盲盒已经开过了，明天还有一个小惊喜。',

  // 瓶子里还没有内容时
  bottleEmptyPhrase: '瓶子里还没攒下事，等攒下了，我给您讲。',

  // ============================================================
  // T-6 定稿：五机制防成瘾护栏 + 量化指标（B-10 验收引用）
  // 每机制三条护栏：频率硬上限 / 无连续 / 无悬念
  // ============================================================
  antiAddiction: {
    // 机制1：反向索取
    reverseAsk: {
      maxPerDay: 2,              // 频率硬上限：一天最多 2 次
      noConsecutive: true,       // 无连续：不连发
      noSuspense: true,          // 无悬念：不吊胃口
      metric: { name: 'response_rate', target: '>=50%', minSamples: 20 },
    },
    // 机制2：盲盒
    blindBox: {
      maxPerDay: 1,              // 频率硬上限：一天 1 个
      noConsecutive: true,       // 无连续：不连开
      noSuspense: true,          // 无悬念：无"再来一个"入口
      metric: {
        name: 'positive_ratio', target: '>=90%',
        negativeFeedbackRate: '<=5%', minSamples: 50,
      },
    },
    // 机制3：收集瓶
    bottle: {
      maxPerDay: 3,              // 频率硬上限：一天最多收集 3 件
      noConsecutive: true,       // 无连续：不连讲
      noSuspense: true,          // 无悬念：无断签焦虑
      metric: {
        name: 'replay_rate', target: '>=50%',
        note: '初值，P1实测后回填',
      },
    },
    // 机制4：前奏音
    prelude: {
      maxPerDay: 5,              // 频率硬上限：一天最多 5 次
      noConsecutive: true,       // 无连续：不连响
      oneToOne: true,            // 与正向事件 1:1（零误响）
      metric: { name: 'zero_mismatch', target: '0', note: '20次播放抽查' },
    },
    // 机制5：自然回忆触发
    naturalRecall: {
      maxPerDay: 2,              // 频率硬上限：一天最多 2 次
      noConsecutive: true,       // 无连续：不连触发
      noSuspense: true,          // 无悬念：不吊胃口
      metric: {
        name: 'positive_dialog_duration', target: '>=3min',
        note: '中位数，初值，P1实测后回填',
      },
    },
  },

  // 机制5：自然回忆触发（T-6 新增落地）
  naturalRecall: {
    enabled: true,
    maxPerDay: 2,
    noConsecutive: true,
    // 触发条件：老人提到某个人/物/事（关键词命中）→ 触发回忆
    // 关键词从记忆系统提取的高频人名/物名（运行时注入）
    phrase: '您说这个，我想起之前那件了——',
  },

  // 盲盒心理原理：正向惊喜（随机是形式不是机制）
  // 变率强化=赌博机制，不得写成原理；三个成瘾要件全砍掉：
  // 频率硬上限 / 无连续 / 无"再来一个"入口+无悬念钩子
  boxPsychology: '正向惊喜',
}
