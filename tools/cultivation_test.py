# coding:utf-8
"""修仙核心服务命令行验证（文档阶段 1~4：无 UI 环境完成验证）。"""
import random
import sys
import time

import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import DyberPet.cultivation_service as cs
from DyberPet.cultivation_service import (
    CUM, MAX_STAGE, NEEDS, RATES, ASCEND_CUM, CultivationCore, add_exp,
    fmt_exp, get_core, stage_name)

random.seed(42)

# ---------- 1. 数值表健全性 ----------
assert len(NEEDS) == 40 and len(RATES) == 40 and len(CUM) == 41
assert all(CUM[i] < CUM[i + 1] for i in range(40)), '累计阈值必须单调递增'
assert all(RATES[i] < RATES[i + 1] or (i + 1) % 4 == 0 for i in range(39))
print('曲线: 炼气初→筑基初 =', fmt_exp(CUM[4]), '| 渡劫初 =', fmt_exp(CUM[36]),
      '| 飞升 =', fmt_exp(ASCEND_CUM))

# ---------- 2. 显示格式化 ----------
assert fmt_exp(0) == '0'
assert fmt_exp(999) == '999'
assert fmt_exp(15000) == '1.5万'
assert fmt_exp(2.5e5) == '25万'
assert fmt_exp(4.0e6) == '400万'
assert fmt_exp(1.23e8) == '1.23亿'
assert fmt_exp(1.0e12) == '1兆'
assert fmt_exp(4.5e13) == '45兆'
assert fmt_exp(1.35e16) == '1.35京'
assert 'e' in fmt_exp(1.5e36)  # 超穰 → 科学计数法
print('FMT_OK')

# ---------- 3. 挂机节奏（文档 §4.2 参考耗时） ----------
NOW = 1_000_000.0


def simulate(minutes, rate_mult=1.0, idle=0.0):
    """模拟挂机 minutes 分钟（auto_break 开 + 必中突破，测真实节奏）。"""
    core = CultivationCore()
    core.last_tick = NOW
    orig_random = random.random
    random.random = lambda: 0.1  # 突破必中、顿悟不触发
    try:
        dt = minutes * 60
        seg = dt / 60  # 分 60 段在线结算（避免离线打折干扰节奏测量）
        for k in range(60):
            core.tick(NOW + seg * (k + 1), idle=idle)
    finally:
        random.random = orig_random
    return core


c = simulate(15)   # 炼气期 15 分钟 @1/s + 阶乘加成 → 应突破到筑基
print(f'炼气 15 分钟: {fmt_exp(c.exp)} (阶段 {stage_name(c.stage())})')
assert c.stage() >= 4, '15 分钟应突破到筑基'

c5 = simulate(60)  # 筑基期约 1 小时（c5 从 0 重挂）
print(f'再挂 60 分钟(重置): 阶段 {stage_name(c5.stage())} exp={fmt_exp(c5.exp)}')
assert c5.stage() >= 8, '60 分钟应推进到金丹'

# ---------- 4. 状态联动倍率 ----------
core = CultivationCore()
core.realm_idx = 4  # 筑基初期 5/s
base, mults, final = core.get_rate(NOW, idle=0.0)
assert base == 5.0 and mults == {} and final == 5.0
# 闭关
_, m, f = core.get_rate(NOW, idle=16 * 60)
assert m.get('闭关') == 1.5 and f == 7.5
# 双修
core.set_dual(True)
_, m, f = core.get_rate(NOW, idle=0.0)
assert m.get('双修打坐') == 2.0 and f == 10.0
# 抚慰
core.mark_touch(NOW - 60)
core.set_dual(False)
_, m, f = core.get_rate(NOW, idle=0.0)
assert m.get('抚慰') == 1.2 and abs(f - 6.0) < 1e-9
# 心境低落（2 小时无互动，且已过抚慰窗）
core2 = CultivationCore()
core2.last_touch = NOW - 3 * 3600
_, m, f = core2.get_rate(NOW, idle=0.0)
assert m.get('心境低落') == 0.8 and abs(f - 0.8) < 1e-9
# 虚弱
core3 = CultivationCore()
core3.weak_until = NOW + 100
_, m, f = core3.get_rate(NOW, idle=0.0)
assert m.get('虚弱') == 0.5
print('MULT_OK')

# ---------- 5. 抚摸打断双修 ----------
core = CultivationCore()
core.set_dual(True)
core.mark_touch(NOW)
assert core.dual_on is False, '抚摸必须打断双修'
print('TOUCH_DUAL_OK')

# ---------- 6. 突破：成功 / 失败 / 冷却 / 虚弱 ----------
# 成功路径：炼气圆满(realm 3) + 修为拉满 + 伪随机必中
core = CultivationCore()
core.realm_idx = 3
core.exp = CUM[4]  # 达到炼气圆满→筑基阈值
orig_random = random.random
random.random = lambda: 0.0  # 必中
ev = core.try_breakthrough(now=NOW)
random.random = orig_random
assert ev and ev['type'] == 'breakthrough' and ev['to'] == 4, ev
assert core.realm_idx == 4 and stage_name(4) == '筑基 · 初期'
print('BREAKTHROUGH_OK →', ev['stage'])

