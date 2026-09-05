# coding:utf-8
"""修仙世界守护（主程序常驻）：驱动世界服务 + 克制的演出。

历史沿革：本模块原为 xiuxian_world 插件——但修仙世界是角色层面的核心
体验，不是"插件功能"。v0.6.8 起收编进主程序：
- 世界日志/奇遇请示入口在角色面板「修仙世界」页（Dashboard/worldUI.py）；
- 本守护随主程序启动，负责世界时钟推进与演出，与插件系统彻底解耦。

分层：
- 核心服务 `DyberPet/world_service.py`（纯逻辑零 LLM，存档 CONFIGDIR/data）
- 本守护只做：内容装载（res/world）、驱动 tick（60s 现实时间 → 世界日
  补算）、L2/L3 事件演出（通知/气泡，绝不弹窗）、玩家回响收益兑现。
- **注意力铁律（设计文档 §10.2）**：L1 静默入库、L2 可选通知、L3 气泡
  一句带过。日志是「想看时永远有新内容」，不是「不断催你看」。
"""
from __future__ import annotations

import atexit
import os
import random
from typing import Optional

from PySide6.QtCore import QTimer

import DyberPet.settings as settings
from DyberPet.world_service import get_world
from DyberPet.choice_service import get_choice

_SPEED_SPY = {'标准': 3600.0, '疾行': 1800.0, '悠远': 10800.0}
TRAVEL_P_PER_TICK = 0.10     # 游历琐事概率/60s tick（约 10 分钟一条 → 2 小时游历 ≈ 12 条）
IDLE_QIYU_P = 0.02           # 留守奇遇概率/60s tick（平均约 50 分钟一次）

_SAVE_NAME = os.path.join(settings.CONFIGDIR, 'data', 'world_state.json')
_DATA_DIR = os.path.join(settings.BASEDIR, 'res', 'world')


