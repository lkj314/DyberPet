# coding:utf-8
"""world_daemon 单元测试：守护启动/演出原语/收益兑现/游历琐事/开关。

重点验证「插件 → 主程序」迁移后的行为等价性：
- say/notify/addCoins/add_item 全部直调 PetWidget（fake 记录）
- exp 经 cultivation_service.add_exp（测试中 monkeypatch 防污染存档）
- res/world 内容装载、奇遇开关、游历琐事入流
- 存档/单例全部走 tmp 隔离，绝不触碰真存档
"""
import os
import sys
import tempfile
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

app = QApplication.instance() or QApplication([])

FAILS = []


def check(name, cond):
    print(('[OK]   ' if cond else '[FAIL] ') + name)
    if not cond:
        FAILS.append(name)


class FakePet(QObject):
    """PetWidget 替身：记录演出调用。"""
    addCoins = Signal(int)

    def __init__(self):
        super().__init__()
        self.said = []
        self.notes = []
        self.items = []
        self.coins = []
        self.addCoins.connect(lambda n: self.coins.append(int(n)))

    def show_speech(self, text):
        self.said.append(str(text))

    def register_notification(self, note_type, message):
        self.notes.append((note_type, str(message)))

    def add_item(self, n, names=None):
        self.items.append((int(n), list(names or [])))


fake = FakePet()

# ---- 隔离：世界/抉择单例重置 + tmp 存档（绝不触碰真存档）----
tmpdir = tempfile.mkdtemp(prefix='dyber_world_daemon_')
save_path = os.path.join(tmpdir, 'world_state.json')

import DyberPet.settings as settings  # noqa: E402
import DyberPet.world_service as ws_mod  # noqa: E402
import DyberPet.choice_service as ch_mod  # noqa: E402
ws_mod._WORLD = None
ch_mod._CHOICE = None

from DyberPet.world_daemon import WorldDaemon, start_daemon, get_daemon  # noqa: E402

# ---- ① 守护启动（内容装载 + choice 初始化）----
daemon = WorldDaemon(fake, save_path=save_path)
daemon.start()
check('世界服务就绪（事件表非空）', len(daemon.world.events['by_id']) >= 40)
check('玩家回响表装载', len(daemon.world.player_echoes) >= 15)
check('游历琐事表装载',
      sum(len(v) for v in daemon.world.player_travel
          .get('ambient', {}).values()) >= 10)
check('奇遇库装载', len(daemon.choice.table) >= 10)
check('存档隔离（tmp 路径）', daemon.world.save_path == save_path)

# ---- ② 演出原语（PetWidget 直调）----
daemon.say('测试气泡')
check('say -> show_speech', fake.said == ['测试气泡'])
daemon.notify('测试通知')
check('notify -> register_notification(system)',
      fake.notes and fake.notes[-1][0] == 'system' and fake.notes[-1][1] == '测试通知')
daemon.say('')
daemon.notify('')
check('空文本演出静默', len(fake.said) == 1 and len(fake.notes) == 1)

# ---- ③ 收益兑现（exp monkeypatch 防污染真修为存档）----
called = {}
import DyberPet.cultivation_service as cult  # noqa: E402
_orig_add = cult.add_exp
_orig_core = cult.get_core
cult.add_exp = lambda amount, reason='': called.setdefault(
    'exp', []).append((amount, reason))
daemon.apply_player_grants([{'exp': 66, 'stones': 120}])
check('exp -> cultivation_service.add_exp', called.get('exp') == [(66, '善缘回响')])
check('stones -> addCoins 信号', fake.coins == [120])
check('收益通知', any('修为 +66' in m for _, m in fake.notes))
# 物品 + 受伤（fake core 带齐 stage/set_rate_modifier，防污染后续 realm 读取）
injured = {}
cult.get_core = lambda: types.SimpleNamespace(
    stage=lambda: 0,
    set_rate_modifier=lambda key, a, b: injured.update({key: (a, b)}))
daemon.apply_player_grants([{'item': '回春丹', 'injury': [0.5, 3600]}])
check('item -> add_item', fake.items == [(1, ['回春丹'])])
check('injury -> set_rate_modifier', injured.get('injury') == (0.5, 3600))
cult.add_exp = _orig_add
cult.get_core = _orig_core

# ---- ④ 游历琐事直播（is_away 固定为 True）----
import DyberPet.adventure_service as adv  # noqa: E402
adv.get_service = lambda: types.SimpleNamespace(
    status=lambda: {'name': '落霞岭'})
adv.is_away = lambda: True
settings.world_travel_log = True
before = len(daemon.world.recent_logs(1000, cat='main'))
import random as _random  # noqa: E402
_random.seed(11)                      # 固定种子确保命中概率
for _ in range(50):
    daemon.travel_ambient()
after = len(daemon.world.recent_logs(1000, cat='main'))
check('游历琐事入流（main 线新增）', after > before)
# 开关拦截
settings.world_travel_log = False
before = after
_random.seed(1)
for _ in range(50):
    daemon.travel_ambient()
check('游历直播开关拦截', len(daemon.world.recent_logs(1000, cat='main')) == before)
settings.world_travel_log = True

# ---- ⑤ 留守奇遇开关（概率常量临时拉满，专测开关与请示链路）----
import DyberPet.world_daemon as wd  # noqa: E402
adv.is_away = lambda: False          # 留守状态（④步曾 patch 为在外）
w = daemon.world.world
w['last_qiyu_ts'] = 0.0
w['pending_choice'] = None
settings.world_qiyu_choices = False
wd.IDLE_QIYU_P = 1.0
offered = None
for _ in range(5):
    daemon.idle_qiyu()
    if w.get('pending_choice'):
        offered = w['pending_choice']
        break
check('奇遇开关拦截', offered is None)
settings.world_qiyu_choices = True
for _ in range(5):
    daemon.idle_qiyu()
    if w.get('pending_choice'):
        offered = w['pending_choice']
        break
check('开关打开可请示（请示卡数据就绪）',
      offered is not None and offered.get('choices'))
w['pending_choice'] = None            # 清场
wd.IDLE_QIYU_P = 0.02                 # 恢复常量

# ---- ⑥ 演出克制：单 tick 最多 2 条 ----
notable = daemon.world.drain_notable()
daemon.world._notable.extend(        # 塞 5 条 L3 验证演出上限
    {'day': 0, 'cat': 'friend', 'level': 3, 'text': f'压测{i}', 'who': None}
    for i in range(5))
said_before = len(fake.said)
daemon._on_tick()
check('单 tick 演出 ≤2 条', len(fake.said) - said_before <= 2)

# ---- ⑦ 停止（存档不崩）----
daemon.stop()
check('守护停止后 tick 失效', daemon.tick_timer is None)
check('停止后存档落盘', os.path.isfile(save_path))

if FAILS:
    print('RESULT: FAIL ->', FAILS)
    sys.exit(1)
print('RESULT: PASS')