# 失败路径：必败 + 损失 + 虚弱 + 冷却
core = CultivationCore()
core.realm_idx = 3
core.exp = CUM[4]
core.last_tick = NOW
core.last_touch = NOW  # 抚慰中
random.random = lambda: 0.999  # 必败
ev = core.try_breakthrough(now=NOW)
random.random = orig_random
assert ev and ev['type'] == 'break_fail', ev
assert abs(core.exp - (CUM[4] - NEEDS[3] * cs.BREAK_FAIL_LOSS)) < 1e-6
assert core.realm_idx == 3, '失败不得升阶'
assert core.weak_until == NOW + cs.WEAK_SECONDS
assert core.break_after == NOW + cs.BREAK_COOLDOWN
# 虚弱期速率减半
_, m, f = core.get_rate(NOW, idle=0.0)
assert m.get('虚弱') == 0.5
# 冷却期内即使修为再达标也不自动突破
core.exp = CUM[4] + 100
evs = core.tick(NOW + 10, idle=0.0)
assert not any(e['type'] == 'breakthrough' for e in evs)
print('BREAK_FAIL_OK')

# ---------- 7. 防作弊：时间回拨 / 离线上限 ----------
core = CultivationCore()
core.exp = 0.0
core.last_tick = NOW
evs = core.tick(NOW - 100, idle=0.0)          # 回拨 → 放弃
assert core.exp == 0.0 and core.last_tick == NOW
# 离线 10 小时 → 只结 8 小时 × 0.7（关自动突破隔离变量）
core2 = CultivationCore()
core2.auto_break = False
core2.last_tick = NOW
core2.tick(NOW + 10 * 3600, idle=0.0)
got = core2.exp
expect = 8 * 3600 * 1.0 * cs.OFFLINE_EFFICIENCY  # 炼气初期 1/s
assert abs(got - expect) < 1e-6, (got, expect)
# 离线攒够修为后，重新启用自动突破应能推进阶位（突破 roll 行为存在）
core2.auto_break = True
random.random = lambda: 0.0
evs = core2.tick(NOW + 10 * 3600 + 5, idle=0.0)
random.random = orig_random
brk = [e for e in evs if e['type'] == 'breakthrough']
assert brk, '修为富余时应触发突破 roll'
# 单次在线 tick（5 秒）不受离线效率影响
core3 = CultivationCore()
core3.last_tick = NOW
core3.tick(NOW + 5, idle=0.0)
assert abs(core3.exp - 5.0) < 1e-9
print('ANTICHEAT_OK')

# ---------- 8. add_exp 大额连破 ----------
core = CultivationCore()
core.last_tick = NOW
core.auto_break = True
random.random = lambda: 0.0  # 必中突破
evs = core.add_exp(CUM[12] + 1, '斗法大胜', now=NOW)  # 直接拉到金丹后期
random.random = orig_random
brk = [e for e in evs if e['type'] == 'breakthrough']
assert len(brk) == 12, f'阶0→阶12 应连破 12 次，got {len(brk)}'
assert core.stage() == 12
print('MULTI_BREAK_OK →', stage_name(core.stage()))

# ---------- 9. 顿悟 ----------
core = CultivationCore()
core.last_tick = NOW
cs.EPIPHANY_P_PER_SEC = 10.0  # 临时调高必触发
evs = core.tick(NOW + 5, idle=0.0)
cs.EPIPHANY_P_PER_SEC = 1.0 / 2700
epi = [e for e in evs if e['type'] == 'epiphany']
assert len(epi) == 1 and epi[0]['amount'] == 1.0 * 600.0, epi  # 炼气 1/s × 600s
print('EPIPHANY_OK')

# ---------- 10. 存档往返 ----------
import tempfile, os
path = os.path.join(tempfile.gettempdir(), 'dyber_cult_test.json')
core = CultivationCore(path)
core.last_tick = NOW
core.add_exp(1234.5, '', now=NOW)
core.save()
core2 = CultivationCore(path)
core2.load()
assert abs(core2.exp - core.exp) < 1e-9
assert core2.dirty is False
os.remove(path)
print('SAVE_OK')

# ---------- 11. 模块级接口 ----------
c = get_core()
assert isinstance(c, CultivationCore)
r = add_exp(10, 'test')
assert r is not None
print('SERVICE_OK')

# ---------- 12. 长跑模拟：全程随机突破，终局应可达飞升 ----------
core = CultivationCore()
core.last_tick = 0.0
t = 0.0
random.random = lambda: 0.5   # 中等运气
while t < 3600 * 24 * 400 and core.stage() <= MAX_STAGE:
    t += 600
    core.auto_break = True
    core.exp = min(core.exp + 600 * RATES[core.stage()] * 3, ASCEND_CUM)  # 模拟 3 倍加速挂机
    core.tick(t, idle=0.0)
random.random = orig_random
print(f'400 天(3x)长跑终局: {stage_name(core.stage())} exp={fmt_exp(core.exp)}')
assert core.exp <= ASCEND_CUM + 1e-9

print('\nALL CULTIVATION_CORE_TESTS_PASSED')
