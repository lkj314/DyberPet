# coding:utf-8
"""世界冒险核心数值层回归测试（命令行可跑，文档阶段 1~4 要求）。

跑法：.venv/Scripts/python.exe tools/adventure_test.py
覆盖：秘境解锁校验、成功率公式边界、结果掷骰分布、传讯符节奏、
离线补发、时间回拨防护、受伤减速注入修为系统、历练 buff、历练志上限、存档往返、
事件模板完整性（20+ 骨架全格式化通过）。
"""
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from DyberPet import adventure_service as adv
from DyberPet.adventure_service import (AdventureService, compute_success,
                                        get_service, grant_gaming_buff, is_away)
from DyberPet.cultivation_service import get_core

NOW = 1_000_000.0
TMP = tempfile.mkdtemp(prefix='adv_test_')


def make_spec(tier_idx=0, dur=1800, risk='均衡'):
    from DyberPet.plugins.adventure import realms, events
    spec, err = realms.build_spec(tier_idx, dur, risk, 99)  # self_group 拉满=全解锁
    assert spec is not None, err
    return spec


def fresh_service():
    path = os.path.join(TMP, f'adv_{random.randint(0, 1 << 30)}.json')
    return AdventureService(path)


# ---------- 1) 秘境解锁校验 ----------
svc = fresh_service()
core = get_core()
core.realm_idx = 0            # 炼气期
core.exp = 0
spec, err = __import__('DyberPet.plugins.adventure.realms', fromlist=['build_spec']) \
    .build_spec(3, '中程', '均衡', 0)     # 上古遗迹需元婴
assert spec is None and '境界不足' in err, err
spec = make_spec(0, '短程')
r = svc.dispatch(spec, {'tid': 'wolf', 'cat': 'battle', 'title': '遭遇妖狼',
                        'vars': {'beast': '妖狼', 'loc': '青云山'}}, now=NOW)
assert r['ok'], r
assert svc.status()['state'] == 'away'
r2 = svc.dispatch(make_spec(0, '短程'), {}, now=NOW)   # 重复派出拒绝
assert not r2['ok'] and '正在历练' in r2['msg']
print('LOCK_OK')

# ---------- 2) 成功率公式边界 ----------
base = {'base_success': 0.5, 'req': 0, 'risk_success': 0.0}
assert compute_success(base, 0, 0.0, False) == 0.5
assert compute_success(base, 10, 0.30, False) == 0.95    # 封顶
assert compute_success({**base, 'base_success': 0.0}, 0, 0.0, True) == 0.15  # 保底
assert compute_success(base, 0, 0.0, True) == 0.4        # 带伤 -10%
print('FORMULA_OK')

# ---------- 3) 传讯符节奏 + 归来 ----------
svc2 = fresh_service()
svc2.dispatch(make_spec(0, '中程'), {'tid': 'wolf', 'cat': 'battle',
                                     'title': '遭遇妖狼',
                                     'vars': {'beast': '妖狼', 'loc': '青云山'}},
              now=NOW)
dur = svc2.status()['duration']
ev25 = svc2.tick(NOW + dur * 0.25 + 1)
assert any(e['type'] == 'talisman' and e['idx'] == 0 for e in ev25), ev25
ev50 = svc2.tick(NOW + dur * 0.5 + 2)
assert any(e['type'] == 'talisman' and e['idx'] == 1 for e in ev50)
ev_ret = svc2.tick(NOW + dur + 10)
assert any(e['type'] == 'return' for e in ev_ret), ev_ret
ret = next(e for e in ev_ret if e['type'] == 'return')['result']
assert ret['outcome'] in ('大胜', '小胜', '险胜', '失利', '重伤')
assert ret['exp'] >= 0 and ret['stones'] >= 0
assert svc2.status()['state'] == 'idle'
assert len(svc2.recent_records()) == 1
print(f"RETURN_OK outcome={ret['outcome']} exp={ret['exp']} stones={ret['stones']} pill={ret['pill']}")

# ---------- 4) 结果分布（500 次，权重合理性）----------
from collections import Counter
cnt = Counter()
rng_state = random.getstate()
random.seed(1234)
for _ in range(500):
    s = fresh_service()
    s.dispatch(make_spec(0, '短程'), {}, now=NOW)
    evs = s.tick(NOW + 3600)
    ret = next(e for e in evs if e['type'] == 'return')['result']
    cnt[ret['outcome']] += 1
random.setstate(rng_state)
# 实际 self_group=炼气(0) → p = 0.80：胜段(大/小/险)≈80%，失利≈18%，重伤≈2%
total = sum(cnt.values())
win_total = cnt['大胜'] + cnt['小胜'] + cnt['险胜']
assert abs(win_total / total - 0.80) < 0.06, cnt
assert cnt['重伤'] < 25, cnt
assert cnt['大胜'] < cnt['小胜'] < cnt['大胜'] + cnt['险胜'] + 60, cnt  # 小胜居中
print(f'DISTRIBUTION_OK 500局: ' + ', '.join(f'{k}={v}({v/total:.0%})' for k, v in cnt.most_common()))

