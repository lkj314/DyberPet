# coding:utf-8
"""斗地主修复专项测试（offscreen）：
① 选中索引一致性——fake 点击 toggle 与绘制判断同源（都走索引）；
② _on_play 从索引取牌值正确出牌；
③ 军师失败必须回调 (None, 原因) 且 UI 解除「军师在想…」（防永久卡死）；
④ 军师 pending 防重入；
⑤ 幻影判定回归——selected 是索引，_refresh_selection_state 必须换算成
   牌值再评估（大王=index 0 曾被当成 3♠，永远「压不过上家」）；
⑥ 军师模型自动降级（配置模型未安装 -> 落到本机已装模型）；
⑦ 本机 Ollama 在跑则做一次真实军师问答（条件执行）。
运行：.venv/Scripts/python.exe tools/doudizhu_test.py
"""
import os
import sys
import time
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

import DyberPet.settings as settings
settings.init()

from DyberPet.plugins.doudizhu.card_rules import card_rank, Counter, detect_move
from DyberPet.plugins.doudizhu.commentary import Commentator
from DyberPet.plugins.doudizhu.ui import CH, CW, OVER, DouDizhuWindow
from DyberPet.plugins.doudizhu.voice import VoiceBank

FAILS = []


def check(name, cond):
    print(('[OK]   ' if cond else '[FAIL] ') + name)
    if not cond:
        FAILS.append(name)


# ---- fake api：settings 走字典，pet 说话/播音频记录调用 ----
class _FakePet:
    def __init__(self):
        self.said = []
        self.spoken = []          # api.pet.speak（实时 TTS）——军师链路必须为空
        self.audio = []           # api.pet.play_audio（预合成 mp3）

    def say(self, text):
        self.said.append(text)

    def speak(self, text):
        self.spoken.append(text)

    def react(self, emotion):
        pass

    def play_audio(self, path):
        self.audio.append(path)


fake_pet = _FakePet()
fake_api = types.SimpleNamespace(
    settings={'advisor': True, 'advisor_tts': True, 'voice': False,
              'pet_taunt': False, 'difficulty': 3, 'card_theme': '经典',
              'llm_model': 'qwen2.5:7b'},
    pet=fake_pet)

# ollama_base 指向必然拒绝连接的端口 -> request_advisor 应立即回调 None
commentator = Commentator(use_llm=True, ollama_base='http://127.0.0.1:9')
voices = VoiceBank(os.path.join(REPO, 'DyberPet', 'plugins', 'doudizhu'))
win = DouDizhuWindow(fake_api, commentator, voices)


def pump(ms):
    """推进事件循环 ms 毫秒（触发跨线程 signal 与 QTimer）。"""
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


# ---- ① 选中索引一致性 ----
hand = win.engine.hands[0]
win.engine.phase = 'playing'
win.engine.turn = 0
win.engine.landlord = 0          # 跳过叫地主流程，直接进入出牌态
win.selected.clear()


def fake_click(x, y):
    class E:
        def __init__(self, px, py):
            self._p = QPoint(px, py)

        def button(self):
            return Qt.LeftButton

        def pos(self):
            return self._p
    win.mousePressEvent(E(x, y))


x0, y = win._hand_origin(len(hand))
cy = y + CH // 2
target = 2                      # 点第 3 张牌
cx = x0 + target * OVER + CW // 2
fake_click(cx, cy)
check('点击一次：selected 收到索引', win.selected == {target})
check('绘制判断与选中同源（i in selected）', target in win.selected)
fake_click(cx, cy)
check('再点一次：取消选中', win.selected == set())

# ---- ② _on_play 从索引取牌值 ----
ranks = Counter(card_rank(c) for c in hand)
pair_rank = next(r for r, k in ranks.items() if k >= 2)
idxs = [i for i, c in enumerate(hand) if card_rank(c) == pair_rank][:2]
before = len(hand)
expected_cards = [hand[i] for i in idxs]     # 快照！出牌原地移除，旧索引随即失效
win.selected = set(idxs)
win._on_play()
played = [m for s, m in win.engine.history if s == 0]
check('选中一对索引 -> 出牌成功', len(played) == 1
      and sorted(card_rank(c) for c in played[0].cards)
      == sorted(card_rank(c) for c in expected_cards))
check('出牌后 selected 清空、手牌减少', win.selected == set()
      and len(win.engine.hands[0]) == before - 2)
