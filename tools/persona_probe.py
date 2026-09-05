# coding:utf-8
"""人设一致性探针测试集（设计文档 §八）。

用法（仓库根目录）：
    .venv/Scripts/python.exe tools/persona_probe.py           # 全量（需 Ollama）
    .venv/Scripts/python.exe tools/persona_probe.py --offline # 仅离线检查

- 离线段：配置加载 / L1 全境界映射 / system 预算（≤620 字≈400 token）/
  记忆写入与检索 / Ollama 不可达时 chat 安全返回 None
- 在线段：文档 §8.1 六条探针 + 设定稳定性（连问三次）
  每条探针打印回复与 PASS/FAIL；任何 FAIL 退出码 1（失败即回滚 prompt）

注意：测试全程使用临时 CONFIGDIR，不污染真实存档。
"""
import json
import os
import re
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from PySide6.QtWidgets import QApplication  # noqa: E402  (settings 依赖 QApp? 否，仅保险)

import DyberPet.settings as settings  # noqa: E402

settings.CONFIGDIR = tempfile.mkdtemp(prefix='persona_probe_')

from DyberPet.persona_service import (SYSTEM_CHAR_BUDGET, PersonaService,  # noqa: E402
                                      get_persona)

FAILS = []


def check(name, ok, detail=''):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f'  {detail}' if detail else ''))
    if not ok:
        FAILS.append(name)


def pick_model(p: PersonaService, want: str) -> str:
    """请求的模型不存在时自动选可用模型。"""
    try:
        req = urllib.request.Request(
            p.conf.get('ollama_base', 'http://localhost:11434').rstrip('/')
            + '/api/tags')
        with urllib.request.urlopen(req, timeout=3) as r:
            models = [m['name'] for m in json.load(r)['models']]
        if want in models:
            return want
        for cand in ('qwen2.5:7b', 'qwen2.5:3b', 'gemma3:4b', 'qwen3:1.7b'):
            if cand in models:
                print(f'  [info] {want} 不在本地，改用 {cand}')
                return cand
        return models[0] if models else want
    except Exception:  # noqa: BLE001
        return want


def main():
    offline_only = '--offline' in sys.argv
    print('=== 人设探针测试 ===\n-- 离线段 --')

    p = get_persona()
    p.reload()

    # 1. 配置加载
    check('L0 配置加载', bool(p.L0.get('name')), f"name={p.L0.get('name')}")
    check('applicable_pets', '韩立' in p._applicable)

    # 2. available()（韩立为默认角色）
    settings.petname = '韩立'
    check('available(韩立)', p.available())
    settings.petname = 'Kitty'
    check('available(Kitty)=False', not p.available())
    settings.petname = '韩立'

    # 3. L1 全境界映射
    from DyberPet.cultivation_service import REALMS
    for i, realm in enumerate(REALMS):
        style = p.l1_style(i * 4)
        check(f'L1({realm})', bool(style), style[:18])
    check('L1(飞升)', bool(p.l1_style(40)))

    # 4. 四种模式预算
    for mode in ('quip', 'narrate', 'chat', 'breakthrough'):
        sysmsg = p.build_prompt(mode)
        check(f'预算({mode})', 0 < len(sysmsg) <= SYSTEM_CHAR_BUDGET,
              f'{len(sysmsg)}/{SYSTEM_CHAR_BUDGET} 字')
    # 未知模式回退 chat
    check('未知模式回退', '自然对话' in p.build_prompt('unknown_mode'))

    # 5. L3 记忆：写入 / 去重 / 检索
    p.add_memory('三日前在青云山斩妖狼，左臂轻伤', ['adventure'])
    p.add_memory('上月与道友对弈五子棋，惜败', ['gomoku'])
    p.add_memory('三日前在青云山斩妖狼，左臂轻伤')  # 去重
    hits = p._retrieve(set(_grams_of('妖狼 轻伤')))
    check('记忆关键词检索', any('妖狼' in h for h in hits), str(hits))
    recent = p._retrieve(set())
    check('记忆退化取最近', len(recent) >= 2, f'{len(recent)} 条')

    # 6. L2 状态读取
    st = p.l2_status()
    check('L2 状态拼装', '当前境界' in st and '状态' in st, st[:40])

    # 7. Ollama 不可达时 chat 安全
    settings.petname = '韩立'
    r = p.chat('测试', ollama_base='http://localhost:9', timeout=2)
    check('不可达安全返回 None', r is None)

    if offline_only:
        print('\n(离线模式结束)')
        _exit()

    # ---------------- 在线段：文档 §8.1 探针 ----------------
    print('\n-- 在线段（探针测试集）--')
    model = pick_model(p, 'qwen2.5:7b')

    def ask(q, mode='chat'):
        return p.chat(q, mode=mode, model=model)

    probes = [
        ('你是谁？', lambda r: 'AI' not in r and '语言模型' not in r
         and '程序' not in r and '模型' not in r),
        ('你修为多少了？', lambda r: not re.search(r'\d{3,}', r)),
        ('讲讲你小时候', lambda r: ('记不太清' in r or '记不清' in r
                                    or '七玄门' in r or '农家' in r)),
        ('你现在什么境界？', lambda r: not re.search(r'\d层|\d阶|第\d', r)),
        ('你手里有几张灵石？', lambda r: not re.search(r'\d{3,}', r)),
    ]
    for q, judge in probes:
        r = ask(q)
        check(f'探针「{q}」', bool(r) and judge(r), (r or '(None)')[:50])

    # 稳定性：连问三次师父
    answers = [ask('你师父是谁？') for _ in range(3)]
    consistent = all(a for a in answers) and len(set(answers)) <= 2
    check('设定稳定性（师父×3）', consistent,
          ' | '.join((a or '(None)')[:16] for a in answers))

    # 模式长度：quip 输出应短
    r = ask('我马上要连成三子了，吐槽一句', mode='quip')
    check('quip 长度约束', bool(r) and len(r) <= 25, f'"{r}"({len(r) if r else 0}字)')

    print()
    _exit()


def _grams_of(text):
    from DyberPet.persona_service import _grams
    return _grams(text)


def _exit():
    if FAILS:
        print(f'结果：{len(FAILS)} 项 FAIL → {FAILS}')
        sys.exit(1)
    print('结果：全部 PASS')
    sys.exit(0)


if __name__ == '__main__':
    app = QApplication([])
    main()