# ---------- 5) 离线补发（关机期间历练完成）----------
svc3 = fresh_service()
svc3.dispatch(make_spec(0, '短程'), {'tid': 'mist', 'cat': 'lost', 'title': '迷雾困山',
                                     'vars': {'loc': '青云山'}}, now=NOW)
dur = svc3.status()['duration']
evs = svc3.tick(NOW + dur + 7200)       # 一口气跳过全程
tals = [e for e in evs if e['type'] == 'talisman']
rets = [e for e in evs if e['type'] == 'return']
assert len(tals) == len(svc3.status().get('talisman_texts', []) or tals) or len(tals) >= 1
assert tals and all(e['offline'] for e in tals), tals
assert rets and rets[0]['offline'], rets
print(f'OFFLINE_OK 补发传讯符×{len(tals)} + 归来(offline=True)')

# ---------- 6) 时间回拨防护 ----------
svc4 = fresh_service()
svc4.dispatch(make_spec(0, '中程'), {}, now=NOW)
before = svc4.status()['elapsed']
svc4.tick(NOW - 500)                    # 回拨：必须不推进
assert svc4.status()['elapsed'] == before, '回拨被误推进！'
svc4.tick(NOW + 30)                     # 正常推进不受影响
assert svc4.status()['elapsed'] >= before + 30 - 1e-6
print('REWIND_OK')

# ---------- 7) 受伤减速注入修为系统 ----------
core2 = get_core()
core2.realm_idx = 0
core2.last_tick = NOW
core2.set_rate_modifier('injury', 0.8, 3600, now=NOW)
_, mults, final = core2.get_rate(NOW + 10, idle=0.0)
assert mults.get('伤势') == 0.8, mults
assert abs(final - 1.0 * 0.8) < 1e-9
assert core2.injured(NOW + 100)
assert not core2.injured(NOW + 3700)    # 自动过期恢复
# 永不倒扣：受伤只影响速率
exp_before = core2.exp
core2.tick(NOW + 20, idle=0.0)
assert core2.exp >= exp_before
print('INJURY_OK (速率×0.8，自动恢复，修为绝不倒扣)')

# ---------- 8) 历练 buff（小游戏胜利）----------
svc5 = fresh_service()
grant_gaming_buff()
b = get_service().buff_bonus()
assert b >= 0.10, b
print(f'BUFF_OK gaming buff +{b:.0%}')

# ---------- 9) 历练志上限 ----------
svc6 = fresh_service()
for i in range(25):
    svc6.dispatch(make_spec(0, '短程'), {}, now=NOW)
    svc6.tick(NOW + 7200)
assert len(svc6.recent_records()) == 20, len(svc6.recent_records())
print('RECORDS_OK 上限20条')

# ---------- 10) 存档往返 ----------
core.realm_idx = 16          # 元婴期（group 4）——派高阶秘境需真实境界达标
svc7 = fresh_service()
svc7.dispatch(make_spec(1, '长程'), {'tid': 'hall', 'cat': 'ruin', 'title': '古修洞府',
                                     'vars': {'loc': '昆吾山'}}, now=NOW)
svc7.tick(NOW + 600)
svc7.save()
svc8 = AdventureService(svc7.save_path)
svc8.load()
st = svc8.status()
assert st['state'] == 'away' and st['name'] == '灵山福地', st
assert svc8.records == svc7.records
core.realm_idx = 0           # 还原
print('SAVE_OK 外出状态跨重启恢复')

# ---------- 11) 事件模板完整性（全部可格式化）----------
from DyberPet.plugins.adventure import events, realms
assert len(events.TEMPLATES) >= 20, len(events.TEMPLATES)
for tier_idx in range(6):
    for _ in range(30):
        sk = events.pick(tier_idx)
        assert sk['tid'] and sk['vars'].get('loc')
        for idx in range(3):
            t = events.preset_talisman(sk, idx, 3)
            assert t and '{' not in t.replace('{loc}', ''), (sk['tid'], t)
        for oc in ('大胜', '失利'):
            t = events.preset_return(sk, oc)
            assert t and 'outcome_txt' not in t, (sk['tid'], t)
assert len(realms.REALM_TIERS) == 6
print(f"TEMPLATES_OK {len(events.TEMPLATES)} 个骨架 × 传讯符/归来 全格式化通过")

# ---------- 12) 收益量级抽查 ----------
s = fresh_service()
random.seed(7)
s.dispatch(make_spec(0, '短程'), {}, now=NOW)
ret = next(e for e in s.tick(NOW + 3600) if e['type'] == 'return')['result']
# 凡俗 exp_base=1200 → 小胜 1200 / 大胜 1440 / 险胜 720 / 失利 240
expect = {'大胜': 1440, '小胜': 1200, '险胜': 720, '失利': 240, '重伤': 0}
assert ret['exp'] == expect[ret['outcome']], (ret['outcome'], ret['exp'])
print(f"REWARD_OK {ret['outcome']} → exp={ret['exp']}（与参数表一致）")

print('\nALL ADVENTURE_CORE_TESTS_PASSED')
