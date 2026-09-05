# coding:utf-8
"""修仙世界模拟系统单元测试（纯逻辑，无 Qt 依赖）。

覆盖设计文档 §十三 关键指标与 §四/§五 机制：
① 内容装载：事件表/名字池/天下大事数量与字段完整；
② 开天辟地：初始 NPC 数、与玩家旧交数、无重名；
③ 时间推进：catch_up 时间换算、时间回拨防护；
④ 生命周期：寿元坐化、事件致陨落、死亡连锁（亲友哀恸）；
⑤ 关系网：bump/跃迁/年度衰减；
⑥ 回响机制：延迟触发、人已陨落时静默落空；
⑦ 日志质量：连续 100 条重复率 < 5%；
⑧ 存档往返：save/load 后世界日与 NPC 数一致；
⑨ 补算性能：2920 世界日（≈8 小时离线）< 20s。
运行：.venv/Scripts/python.exe tools/world_test.py
"""
import json
import os
import random
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from DyberPet.world_service import (WorldService, day_str, DAYS_PER_YEAR,  # noqa: E402
                                    MAX_CATCHUP_DAYS)
from DyberPet.npc_simulator import NPCSimulator  # noqa: E402

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
    return svc


# ---- ① 内容装载 ----
svc = fresh()
check('事件表 >= 40 种', len(svc.events['by_id']) >= 40)
pools = svc.events['pools']
check('六大日常活动池齐全', all(p in pools and pools[p]
                              for p in ('cultivate', 'travel', 'social',
                                        'life', 'fortune', 'mishap')))
check('回响事件 >= 6 种', any(e.get('echo') for e in svc.events['by_id'].values())
      and sum(1 for e in svc.events['by_id'].values() if e['id'].startswith('echo_')) >= 6)
check('天下大事 >= 8 种', len(svc.world_events_table) >= 8)
check('名字池/地点池非空', svc.ctx_pools.get('surnames')
      and svc.ctx_pools.get('locations'))
check('每个事件都有措辞模板',
      all(e.get('log') for e in svc.events['by_id'].values()
          if not e['id'].startswith('echo_')
          and e['id'] != 'life_seek_longevity'))   # 特例走代码分支，无需模板
check('模板按境界分档（low/mid/high 至少两档）',
      all(len([k for k in ('low', 'mid', 'high') if k in e['log']]) >= 2
          for e in svc.events['by_id'].values()
          if e.get('log') and not e['id'].startswith('echo_')))

# ---- ② 开天辟地 ----
w = svc.world
check(f'初始 NPC = 30（实际 {len(w["npcs"])}）', len(w['npcs']) == 30)
related = [n for n in w['npcs'].values()
           if any(r['target'] == 'player' for r in n['relations'])]
check('与玩家有旧交者 = 5', len(related) == 5)
names = [n['name'] for n in w['npcs'].values()]
check('初始 NPC 无重名', len(names) == len(set(names)))
check('活跃层分配合理（<=50 且含旧交）',
      0 < len(w['active_ids']) <= 50
      and all(n['id'] in w['active_ids'] for n in related))

# ---- ③ 时间推进 ----
svc2 = fresh()
svc2.world['last_tick'] = 1000.0
stats = svc2.catch_up(now=1000.0 + 3600)       # 标准 1 世界年 = 3600s
check('现实 1 小时 ≈ 1 世界年（365 日）',
      300 <= stats['days'] <= 430)
svc2.world['last_tick'] = 2000.0
stats = svc2.catch_up(now=1500.0)              # 时间回拨
check('时间回拨放弃推进（days=0）', stats['days'] == 0
      and svc2.world['last_tick'] == 2000.0)
svc2.world['last_tick'] = 0.0
big = svc2.catch_up(now=10 ** 9)               # 巨额差值
check(f'补算上限截断（{big["days"]} <= {MAX_CATCHUP_DAYS}）',
      big['days'] <= MAX_CATCHUP_DAYS)

# ---- ④ 生命周期 ----
svc3 = fresh(3)
w3 = svc3.world
npc = next(iter(w3['npcs'].values()))
npc['base_lifespan'] = 1                       # 寿元将尽
npc['birth_day'] = -100 * DAYS_PER_YEAR
n_logs_before = len(w3['logs'])
svc3._simulate_days(3)
check('寿元耗尽 -> 坐化入归档', len(w3['dead_index']) >= 1)
dead = list(w3['dead_index'].values())[0]
check('归档含死因与享年', dead['cause'] == '坐化' and dead['age'] >= 100)

