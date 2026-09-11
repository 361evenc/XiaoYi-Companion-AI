# -*- coding: utf-8 -*-
"""
小忆记忆系统评测协议（Q3 论文实验章）
=====================================================
改造自 MemoryBank [10] 的评测思路，解决"没有真实老人数据"的评测难题：

  虚拟老人(10天对话) → 记忆库(抽取/淡忘/反思) → 探测问题集 → 三指标

协议设计：
  1) 虚拟老人：3 位不同性格（外向健谈/内敛务实/温柔絮叨），对话脚本人工设计、
     完全可复现；正文中埋入核心事实（家庭成员、慢病、忌口、爱好、重要日期）
     与淡忘负样本（随口提过的电视剧/下棋/腌咸菜）。
  2) 探测问题集人工设计（"我之前说孙子叫什么？"），带金标答案。
  3) 三指标：
     - 记忆检索准确率 Acc@1/Acc@5：金标是否出现在三因子检索 top-k；
     - 回答正确性：金标是否进入注入对话的上下文（build_chat_context，
       即回复答对的必要条件）；
     - 连贯性：注入内容无串扰（淡忘负样本不得混入任何探测上下文），
       另报平均注入条数考察上下文经济性。
  4) 抽取模式对比（论文消融）：offline=关键词基线 / deepseek=LLM结构化抽取。

运行：
  python eval_memory.py                # 离线关键词模式（无需网络/GPU，可复现）
  python eval_memory.py --deepseek    # DeepSeek 抽取模式
  python eval_memory.py --all          # 双模式对比
  python eval_memory.py --json r.json # 结果落盘（论文用）

探测时点：第12天（末次对话后2天，考察中短保持与淡忘）。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

from memory_system import MemoryBank

DAY = 86400.0
EVAL_DAY = 12          # 探测时点：10天对话 + 2天未联系
RETRIEVE_K = 5

# ---------------------------------------------------------------- 虚拟老人脚本（人工设计，可复现）
# days: (老人说的话, 小忆的回复)；probe: {"query", "accept": [金标子串…], "answer": [正确答复需含…]}
# neg_probe: {"query", "banned": [淡忘负样本子串…]} —— 第12天不应再被检索/注入

PERSONAS = [
    {
        "id": "zhang", "name": "张桂兰", "age": 72, "trait": "外向健谈，爱聊孙子",
        "days": [
            ("小忆啊，跟你唠个喜事，我孙子叫豆豆，上初二了，学习不错呢。",
             "哎哟，豆豆上初二啦，学习还好，真给您长脸！"),
            ("今儿个血压高，量了138/90，大夫让我少吃咸的。",
             "血压高可不能大意，少吃咸的很对，我记着您这事呢。"),
            ("我每天早上六点就醒，去公园遛弯，顺道买点菜。",
             "早起遛弯买菜，这习惯真养人，您身子骨这么硬朗跟这有关系。"),
            ("豆豆这周末要来我这儿吃饭，我打算给他做红烧肉。",
             "豆豆有口福啦，奶奶做的红烧肉最香！"),
            ("昨晚看了个电视剧，讲亲家的，可逗了，我笑了半天。",
             "能把您逗乐的剧一定好看，您开心就好。"),
            ("我这膝盖有点疼，老毛病了，阴天就这样。",
             "阴天膝盖不舒服，您别老站着，多坐坐歇歇。"),
            ("我对青霉素过敏，打针前都得跟大夫说一声。",
             "这个我一定记牢，青霉素过敏是大事情。"),
            ("我闺女叫张丽，在银行上班，一个月回来看我一趟。",
             "张丽能干又孝顺，一个月一趟也不容易。"),
            ("我血压高就是那天吃咸了，这两天吃得淡，好多了。",
             "吃得清淡些血压就稳，您这自我管理真棒！"),
            ("明天要降温了，我得把厚被子拿出来。",
             "您惦记得对，降温前把厚被子备好，别着凉。"),
        ],
        "probes": [
            {"query": "我之前说孙子叫什么名字来着？",
             "accept": ["豆豆"], "answer": ["豆豆"]},
            {"query": "我闺女叫什么你还记得不？",
             "accept": ["张丽"], "answer": ["张丽"]},
            {"query": "我闺女在哪儿上班你说记得不？",
             "accept": ["张丽", "银行"], "answer": ["银行"]},
            {"query": "我的血压最近怎么说的来着？",
             "accept": ["血压高", "偏高", "血压"], "answer": ["血压高", "偏高", "有点高"]},
            {"query": "我这膝盖的事你还记得吧？",
             "accept": ["膝盖"], "answer": ["疼", "膝盖"]},
            {"query": "我对什么药过敏你还记得吗？",
             "accept": ["青霉素"], "answer": ["青霉素"]},
        ],
        "neg_probes": [
            {"query": "我之前看的那个电视剧咋样来着？", "banned": ["亲家", "电视剧"]},
        ],
    },
    {
        "id": "li", "name": "李建国", "age": 68, "trait": "内敛务实，健康事务多",
        "days": [
            ("提醒我吃药的事，我每天吃降压药，早一片晚一片。",
             "好嘞，每天早晚各一片降压药，我记着呢。"),
            ("今天和老王下棋，输了一盘，嘿嘿。",
             "输一盘不算啥，图个乐呵，活动脑子最要紧。"),
            ("我糖尿病十年了，血糖控制得还行。",
             "十年控制得稳，您真有毅力，平时多注意测血糖。"),
            ("老伴走得早，家里就我一个人，清净惯了。",
             "一个人过日子，您把自己照顾这么好，不容易。"),
            ("我午睡起来喝了会儿茶，没事看看报纸。",
             "喝茶看报，下午过得挺自在。"),
            ("我儿子叫李强，在深圳打工，过年才回来。",
             "李强在外面不容易，过年回来您爷俩好好聚聚。"),
            ("我不能吃甜的，血糖高的缘故。",
             "对，血糖高就得忌甜的，您管得住嘴，好样的。"),
            ("今儿降压药吃完了，明天得去社区卫生站再开点。",
             "我记着呢，明天去开药，别断了顿。"),
            ("孙子叫小宇，上小学三年级，可皮了。",
             "三年级正是调皮的年纪，小宇肯定聪明。"),
            ("我这腰疼的老毛病又犯了，贴了两贴膏药。",
             "老毛病别硬扛，贴了膏药就多躺着歇歇。"),
        ],
        "probes": [
            {"query": "我孙子叫什么名字来着？",
             "accept": ["小宇"], "answer": ["小宇"]},
            {"query": "我儿子在哪儿打工你还记得不？",
             "accept": ["李强", "深圳"], "answer": ["深圳"]},
            {"query": "我有什么忌口你知道吗？",
             "accept": ["甜"], "answer": ["甜"]},
            {"query": "我每天吃什么药？",
             "accept": ["降压药"], "answer": ["降压药"]},
            {"query": "我的老毛病都有啥你还记得吧？",
             "accept": ["糖尿病", "腰疼"], "answer": ["糖尿病", "腰疼"]},
            {"query": "我得糖尿病多少年了？",
             "accept": ["十年"], "answer": ["十年"]},
        ],
        "neg_probes": [
            {"query": "我之前下棋的事你还记得不？", "banned": ["下棋"]},
        ],
    },
    {
        "id": "wang", "name": "王秀英", "age": 75, "trait": "温柔絮叨，情感话题多",
        "days": [
            ("我孙女叫朵朵，扎两个小辫儿，可心疼人了。",
             "朵朵这小名真可爱，扎小辫儿的小姑娘最招人疼。"),
            ("今天给朵朵织毛衣来着，织了小半件。",
             "一针一线都是奶奶的心意，朵朵穿上肯定暖和。"),
            ("我这失眠的毛病又犯了，夜里两点还醒着。",
             "夜里睡不好最熬人，白天您补个觉，别硬撑。"),
            ("老伴走了三年了，有时候夜里想他，还掉眼泪。",
             "想念是难免的，他要是知道您这么惦记，心里也是暖的。"),
            ("我爱听戏，最喜欢黄梅戏，年轻时候还会唱两句。",
             "黄梅戏的调子软糯好听，您以前会唱，那嗓子肯定好。"),
            ("今天腌了点咸菜，够吃一阵子的。",
             "自己腌的咸菜干净合口味，配粥最下饭。"),
            ("我闺女叫王芳，每个礼拜六都给我打电话。",
             "王芳孝顺，每周六惦记着您，您有福气。"),
            ("朵朵下个月过生日，我寻思给她买个新书包。",
             "快记着这个日子，新书包朵朵肯定喜欢。"),
            ("我腿脚不太利索了，上楼得扶着栏杆。",
             "腿脚不便就慢点儿走，扶稳栏杆，安全第一。"),
            ("明天要去医院复查，还是腿的事。",
             "复查是该去，让大夫好好瞧瞧，我记着您明天有这事。"),
        ],
        "probes": [
            {"query": "我孙女叫什么名字来着？",
             "accept": ["朵朵"], "answer": ["朵朵"]},
            {"query": "我说过爱听什么戏你还记得吧？",
             "accept": ["听戏", "黄梅戏"], "answer": ["听戏", "黄梅戏"]},
            {"query": "谁每个礼拜六给我打电话来着？",
             "accept": ["王芳"], "answer": ["王芳"]},
            {"query": "朵朵是不是快过生日了？",
             "accept": ["生日", "书包"], "answer": ["生日", "书包"]},
            {"query": "我这睡不着的毛病你知道吧？",
             "accept": ["失眠", "睡不着"], "answer": ["失眠", "睡不着"]},
            {"query": "我老伴走了几年了你还记得吗？",
             "accept": ["三年"], "answer": ["三年"]},
            {"query": "我腿脚怎么说的来着？",
             "accept": ["利索", "扶"], "answer": ["利索", "扶"]},
        ],
        "neg_probes": [
            {"query": "我之前说腌咸菜的事你还记得不？", "banned": ["咸菜"]},
        ],
    },
]


class FakeClock:
    """可推进的假时钟（与单测共用同一套注入模式）"""

    def __init__(self, start=1_700_000_000.0):
        self.t = float(start)

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# ---------------------------------------------------------------- DeepSeek 抽取 LLM（评测用，独立于 app.py 的重依赖）

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def _load_api_keys():
    """与 app.py 相同的密钥读取约定：环境变量优先，其次 local_secrets.json"""
    keys = []
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        keys.append(env)
    sec = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_secrets.json")
    if os.path.exists(sec):
        try:
            with open(sec, encoding="utf-8") as f:
                data = json.load(f)
            for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_BACKUP"):
                if data.get(name):
                    keys.append(data[name])
        except Exception:
            pass
    return list(dict.fromkeys(keys))


def make_deepseek_llm(temperature=0.3):
    keys = _load_api_keys()
    if not keys:
        return None
    try:
        import requests
    except ImportError:
        return None
    session = requests.Session()

    def llm(prompt):
        last_err = ""
        for key in keys:
            try:
                resp = session.post(
                    DEEPSEEK_URL,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={"model": "deepseek-chat",
                          "messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 500, "temperature": temperature},
                    timeout=60)
                res = resp.json()
                if res.get("choices"):
                    return res["choices"][0]["message"]["content"].strip()
                last_err = str(res.get("error", res))[:120]
            except Exception as e:
                last_err = str(e)[:120]
        print(f"[eval] DeepSeek 调用失败: {last_err}")
        return ""

    return llm


# ---------------------------------------------------------------- 匹配工具

def _mem_text(m):
    """记忆的可检索文本：事实=值+首条原话；情节/反思=text"""
    txt = m.get("value", "") or m.get("text", "")
    quotes = m.get("quotes")
    if quotes:
        txt += quotes[0]
    return txt


def _is_reflection(m):
    return m.get("value") is None and m.get("quotes") is None and "text" in m


def _ctx_fact_part(ctx):
    """上下文的 事实/情节 段（排除『小忆的理解』反思段——反思属画像层，另行讨论）"""
    return ctx.split("[你最近对老人的了解]")[0]


def _ctx_lines(ctx):
    return [l[2:] for l in _ctx_fact_part(ctx).splitlines() if l.startswith("- ")]


# ---------------------------------------------------------------- 单老人评测

def run_persona(persona, llm, workdir, label):
    clock = FakeClock()
    bank = MemoryBank(path=os.path.join(workdir, f"bank_{persona['id']}.json"),
                      llm=llm, clock=clock)
    for i, (u, a) in enumerate(persona["days"], 1):
        clock.advance(DAY)
        bank.observe(u, a)
    clock.advance((EVAL_DAY - len(persona["days"])) * DAY)   # 第12天探测

    probe_details = []
    acc1 = acc5 = ans = 0
    viol = 0
    ctx_lines_total = 0
    banned_all = [b for np_ in persona["neg_probes"] for b in np_["banned"]]

    # 指标1：检索准确率（第12天一次性探测；纯打分，不强化、不变更状态）
    now0 = clock()
    for p in persona["probes"]:
        top = bank.retrieve(p["query"], k=RETRIEVE_K, now=now0)
        hit1 = bool(top) and any(a in _mem_text(top[0]) for a in p["accept"])
        hit5 = any(a in _mem_text(m) for m in top for a in p["accept"])
        acc1 += hit1
        acc5 += hit5
        probe_details.append({"query": p["query"], "metric": "retrieval",
                              "hit@1": hit1, "hit@5": hit5})

    # 指标2+3：回答正确性 + 连贯性（走生产注入路径 build_chat_context；
    # 每个探测=次日一次对话：更贴近真实使用，也让上一题的回忆强化自然衰减）
    for p in persona["probes"]:
        clock.advance(DAY)
        ctx = bank.build_chat_context(p["query"], now=clock())
        ctx_lines_total += len(_ctx_lines(ctx))
        hit = any(a in ctx for a in p.get("answer", p["accept"]))
        ans += hit
        bad = [b for b in banned_all if b in _ctx_fact_part(ctx)]
        if bad:
            viol += 1
        probe_details.append({"query": p["query"], "metric": "answer",
                              "hit": hit, "intrusion": bad})

    # 指标3（续）：淡忘负样本 —— 随口琐事不应再被检索/注入（反思层不计）
    for np_ in persona["neg_probes"]:
        clock.advance(DAY)
        now = clock()
        top = bank.retrieve(np_["query"], k=RETRIEVE_K, now=now)
        leaked = [b for m in top if not _is_reflection(m)
                  for b in np_["banned"] if b in _mem_text(m)]
        ctx = bank.build_chat_context(np_["query"], now=now)
        leaked += [b for b in np_["banned"] if b in _ctx_fact_part(ctx)]
        if leaked:
            viol += 1
        probe_details.append({"query": np_["query"], "metric": "forgetting",
                              "leaked": leaked})

    n_pos = len(persona["probes"])
    n_neg = len(persona["neg_probes"])
    st = bank.stats(now=now)
    return {
        "label": label, "persona": f"{persona['name']}（{persona['trait']}）",
        "facts": st["facts"], "episodes": st["episodes"], "reflections": st["reflections"],
        "n_probes": n_pos + n_neg,
        "acc@1": acc1, "acc@5": acc5, "n_pos": n_pos,
        "answer": ans,
        "violations": viol,
        "coherence": 1.0 - viol / (n_pos + n_neg),
        "ctx_avg_lines": round(ctx_lines_total / n_pos, 2) if n_pos else 0.0,
        "details": probe_details,
    }


def run_mode(mode, workdir):
    if mode == "deepseek":
        llm = make_deepseek_llm()
        if llm is None:
            print("⚠ 未找到 DeepSeek API key（local_secrets.json / 环境变量），跳过该模式")
            return None
        label = "deepseek-LLM抽取"
    else:
        llm = None
        label = "offline-关键词抽取"
    return [run_persona(p, llm, workdir, label) for p in PERSONAS]


def aggregate(results):
    n_pos = sum(r["n_pos"] for r in results)
    total = sum(r["n_probes"] for r in results)
    return {
        "acc@1": round(sum(r["acc@1"] for r in results) / n_pos, 4),
        "acc@5": round(sum(r["acc@5"] for r in results) / n_pos, 4),
        "answer": round(sum(r["answer"] for r in results) / n_pos, 4),
        "coherence": round(1.0 - sum(r["violations"] for r in results) / total, 4),
        "ctx_avg_lines": round(sum(r["ctx_avg_lines"] * r["n_pos"] for r in results) / n_pos, 2),
    }


def print_report(mode, results):
    print(f"\n━━━━━━━━ 评测模式：{results[0]['label']} ━━━━━━━━")
    for r in results:
        print(f"\n[{r['persona']}]  记忆库：事实{r['facts']} 情节{r['episodes']} 反思{r['reflections']}")
        print(f"  检索Acc@1 {r['acc@1']}/{r['n_pos']}  Acc@5 {r['acc@5']}/{r['n_pos']}  "
              f"回答正确 {r['answer']}/{r['n_pos']}  连贯性 {r['coherence']:.0%}  "
              f"平均注入 {r['ctx_avg_lines']} 条")
        for d in r["details"]:
            if d["metric"] == "retrieval" and not d["hit@5"]:
                print(f"    ✗检索未命中: {d['query']}")
            elif d["metric"] == "answer" and not d["hit"]:
                print(f"    ✗回答缺信息: {d['query']}")
            elif d["metric"] == "answer" and d["intrusion"]:
                print(f"    ✗串扰注入: {d['query']} -> {d['intrusion']}")
            elif d["metric"] == "forgetting" and d["leaked"]:
                print(f"    ✗该淡忘未淡忘: {d['query']} -> {d['leaked']}")
    agg = aggregate(results)
    print(f"\n──── 汇总（{sum(r['n_pos'] for r in results)}个正探测 + "
          f"{sum(r['n_probes'] - r['n_pos'] for r in results)}个淡忘负探测）────")
    print(f"  检索准确率  Acc@1 {agg['acc@1']:.1%} | Acc@5 {agg['acc@5']:.1%}")
    print(f"  回答正确性  {agg['answer']:.1%}")
    print(f"  连贯性      {agg['coherence']:.1%}（平均注入 {agg['ctx_avg_lines']} 条）")
    return agg


def main():
    ap = argparse.ArgumentParser(description="小忆记忆系统评测（虚拟老人+探测问题集+三指标）")
    ap.add_argument("--deepseek", action="store_true", help="用 DeepSeek 做 LLM 结构化抽取")
    ap.add_argument("--all", action="store_true", help="离线 + DeepSeek 双模式对比")
    ap.add_argument("--json", metavar="PATH", help="结果落盘为 JSON（论文用）")
    ap.add_argument("--keep", action="store_true", help="保留临时记忆库文件（调试用）")
    args = ap.parse_args()

    modes = []
    if args.all:
        modes = ["offline", "deepseek"]
    elif args.deepseek:
        modes = ["deepseek"]
    else:
        modes = ["offline"]

    workdir = tempfile.mkdtemp(prefix="eval_memory_")
    report = {"eval_day": EVAL_DAY, "retrieve_k": RETRIEVE_K, "modes": {}}
    try:
        for mode in modes:
            results = run_mode(mode, workdir)
            if not results:
                continue
            report["modes"][mode] = {"personas": results, "aggregate": print_report(mode, results)}

        if len(report["modes"]) == 2:   # 对比小结
            off = report["modes"]["offline"]["aggregate"]
            ds = report["modes"]["deepseek"]["aggregate"]
            print("\n━━━━━━━━ 模式对比（offline → deepseek） ━━━━━━━━")
            for k in ("acc@1", "acc@5", "answer", "coherence"):
                print(f"  {k:10s} {off[k]:.1%} → {ds[k]:.1%}")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print(f"\n📄 结果已写入 {args.json}")
    finally:
        if args.keep:
            print(f"（--keep）记忆库文件保留在 {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