pump(50)                        # 让排队的 AI 回合定时器走掉也无妨

# ---- ③ 军师失败回调 (None, 原因) -> UI 解除等待态并明示原因 ----
win2 = DouDizhuWindow(fake_api, commentator, voices)
win2.engine.phase = 'playing'
win2.engine.turn = 0
cb_result = []
win2.commentator.request_advisor('测试简报。', lambda t, e: cb_result.append((t, e)))
pump(3500)                      # Windows 连接拒绝实测 ~2s 抛出；tags 探活失败立即回调
check('request_advisor 失败回调 (None, 原因)（不再静默）',
      len(cb_result) == 1 and cb_result[0][0] is None
      and isinstance(cb_result[0][1], str) and cb_result[0][1])

win2._status = '军师在想…'
win2._advisor_pending = True
win2._on_advisor_ready(None, '连不上 Ollama——请确认 Ollama 正在运行')
check('空回复解除「军师在想…」', win2._status != '军师在想…')
check('空回复把具体原因写进提示', 'Ollama' in win2._status)

# 手动问一次全链路（走到 pending + 超时兜底启动 + 失败回调收尾）
win3 = DouDizhuWindow(fake_api, commentator, voices)
win3.engine.phase = 'playing'
win3.engine.turn = 0
win3._ask_advisor()
check('点击军师进入等待态', win3._status == '军师在想…'
      and win3._advisor_pending and win3._advisor_timer.isActive())
win3._ask_advisor()
pump(200)
check('等待中重复点击不叠问（pending 防重入）',
      win3._advisor_pending and win3._advisor_timer.isActive())
pump(3500)                      # 失败回调到达
check('失败回调后等待态解除', not win3._advisor_pending
      and win3._status != '军师在想…')

# 超时兜底
win4 = DouDizhuWindow(fake_api, commentator, voices)
win4.engine.phase = 'playing'
win4.engine.turn = 0
win4._advisor_pending = True
win4._status = '军师在想…'
win4._advisor_timer.start(200)
pump(600)
check('35s 兜底超时可解除等待态（此处用 200ms 模拟）',
      not win4._advisor_pending and win4._status != '军师在想…')

# ---- ⑤ 幻影判定回归：selected 是索引，必须换算牌值再评估 ----
win5 = DouDizhuWindow(fake_api, commentator, voices)
h5 = win5.engine.hands[0]
win5.engine.phase = 'playing'
win5.engine.turn = 0
win5.engine.landlord = 0
win5.engine.last_move = detect_move([0])     # 上家（AI）出了 3♠ 单张
win5.engine.last_player = 1
# 手牌降序排列，索引 0 = 最大牌。旧 bug：索引 0 被当牌值 0（=3♠，rank 0），
# 0 > 0 不成立 -> 永远「压不过上家」；最大牌 rank 至少 7（数学上必有）
win5.selected = {0}
win5._refresh_selection_state()
check('最大牌评估 rank 正确（索引换算成真实牌值，非幻影 0）',
      win5._sel_move is not None and win5._sel_move.ptype == 'single'
      and win5._sel_move.rank == card_rank(h5[0]) and win5._sel_move.rank >= 7)
check('最大牌可压过 3（出牌按钮解锁）',
      win5._sel_can and win5.playBtn.isEnabled())
win5.selected.clear()
win5._refresh_selection_state()
check('清空选中后出牌按钮禁用', not win5.playBtn.isEnabled())

# ---- ⑥ 军师模型自动降级 ----
c6 = Commentator(use_llm=True, model='qwen2.5:7b',
                 fallback_models=['gemma3:4b'])
check('配置模型已装 -> 原样使用',
      c6._pick_model(['qwen2.5:7b', 'gemma3:4b']) == 'qwen2.5:7b')
check('配置模型未装 -> 降到 fallback（chat_model）',
      c6._pick_model(['gemma3:4b', 'nanbeige4.1:3b']) == 'gemma3:4b')
check('兜底链全 miss -> 挑第一个常规模型',
      c6._pick_model(['nanbeige4.1:3b', 'qwen2.5vl:3b']) == 'nanbeige4.1:3b')
check('只剩特殊用途模型 -> 退回配置模型（404 时明示模型名）',
      c6._pick_model(['nomic-embed-text:latest']) == 'qwen2.5:7b')

