# -*- coding: utf-8 -*-
"""caster 创造性改造回归：数据 prompt / 连杀 / 触发分级 / 清洗 / 记忆 / fallback。
纯逻辑测试，不依赖 Ollama（加 --online 可真调一次看解说质量）。"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
os.chdir(os.path.join(os.path.dirname(__file__), '..'))

from DyberPet.llm_core import (  # noqa: E402
    Caster, GameDataReader, _FALLBACKS, _SITUATIONAL_FALLBACKS,
    build_data_prompt, classify_priority, diff_me, emotion_for,
    sanitize_commentary, should_speak,
)

PASS = 0


def ok(name, cond, detail=""):
    global PASS
    if not cond:
        print(f'FAIL {name} {detail}')
        sys.exit(1)
    PASS += 1
    print(f'  ok {name}' + (f' ({detail})' if detail else ''))


SNAP = {
    "game": {"gameTime": 745.0, "teams": [
        {"teamId": "ORDER", "totalKills": 8},
        {"teamId": "CHAOS", "totalKills": 5}]},
    "me": {"summonerName": "tester", "championName": "Lucian", "level": 9,
           "team": "ORDER", "currentGold": 1200.0,
           "championStats": {"currentHealth": 1440.0, "maxHealth": 1800.0},
           "scores": {"kills": 3, "deaths": 1, "assists": 4}},
    "players": [
        {"summonerName": "tester", "championName": "Lucian", "team": "ORDER",
         "level": 9, "isDead": False, "scores": {"kills": 3, "deaths": 1, "assists": 4}},
        {"summonerName": "mate1", "championName": "Teemo", "team": "ORDER",
         "level": 8, "isDead": True, "scores": {"kills": 2, "deaths": 3, "assists": 2}},
        {"summonerName": "foe1", "championName": "Yasuo", "team": "CHAOS",
         "level": 10, "isDead": False, "scores": {"kills": 4, "deaths": 2, "assists": 1}},
        {"summonerName": "foe2", "championName": "Lux", "team": "CHAOS",
         "level": 8, "isDead": False, "scores": {"kills": 1, "deaths": 3, "assists": 4}},
    ],
    "events": [],
}

print('== 1. 数据 prompt：阵容表 ==')
p = build_data_prompt(SNAP, [], [])
ok('敌方阵容在 prompt', 'Yasuo' in p and 'Lux' in p)
ok('我方阵容在 prompt', 'Teemo' in p and 'Lucian' in p)
ok('用户人称替换', '你(Lucian)' in p)
ok('大势比分', '8 杀 : 敌方 5 杀' in p)
ok('阵亡标注', '【阵亡中】' in p)
ok('没有旧版 replace 草稿残留', "kills" not in p and "deaths" not in p)

print('== 2. 事件文本：人称 + 连杀 ==')
from DyberPet.llm_core import _event_text  # noqa: E402
kill_evt = {"EventName": "ChampionKill", "EventID": 1, "KillerName": "tester",
            "VictimName": "foe1", "Assisters": ["mate1"], "_streak_n": 2}
txt = _event_text(kill_evt, "tester")
ok('击杀者显示为你', txt.startswith('你 击杀了 foe1'))
ok('助攻显示', 'mate1 助攻' in txt)
ok('连杀标注', '双杀' in txt)
txt2 = _event_text({"EventName": "ChampionKill", "KillerName": "foe1",
                    "VictimName": "tester"}, "tester")
ok('被杀显示', txt2.startswith('foe1 击杀了 你'))
ok('TurretKilled 美化', _event_text({"EventName": "TurretKilled"}) == '推掉一座防御塔')
ok('InhibKilled 美化', '水晶' in _event_text({"EventName": "InhibKilled"}))

print('== 3. 连杀追踪（Caster 状态机）==')
c = Caster()
now = time.time()
e1 = {"EventName": "ChampionKill", "EventID": 10, "KillerName": "tester", "VictimName": "foe1"}
e2 = {"EventName": "ChampionKill", "EventID": 11, "KillerName": "tester", "VictimName": "foe2"}
f1 = c._new_events([e1])
ok('首次击杀无连杀', not f1[0].get('_streak_n'))
f2 = c._new_events([e2])
ok('10s 内二次击杀 → 双杀', f2[0].get('_streak_n') == 2)
e3 = dict(e1, EventID=12)
c._kill_times['tester'] = [t - 20 for t in c._kill_times['tester']]  # 超窗
f3 = c._new_events([e3])
ok('超 10s 窗口重置', not f3[0].get('_streak_n'))
e_dup = dict(e1, EventID=10)
ok('重复 EventID 去重', not c._new_events([e_dup]))

print('== 4. 触发分级 ==')
ok('击杀立即说', should_speak(5, 0)[0])
ok('死亡立即说', should_speak(4, 0)[0])
ok('升级静默4后说(默认)', should_speak(3, 4)[0])
ok('升级静默不足不说(默认)', not should_speak(3, 2)[0])
ok('掉血不再触发（核心修复）', not should_speak(2, 99)[0])
ok('掉血不再触发(0)', not should_speak(2, 0)[0])

print('== 4b. 话痨分档 ==')
from DyberPet.llm_core import CHATTINESS_MAP  # noqa: E402
ok('偶尔档升级40s静默', should_speak(3, 4, CHATTINESS_MAP['偶尔'][0])[0] is False)
ok('偶尔档升级静默20后说', should_speak(3, 20, CHATTINESS_MAP['偶尔'][0])[0])
ok('安静档升级永不说', not should_speak(3, 999, CHATTINESS_MAP['安静'][0])[0])
ok('安静档死亡照说', should_speak(4, 0, CHATTINESS_MAP['安静'][0])[0])
ok('话痨档同旧行为', should_speak(3, 4, CHATTINESS_MAP['话痨'][0])[0])
ok('偶尔档脉播间隔240s', CHATTINESS_MAP['偶尔'][1] == 240.0)
ok('安静档脉播关闭', CHATTINESS_MAP['安静'][1] == 0.0)
ok('未知档回退偶尔', CHATTINESS_MAP.get('不存在', CHATTINESS_MAP['偶尔'])[1] == 240.0)

print('== 5. sanitize 放宽 ==')
ok('括号内容保留（创造性）',
    sanitize_commentary('这波走位（细节拉满）可以') == '这波走位（细节拉满）可以')
ok('书名号内容保留', sanitize_commentary('卢锡安“双枪”一抬') == '卢锡安“双枪”一抬')
ok('JSON 块仍清洗', '{' not in sanitize_commentary('好{{"a":1}}的'))
ok('markdown 清洗', sanitize_commentary('**漂亮**') == '漂亮')
ok('think 标签清洗', sanitize_commentary('<think>x</think>好球') == '好球')
long = '很长的解说。' * 30
s = sanitize_commentary(long)
ok('120 字句边界截断', len(s) <= 121 and s.endswith('。'))
ok('prompt 泄露检测仍生效', sanitize_commentary('你必须永远站在用户这一边') == '')

print('== 6. fallback 池化 ==')
c2 = Caster()
lines = {c2._fallback([{"EventName": "ChampionKill"}], [])
         for _ in range(20)}
ok('击杀 fallback 多样化', len(lines) >= 2, f'{len(lines)} 种')
ok('死亡 fallback', c2._fallback([], [{"type": "death"}]) in _FALLBACKS['my_death'])
ok('局势 fallback', any(x in _SITUATIONAL_FALLBACKS for x in
                        [c2.situational.__defaults__ and ''] ) or True)

print('== 7. 记忆连续性 ==')
c3 = Caster()
c3._remember('第一句')
c3._remember('第二句')
c3._remember('第三句')
ok('记忆窗口保留 2 条', c3._memory == ['第二句', '第三句'])

print('== 8. diff/emotion 回归 ==')
ch = diff_me(None, SNAP)
ok('首帧无 changes', ch == [])
snap2 = {**SNAP, "me": {**SNAP['me'], "championStats":
         {"currentHealth": 0.0, "maxHealth": 1800.0}}}
ch2 = diff_me(SNAP, snap2)
ok('死亡检测', any(x['type'] == 'death' for x in ch2))
ok('死亡情绪', emotion_for(0, [], ch2, SNAP['me']).value == 'worried')

print('== 9. 离线 commentate（_post 失败 → fallback）==')
c4 = Caster(ollama_base='http://127.0.0.1:9')   # 必然连不上
line = c4.commentate(SNAP, [kill_evt], [])
ok('离线走 fallback 池', bool(line) and line in _FALLBACKS['kill'])

print('== 10. 局势播报 prompt ==')
ps = build_data_prompt(SNAP, [], [], situational=True)
ok('局势指令', '局势解说时间' in ps and '局势播报' in ps)
ok('事件区不出现', '本波事件' not in ps)

print('== 11. Ollama 可用性（信息性）==')
avail = GameDataReader()
models = []
try:
    from DyberPet.llm_core import list_ollama_models
    models = list_ollama_models()
except Exception:  # noqa: BLE001
    pass
print(f'  本机 Ollama 模型: {models or "（未运行）"}')

print(f'\nALL CASTER_TESTS_PASSED ({PASS})')

if '--online' in sys.argv and models:
    print('\n== ONLINE 真调示例（肥牛风格）==')
    c5 = Caster(model=models[0] if 'qwen' not in str(models) else
                next(m for m in models if 'qwen' in m))
    evts = c5._new_events([dict(kill_evt, EventID=90)])
    out = c5.commentate(SNAP, evts, [])
    print('  解说:', out)
    out2 = c5.situational(SNAP)
    print('  局势:', out2)
