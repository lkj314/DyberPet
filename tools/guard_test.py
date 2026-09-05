# coding:utf-8
"""留守模式专项测试（offscreen）：真实实例化 SubPet(韩立元婴) 验证 guard 行为。

跑法：cd U:/DyberPet && .venv/Scripts/python.exe _test_guard.py
"""
import os
import sys
import types
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv[:1])

import DyberPet.settings as settings
settings.init()
# offscreen 环境：补齐运行时全局（current_screen 存 QScreen 对象）
settings.current_screen = app.primaryScreen()
geo = app.primaryScreen().availableGeometry()
settings.screens = [app.primaryScreen()]
settings.on_top_hint = True
settings.minipet_scale = defaultdict(dict)
settings.pets = []                                   # _set_menu 用
settings.pet_data = types.SimpleNamespace(allData_params={}, fv_lvl=99)
from PySide6.QtGui import QPixmap
settings.current_img = QPixmap('res/pet/韩立元婴/action/daoying_0.png')  # 主宠当前帧（跟随定位用）
settings.current_anchor = [0, 0]

from DyberPet.Accessory import SubPet, SUBPET_MANAGER

FAILS = []
def check(name, cond):
    print(('[OK]   ' if cond else '[FAIL] ') + name)
    if not cond:
        FAILS.append(name)

# ---- ① 实例化韩立元婴迷你宠物 ----
sp = SubPet('test-idx', '韩立元婴', 500, 400, isSubpet=True)
check('实例化成功且为跟随型', sp.follow_main and (sp.follow_main_x or sp.follow_main_y))
check('创建后自动注册到 SUBPET_MANAGER', SUBPET_MANAGER.widgets.get('韩立元婴') is sp)
check('初始非留守', sp.guard_mode is False)

# ---- ② 进入留守 ----
sp.enter_guard_mode()
check('guard_mode 置位', sp.guard_mode is True)
exp_x = geo.right() - sp.width() - 104
exp_y = geo.bottom() - sp.height() - 10
check(f'位置固定右下角 (x={sp.x()}≈{exp_x}, y={sp.y()}≈{exp_y})',
      abs(sp.x() - exp_x) <= 1 and abs(sp.y() - exp_y) <= 1)
check('destination 与当前位置一致（不再移动）', sp.destination == [sp.x(), sp.y()])
check('at_destination 置位', sp.at_destination is True)

# ---- ③ 留守中：本体移动不再跟随 ----
dest_before = list(sp.destination)
sp.update_main_pos(50, 50)          # 模拟（隐藏的）本体移动
check('update_main_pos 只记录不跟随', sp.destination == dest_before and sp.main_pos == [50, 50])
for _ in range(12):                  # 手动跑动画循环
    sp.animation()
check('留守动画循环：走待机分支（Default 无交互）',
      sp.act_name == 'Default' and sp.interact is None)
check('留守动画循环：位置不动', [sp.x(), sp.y()] == [exp_x, exp_y])

# ---- ④ 拖动解锁（fake 事件，坐标完全可控）----
from PySide6.QtCore import Qt, QPoint, QPointF

class FakeEv:
    def __init__(self, gpos, button=Qt.LeftButton):
        self._g, self._b = QPoint(gpos[0], gpos[1]), button
    def button(self): return self._b
    def globalPos(self): return self._g
    def globalPosition(self): return QPointF(self._g)
    def accept(self): pass

G0 = (exp_x + 10, exp_y + 10)
sp.mousePressEvent(FakeEv(G0))
check('留守中左键可开始拖动（is_follow_mouse 置位）', sp.is_follow_mouse is True)
drag_pos_before = QPoint(sp.x(), sp.y())
sp.mouseMoveEvent(FakeEv((G0[0] - 80, G0[1] - 60)))
check('拖动移动窗口生效（往左上 80,60）',
      sp.is_follow_mouse and sp.x() == drag_pos_before.x() - 80
      and sp.y() == drag_pos_before.y() - 60)
sp.mouseReleaseEvent(FakeEv((G0[0] - 80, G0[1] - 60)))
check('松手停止拖动、停在原地不触发掉落',
      sp.is_follow_mouse is False
      and sp.x() == drag_pos_before.x() - 80 and sp.y() == drag_pos_before.y() - 60)

# ---- ⑤ 广播与退出 ----
fake = types.SimpleNamespace(entered=False, exited=False,
                             enter_guard_mode=lambda: setattr(fake, 'entered', True),
                             exit_guard_mode=lambda: setattr(fake, 'exited', True))
SUBPET_MANAGER.register_widget('fake', fake)
SUBPET_MANAGER.set_guard_mode(True)
check('广播进入：真/假实例都收到', fake.entered and sp.guard_mode)
SUBPET_MANAGER.set_guard_mode(False)
check('广播退出：真/假实例都恢复', fake.exited and sp.guard_mode is False)

# ---- ⑥ 关闭注销 ----
sp.close()
app.processEvents()
check('关闭后自动注销', '韩立元婴' not in SUBPET_MANAGER.widgets)

print('\nRESULT:', 'PASS' if not FAILS else f'FAIL ({len(FAILS)})')
sys.exit(0 if not FAILS else 1)