# ---- ⑥b 军师预设语音：语音只播预合成 mp3，绝不实时 TTS 念 LLM 文本 ----
win7 = DouDizhuWindow(fake_api, commentator, voices)
win7.engine.phase = 'playing'
win7.engine.turn = 0
win7.engine.landlord = 0
h7 = win7.engine.hands[0]
fake_pet.audio.clear(); fake_pet.spoken.clear(); fake_pet.said.clear()
win7._apply_hint(None)          # 要不起 -> 预合成「要不起」mp3
check('要不起提示播 pass.mp3（预合成）',
      any(p.endswith('pass.mp3') for p in fake_pet.audio))
check('要不起提示文案明示', win7._status == '要不起，点「不要」吧')

mv7 = detect_move([h7[0]])      # 手牌最大单张
fake_pet.audio.clear()
win7._apply_hint(mv7)
from DyberPet.plugins.doudizhu.voice import key_for_move
expect_key = key_for_move(mv7)
check('出牌提示高亮推荐牌（索引集合对应牌值一致）',
      {h7[i] for i in win7.selected} == set(mv7.cards))
check('出牌提示播对应预合成牌型音',
      any(p.endswith(expect_key + '.mp3') for p in fake_pet.audio))
check('提示文案含压制目标或建议前缀',
      win7._status.startswith('建议：'))

# 军师异步回话：LLM 文本只上气泡，speak 不被调用；自动场景补播预合成音
fake_pet.audio.clear(); fake_pet.spoken.clear(); fake_pet.said.clear()
win7._advisor_hint = mv7
win7._advisor_spoken = False
win7._on_advisor_ready('压住他的单张，别让他跑小牌。', '')
check('军师文本上气泡（say）', any('军师' in t for t in fake_pet.said))
check('军师文本绝不走实时 TTS（speak 零调用）', fake_pet.spoken == [])
check('自动场景补播预合成牌型音',
      any(p.endswith(expect_key + '.mp3') for p in fake_pet.audio))

# 手动场景：_apply_hint 已播过语音，军师回话不得重播
fake_pet.audio.clear()
win7._advisor_spoken = True
win7._on_advisor_ready('再说一句。', '')
check('手动场景语音不重播（_apply_hint 已播过）', fake_pet.audio == [])

# 手动军师（LLM 关也照常给本地提示）
fake_off = types.SimpleNamespace(
    settings=dict(fake_api.settings, advisor=False), pet=fake_pet)
win8 = DouDizhuWindow(fake_off, commentator, voices)
win8.engine.phase = 'playing'
win8.engine.turn = 0
win8.engine.landlord = 0
h8 = win8.engine.hands[0]
fake_pet.audio.clear()
win8._ask_advisor()
check('LLM 关闭时手动军师仍给出本地提示（高亮）',
      len(win8.selected) > 0
      and win8._advisor_hint is not None
      and {h8[i] for i in win8.selected} == set(win8._advisor_hint.cards))
check('LLM 关闭时手动军师播预合成音', len(fake_pet.audio) >= 1)
check('LLM 关闭时不进入 Ollama 等待态',
      not win8._advisor_pending and not win8._advisor_timer.isActive())

# ---- ⑦ 本机 Ollama 在跑则做一次真实军师问答（条件执行）----
from DyberPet.llm_core import DEFAULT_OLLAMA_BASE, list_ollama_models as _lom
installed = _lom(DEFAULT_OLLAMA_BASE)
if installed:
    # 故意配置一个不存在的模型名：应自动降级到已装模型并成功出话
    c_live = Commentator(use_llm=True, model='no-such-model:xx',
                         fallback_models=installed,
                         ollama_base=DEFAULT_OLLAMA_BASE)
    got = []
    c_live.request_advisor('你是农民，手里还有15张，地主剩9张。',
                           lambda t, e: got.append((t, e)))
    deadline = time.time() + 60
    while time.time() < deadline and not got:
        app.processEvents()
        time.sleep(0.05)
    check('真实军师问答：模型名不存在也自动降级并给出提示',
          bool(got) and isinstance(got[0][0], str) and got[0][0])
    if got:
        print('   军师实际回复：', got[0])
else:
    print('[SKIP] 本机 Ollama 未运行，跳过真实军师问答')

print()
if FAILS:
    print('RESULT: FAIL ->', FAILS)
    sys.exit(1)
print('RESULT: PASS')