class WorldDaemon:
    """世界模拟守护：持 PetWidget 直调演出（取代原插件 api 门面）。

    save_path/data_dir 可注入（测试隔离用）；缺省 = 正式存档 + res/world。
    """

    def __init__(self, pet_widget=None, save_path: Optional[str] = None,
                 data_dir: Optional[str] = None):
        self.pet = pet_widget
        self._save_path = save_path or _SAVE_NAME
        self._data_dir = data_dir or _DATA_DIR
        self.world = None
        self.choice = None
        self.tick_timer: Optional[QTimer] = None
        self.save_timer: Optional[QTimer] = None
        self._atexit_installed = False

    # ---- 生命周期 ----
    def start(self):
        # 1) 世界核心（单例 + 读档）
        self.world = get_world(self._save_path)
        # 2) 内容装载（事件表/名字池/地点池/天下大事/奇遇库/玩家回响/游历琐事），
        #    装载后自动开天辟地
        self.world.load_content(self._data_dir)
        self.world.seconds_per_year = _SPEED_SPY.get(
            str(settings.world_speed), 3600.0)
        # 3) 抉择系统（奇遇请示 + 因果回响，状态挂世界存档）
        self.choice = get_choice(self.world)
        self.choice.load(_DATA_DIR)

        # 4) 离线补算（关机期间世界照常转），离开越久变化越大
        try:
            stats = self.world.catch_up()
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] catch_up error: {e!r}')
            stats = {'days': 0, 'logs': 0}
        if stats.get('days', 0) >= 30:
            self._greet(stats['days'])

        # 5) 驱动：60s 现实时间 ≈ 标准 6 世界日；2 分钟延迟存档
        self.tick_timer = QTimer()
        self.tick_timer.setInterval(60000)
        self.tick_timer.timeout.connect(self._on_tick)
        self.tick_timer.start()
        self.save_timer = QTimer()
        self.save_timer.setInterval(120000)
        self.save_timer.timeout.connect(self.world.save_if_dirty)
        self.save_timer.start()

        # 6) 退出兜底
        if not self._atexit_installed:
            atexit.register(self._atexit_save)
            self._atexit_installed = True

    def stop(self):
        if self.world is not None:
            try:
                self.world.catch_up()
                self.world.save()
            except Exception:  # noqa: BLE001
                pass
        for t in (self.tick_timer, self.save_timer):
            if t is not None:
                t.stop()
        self.tick_timer = self.save_timer = None
        if self._atexit_installed:
            try:
                atexit.unregister(self._atexit_save)
            except Exception:  # noqa: BLE001
                pass
            self._atexit_installed = False

    # ---- tick + 演出 ----
    def _on_tick(self):
        if self.world is None:
            return
        # 流速设置实时生效
        self.world.seconds_per_year = _SPEED_SPY.get(
            str(settings.world_speed), 3600.0)
        try:
            self.world.catch_up()
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] tick error: {e!r}')
            return

        # ---- 游历直播：本体在外，琐事入流（琐碎即血肉，文档 §3.2）----
        self.travel_ambient()

        # ---- 留守奇遇：本体在家、低频掷骰请示 ----
        self.idle_qiyu()

        # ---- 玩家回响收益兑现（离线补算期间结算出的）----
        self.apply_player_grants()

        notable = self.world.drain_notable()
        # 克制：每个 tick 最多演出 2 条，L3 优先
        notable.sort(key=lambda x: -int(x.get('level', 1)))
        shown = 0
        for lg in notable:
            if shown >= 2:
                break
            text = str(lg.get('text', '')).strip()
            if not text:
                continue
            level = int(lg.get('level', 1))
            if level >= 3:
                if bool(settings.world_bubble_major):
                    self.say(text)
                    shown += 1
            elif level == 2 and bool(settings.world_notify_medium):
                self.notify(text)
                shown += 1

    # ---- 演出原语（PetWidget 直调，全兜底不吞进程）----
    def say(self, text: str):
        if not text or self.pet is None:
            return
        try:
            self.pet.show_speech(str(text))
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] say failed: {e!r}')

    def notify(self, message: str):
        if not message or self.pet is None:
            return
        try:
            self.pet.register_notification('system', str(message))
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] notify failed: {e!r}')

    def travel_ambient(self):
        """本体外出时，游历琐事按概率写入主线日志流（L1 静默）。"""
        if not bool(settings.world_travel_log):
            return
        try:
            from DyberPet.adventure_service import get_service, is_away
            if not is_away():
                return
            if random.random() > TRAVEL_P_PER_TICK:
                return
            st = get_service().status()
            loc = str(st.get('name', ''))
            realm = self.player_realm()
            text = self.world.gen_travel_log(loc, realm)
            if text:
                self.world.player_log(text, 1)
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] travel ambient error: {e!r}')

    def idle_qiyu(self):
        """留守期间低频奇遇请示（归来请示由冒险系统触发，互不冲突）。"""
        if not bool(settings.world_qiyu_choices):
            return
        if self.choice is None:
            return
        try:
            from DyberPet.adventure_service import is_away
        except Exception:  # noqa: BLE001
            is_away = lambda: False  # noqa: E731
        if is_away():
            return
        if random.random() > IDLE_QIYU_P:
            return
        pending = self.choice.offer({'phase': 'idle'})
        if pending:
            self.announce(pending)

    def announce(self, pending: dict):
        """奇遇请示演出：气泡一句 + 通知（绝不弹窗，角色面板应答）。"""
        self.say(f"【请示·{pending['title']}】{pending['narrative']}")
        self.notify(
            f"奇遇请示「{pending['title']}」：{pending['narrative']}"
            f"（打开角色面板「修仙世界」，替我拿个主意）")

    def apply_player_grants(self, grants=None):
        """世界结算的玩家收益（玩家回响/奇遇抉择）→ 修为/灵石/丹药/受伤。

        角色面板「修仙世界」页应答奇遇时也调本方法兑现收益（与 tick 的
        世界回响收益互不重叠——由调用方保证 grants 只消费一次）。
        """
        if grants is None:
            try:
                grants = self.world.drain_grants()
            except Exception:  # noqa: BLE001
                return
        if not grants:
            return
        exp = stones = 0
        for g in grants:
            exp += int(g.get('exp', 0) or 0)
            stones += int(g.get('stones', 0) or 0)
            item = g.get('item')
            if item and self.pet is not None:
                try:
                    self.pet.add_item(1, [str(item)])
                except Exception as e:  # noqa: BLE001
                    print(f'[world_daemon] add_item failed: {e!r}')
            injury = g.get('injury')
            if injury:
                try:
                    from DyberPet.cultivation_service import get_core
                    get_core().set_rate_modifier(
                        'injury', float(injury[0]), float(injury[1]))
                except Exception as e:  # noqa: BLE001
                    print(f'[world_daemon] injury failed: {e!r}')
        if exp > 0:
            try:
                from DyberPet.cultivation_service import add_exp as _add
                _add(exp, '善缘回响')
            except Exception as e:  # noqa: BLE001
                print(f'[world_daemon] add_exp failed: {e!r}')
        if stones > 0 and self.pet is not None:
            try:
                self.pet.addCoins.emit(stones)
            except Exception as e:  # noqa: BLE001
                print(f'[world_daemon] add_coins failed: {e!r}')
        if (exp or stones) and self.pet is not None:
            self.notify(
                f"当年的因，今日的果——修为 +{exp}，灵石 +{stones}"
                if exp else f"当年的因，今日的果——灵石 +{stones}")

    @staticmethod
    def player_realm() -> int:
        try:
            from DyberPet.cultivation_service import get_core
            return min(max(get_core().stage(), 0) // 4, 9)
        except Exception:  # noqa: BLE001
            return 0

    def _greet(self, days: int):
        """离开较久归来：一句话交代世界变了（§8.2 惊喜感的最佳来源）。"""
        years = max(1, round(days / 365))
        self.notify(
            f'你离开的这段时日，修仙世界已过去约 {years} 年——'
            f'打开角色面板「修仙世界」看看变化。')

    def _atexit_save(self):
        if self.world is not None:
            try:
                self.world.save()
            except Exception:  # noqa: BLE001
                pass


_DAEMON: Optional[WorldDaemon] = None


def start_daemon(pet_widget=None) -> WorldDaemon:
    """主程序启动时调用（幂等）：创建并启动世界守护。"""
    global _DAEMON
    if _DAEMON is None:
        _DAEMON = WorldDaemon(pet_widget)
        _DAEMON.start()
    return _DAEMON


def get_daemon() -> Optional[WorldDaemon]:
    """角色面板等处取守护（未启动返回 None，调用方自行兜底）。"""
    return _DAEMON
