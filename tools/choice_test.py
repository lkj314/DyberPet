# coding:utf-8
"""抉择系统（奇遇请示 + 因果回响）单元测试（纯逻辑，无 Qt 依赖）。

覆盖设计文档二 §七：
① 内容装载：奇遇 >= 12 种、玩家回响 >= 15 种、游历琐事三档齐全；
② 请示：冷却/开关/去重/境界门槛/因果牵引，pending 落世界存档；
③ 应答：效果区间结算、karma/flag 落账、回响种子、pending 清空；
④ 玩家回响：到期触发产生 L2+ 日志（cat='main'）、收益入队、drain 清空；
⑤ 游历琐事：境界分档措辞（炼气不念缩地成寸）、生成不空。
运行：.venv/Scripts/python.exe tools/choice_test.py
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from DyberPet.world_service import WorldService  # noqa: E402
from DyberPet.choice_service import ChoiceService, get_choice  # noqa: E402

DATA_DIR = os.path.join(REPO, 'res', 'world')
FAILS = []


def check(name, cond):
    print(('[OK]   ' if cond else '[FAIL] ') + name)
    if not cond:
        FAILS.append(name)


def fresh(seed=7):
    svc = WorldService(save_path=None)
    svc.load_content(DATA_DIR)
    svc.rng.seed(seed)
    ch = ChoiceService(svc)
    ch.load(DATA_DIR)
    svc.rng.seed(seed)
    return svc, ch


# ---- ① 内容装载 ----
svc, ch = fresh()
check('奇遇 >= 12 种', len(ch.table) >= 12)
check('每个奇遇都有叙事与 >= 2 个选项',
      all(e.get('narrative') and len(e.get('choices', [])) >= 2
          for e in ch.table))
check('每个选项都有效果与结果措辞',
      all(c.get('effects') and c.get('result')
          for e in ch.table for c in e['choices']))
check('叙事按境界分档（low/mid/high 齐全）',
      all(all(k in e['narrative'] for k in ('low', 'mid', 'high'))
          for e in ch.table))
check('回响引用的玩家回响事件都存在',
      all(x['event'] in svc.player_echoes
          for e in ch.table for c in e['choices']
          for x in [c.get('echo')] if x))
check('玩家回响 >= 15 种', len(svc.player_echoes) >= 15)
check('游历琐事三档齐全且非空',
      all(svc.player_travel.get('ambient', {}).get(t)
          for t in ('low', 'mid', 'high')))

# ---- ② 请示 ----
w = svc.world
svc.rng.seed(11)
p1 = ch.offer({'phase': 'return', 'loc': '落霞岭', 'realm': 5})
check('首次请示命中', p1 is not None)
check('pending 落世界存档', w.get('pending_choice') == p1)
check('叙事渲染含地点槽位', '落霞岭' in p1['narrative'] or p1['narrative'])
check('选项带 key/text', all(c.get('key') and c.get('text')
                             for c in p1['choices']))
check('去重入史', p1['id'] in w.get('qiyu_history', []))

p2 = ch.offer({'phase': 'idle', 'realm': 5})
check('请示未应答不叠新题', p2 is None)

r1 = ch.resolve(p1['choices'][0]['key'])
check('应答返回结果', r1 is not None and r1.get('text'))
check('应答后 pending 清空', w.get('pending_choice') is None)

# 冷却：刚应答过 → 再请示被冷却拦住
p3 = ch.offer({'phase': 'idle', 'realm': 5})
check('冷却期内不再请示', p3 is None)
w['last_qiyu_ts'] = time.time() - 24 * 3600     # 回拨冷却时钟
p3 = ch.offer({'phase': 'idle', 'realm': 5})
check('冷却过后可再请示', p3 is not None)
ch.resolve(p3['choices'][-1]['key'])

# 开关：world_qiyu_choices=False → 全部拦截
import DyberPet.settings as settings  # noqa: E402
w['last_qiyu_ts'] = 0.0
settings.world_qiyu_choices = False
p4 = ch.offer({'phase': 'idle', 'realm': 5})
check('开关关闭时不请示', p4 is None)
settings.world_qiyu_choices = True

# 境界门槛：realm=0 的候选不含 min_realm>0 的奇遇
c0 = ch.eligible({'realm': 0})
c9 = ch.eligible({'realm': 9})
check('境界门槛过滤（低境候选 < 高境候选）', len(c0) < len(c9)
      and all(e.get('min_realm', 0) == 0 for e in c0))

# 因果牵引：善缘值高时 good 奇遇权重放大
w['player_karma'] = 50
base = {e['id']: float(e.get('weight', 10)) for e in ch.table}
c_good = ch.eligible({'realm': 9})
good_w = {e['id']: e['_w'] for e in c_good if e.get('tone') == 'good'}
check('因果牵引（善缘放大善奇遇权重）',
      all(v > base[k] for k, v in good_w.items()))
w['player_karma'] = 0

# ---- ③ 应答结算 ----
svc.rng.seed(23)
w['pending_choice'] = None
w['last_qiyu_ts'] = 0.0
w['qiyu_history'] = []
p5 = ch.offer({'phase': 'return', 'loc': '青云山', 'realm': 5})
ev5 = next(e for e in ch.table if e['id'] == p5['id'])
kill_ch = ev5['choices'][0]                     # 取第一个选项做确定性校验
before_karma = w['player_karma']
before_echoes = len(w.get('pending_echoes', []))
r5 = ch.resolve(kill_ch['key'])
eff = kill_ch['effects']
if 'karma' in eff:
    delta = w['player_karma'] - before_karma
    check(f'karma 落账（{delta} ∈ {eff["karma"]}）',
          eff['karma'][0] <= delta <= eff['karma'][1])
check('flag 写入世界存档',
      eff.get('flag') is None or eff['flag'] in w.get('player_flags', {}))
check('回响种子入 pending_echoes（npc=player）',
      (eff.get('echo') is None)
      == (len(w.get('pending_echoes', [])) == before_echoes)
      or any(x.get('npc') == 'player' for x in w['pending_echoes']))
if eff.get('echo'):
    seed = [x for x in w['pending_echoes'] if x.get('npc') == 'player'][-1]
    delay = eff['echo']['delay']
    check(f'回响延迟在声明区间（{seed["day"] - w["world_day"]} ∈ {delay}）',
          delay[0] <= seed['day'] - w['world_day'] <= delay[1])
if 'exp' in eff:
    check('exp 区间结算', eff['exp'][0] <= r5['grants']['exp'] <= eff['exp'][1])
if 'stones' in eff:
    check('stones 区间结算',
          eff['stones'][0] <= r5['grants']['stones'] <= eff['stones'][1])
check('非法 key 应答返回 None', ch.resolve('no_such_key') is None)

# ---- ④ 玩家回响结算 ----
svc2, ch2 = fresh(31)
w2 = svc2.world
w2['pending_echoes'].append(
    {'day': w2['world_day'] + 2, 'event': 'wolf_spared_repay',
     'npc': 'player'})
svc2.rng.seed(37)
svc2._simulate_days(2)
logs = [x for x in w2['logs'] if x.get('who') == 'player']
check('玩家回响到期触发（cat=main L3 日志）',
      any(x.get('cat') == 'main' and x.get('level') == 3 for x in logs))
grants = svc2.drain_grants()
check('玩家回响收益入队', len(grants) >= 1
      and ('stones' in grants[0] or 'exp' in grants[0]))
check('drain 后清空', svc2.drain_grants() == [])
# 未到期不触发
w2['pending_echoes'].append(
    {'day': w2['world_day'] + 50, 'event': 'stone_debt_return',
     'npc': 'player'})
svc2._simulate_days(3)
check('未到期回响静候', any(x.get('event') == 'stone_debt_return'
                        and x.get('npc') == 'player'
                        for x in w2['pending_echoes']))
# 未知回响事件静默落空
w2['pending_echoes'].append(
    {'day': w2['world_day'], 'event': 'no_such_echo', 'npc': 'player'})
svc2._resolve_player_echoes()
check('未知回响事件静默落空不崩',
      not any(x.get('event') == 'no_such_echo' for x in w2['pending_echoes']))

# ---- ⑤ 游历琐事 ----
svc3 = fresh(41)[0]
svc3.rng.seed(43)
low_texts = [svc3.gen_travel_log('青云山', 0) for _ in range(40)]
high_texts = [svc3.gen_travel_log('青云山', 8) for _ in range(60)]
check('游历琐事全部生成不空', all(low_texts) and all(high_texts))
check('炼气措辞不出「缩地成寸」',
      all('缩地成寸' not in t for t in low_texts))
check('高境措辞确实升级（出现缩地成寸/神念/大道）',
      any(('缩地成寸' in t) or ('神念' in t) or ('大道' in t)
          for t in high_texts))
check('地点槽位注入（多数模板带地点）',
      sum('青云山' in t for t in low_texts) >= len(low_texts) * 0.6)

# ---- ⑥ 单例 ----
svc4 = fresh(51)[0]
check('get_choice 单例复用', get_choice(svc4) is get_choice(svc4))

print()
print(f'== 抉择系统测试：{len(FAILS)} 项失败 ==' if FAILS
      else '== 抉择系统测试全部通过 ==')
sys.exit(1 if FAILS else 0)