# 事件致陨落 + 亲友哀恸连锁（直接结算重伤事件，health 压到 0）
svc4 = fresh(4)
w4 = svc4.world
victim, friend = list(w4['npcs'].values())[:2]
victim['base_lifespan'] = 10 ** 6              # 排除寿元干扰
svc4.sim._bump_relation(friend, victim['id'], 80)
victim['health'] = 0.1
ev = svc4.events['by_id']['combat_ambush']
svc4.sim._run_event(ev, victim, w4, svc4.events, svc4.ctx_pools)
check('重伤致死 -> 陨落归档', victim['id'] in w4['dead_index'])
check('亲友哀恸连锁（bereaved flag）', 'bereaved' in friend['flags'])

# ---- ⑤ 关系网 ----
svc5 = fresh(5)
w5 = svc5.world
a, b = list(w5['npcs'].values())[:2]


def rel_of(npc, target):
    return next((r for r in npc['relations'] if r['target'] == target), None)


svc5.sim._bump_relation(a, b['id'], 20)
svc5.sim._bump_relation(a, b['id'], 20)        # 累计 40
check('关系累计（affinity=40）', rel_of(a, b['id'])['affinity'] == 40)
svc5.sim.yearly_relations(w5)                  # 衰减 0.97
check('年度衰减（40*0.97=38）', rel_of(a, b['id'])['affinity'] == 38)
rel_of(a, b['id'])['affinity'] = 90
svc5.sim.yearly_relations(w5)
check('关系跃迁（>=80 -> sworn）', rel_of(a, b['id'])['type'] == 'sworn')

# ---- ⑥ 回响机制（直接结算，排除日常事件随机干扰）----
svc6 = fresh(6)
w6 = svc6.world
npc6 = next(iter(w6['npcs'].values()))
w6['pending_echoes'] = [{'day': w6['world_day'] + 5,
                         'event': 'echo_wolf_repay', 'npc': npc6['id']}]
svc6.sim.resolve_echoes(w6, svc6.events, svc6.ctx_pools)
check('回响未到期不触发', len(w6['pending_echoes']) == 1)
stones_before = npc6['stones']
w6['pending_echoes'][0]['day'] = w6['world_day']      # 人为到期
out = svc6.sim.resolve_echoes(w6, svc6.events, svc6.ctx_pools)
check('回响到期触发（灵石增加）', len(w6['pending_echoes']) == 0
      and npc6['stones'] > stones_before)
check('回响产出 L3 报恩日志', any(x.get('level') == 3
                              and x.get('who') == npc6['id'] for x in out))
# 人已陨落 -> 静默落空
w6['pending_echoes'].append({'day': w6['world_day'],
                             'event': 'echo_wolf_repay', 'npc': npc6['id']})
svc6.sim._die(npc6, w6, '测试陨落')
svc6.sim.resolve_echoes(w6, svc6.events, svc6.ctx_pools)
check('回响落空不崩（人已陨落）', len(w6['pending_echoes']) == 0
      and npc6['id'] in w6['dead_index'])

# ---- ⑦ 日志质量 ----
svc7 = fresh(11)
svc7._simulate_days(400)
tail = [x['text'] for x in svc7.world['logs'][-100:]]
dup_rate = (len(tail) - len(set(tail))) / max(1, len(tail))
check(f'连续 100 条重复率 {dup_rate * 100:.1f}% < 5%', dup_rate < 0.05)

# ---- ⑧ 存档往返 ----
svc8 = fresh(8)
svc8._simulate_days(30)
tmp = tempfile.NamedTemporaryFile(suffix='.json', delete=False)
tmp.close()
svc8.save_path = tmp.name
svc8.save()
svc9 = WorldService(save_path=tmp.name)
svc9.load_content(DATA_DIR)
check('存档往返：世界日一致',
      svc9.world['world_day'] == svc8.world['world_day'])
check('存档往返：NPC 数一致',
      len(svc9.world['npcs']) == len(svc8.world['npcs']))
check('存档往返：日志保留', len(svc9.world['logs']) == len(svc8.world['logs']))
os.unlink(tmp.name)

# ---- ⑨ 补算性能（≈8 小时离线）----
svc10 = fresh(10)
svc10.world['last_tick'] = 0.0
t0 = time.time()
stats = svc10.catch_up(now=2920.0 * 3600.0 / DAYS_PER_YEAR * 1)  # 折算 2920 日
dt = time.time() - t0
want_days = min(int(2920.0), MAX_CATCHUP_DAYS)
check(f'补算 {stats["days"]} 世界日耗时 {dt:.2f}s < 20s', dt < 20)

# day_str
check('纪年格式（day=0 -> 第1年 1月1日）', day_str(0) == '第1年 1月1日')
check('纪年格式（day=365 -> 第2年 1月1日）', day_str(365) == '第2年 1月1日')

print()
if FAILS:
    print('RESULT: FAIL ->', FAILS)
    sys.exit(1)
print('RESULT: PASS')
