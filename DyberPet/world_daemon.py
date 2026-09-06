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
import re
import threading
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

import DyberPet.settings as settings
from DyberPet.world_service import get_world
from DyberPet.choice_service import get_choice

_SPEED_SPY = {'标准': 3600.0, '疾行': 1800.0, '悠远': 10800.0}
TRAVEL_P_PER_TICK = 0.10     # 游历琐事概率/60s tick（约 10 分钟一条 → 2 小时游历 ≈ 12 条）
IDLE_QIYU_P = 0.02           # 留守奇遇概率/60s tick（平均约 50 分钟一次）

DECIDE_TIMEOUT_S = 45        # AI 决策看门狗：超时强制规则回退，绝不悬置
DECIDE_LLM_TIMEOUT = 30      # Ollama 单次调用超时（秒）
_DECIDE_ANS_RE = re.compile(
    r'^\s*[「"“]?([A-Da-d])[」"”]?\s*(?:[|｜:：,，]\s*)?(.{0,60})')


def parse_decision(text: str, choices: list) -> tuple:
    """解析 LLM 决策输出「字母|理由」→ (choice_key|None, reason)。

    字母按选项顺序映射（A=第一个选项）。解析失败/字母越界返回 (None, '')。
    """
    if not text or not choices:
        return None, ''
    m = _DECIDE_ANS_RE.match(str(text).strip().splitlines()[0] if str(text).strip() else '')
    if not m:
        return None, ''
    idx = ord(m.group(1).upper()) - ord('A')
    if idx < 0 or idx >= len(choices):
        return None, ''
    reason = re.sub(r'[「"“」"”]。?$', '', str(m.group(2) or '').strip())
    return str(choices[idx].get('key', '')), reason[:60]

_SAVE_NAME = os.path.join(settings.CONFIGDIR, 'data', 'world_state.json')
_DATA_DIR = os.path.join(settings.BASEDIR, 'res', 'world')


class _DecideSignal(QObject):
    """AI 决策线程 → 主线程的跨线程投递（队列连接，天然线程安全）。"""

    decided = Signal(dict)


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
        # ---- 奇遇 AI 自主决策（v0.6.9：选择权归桌宠，玩家做观察者）----
        self._decide_sig = _DecideSignal()
        self._decide_sig.decided.connect(self._on_decided)
        self._deciding_id: Optional[str] = None   # 正在决策的 pending id（防重复）
        self._watchdog: Optional[QTimer] = None   # 决策超时兜底（绝不悬置请示）

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
        self._watchdog_stop()
        self._deciding_id = None
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
        """奇遇演出：按模式分叉——AI 自主判断（默认）或请示玩家拍板。"""
        if bool(getattr(settings, 'world_ai_decide', True)):
            # 自主判断：只播事件本身，不需要玩家停下做什么
            self.say(f"【奇遇·{pending['title']}】{pending['narrative']}")
            self.notify(
                f"奇遇「{pending['title']}」：它正在自行斟酌如何处置……"
                f"稍后向你回禀")
            self.request_decide()
            return
        self.say(f"【请示·{pending['title']}】{pending['narrative']}")
        self.notify(
            f"奇遇请示「{pending['title']}」：{pending['narrative']}"
            f"（打开角色面板「修仙世界」，替我拿个主意）")

    # ---- 奇遇 AI 自主决策（v0.6.9：LLM 只选方向，数值仍 100% 规则结算）----
    def request_decide(self):
        """对当前 pending 奇遇启动 AI 决策（幂等；adventure 插件归来奇遇也调这里）。"""
        if self.world is None or self.choice is None:
            return
        pending = self.world.world.get('pending_choice')
        if not pending or not bool(getattr(settings, 'world_ai_decide', True)):
            return
        pid = str(pending.get('id', ''))
        if self._deciding_id == pid:
            return                          # 已在斟酌这道题
        self._deciding_id = pid
        choices = list(pending.get('choices', []))
        t = threading.Thread(target=self._decide_worker, args=(pending, choices),
                             daemon=True)
        t.start()
        # 看门狗：LLM 挂死/超时也必须收敛，绝不悬置请示卡住后续奇遇
        if self._watchdog is None:
            self._watchdog = QTimer()
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._decide_timeout)
        self._watchdog.start(DECIDE_TIMEOUT_S * 1000)

    def _decide_worker(self, pending: dict, choices: list):
        """后台线程：以桌宠人格向 Ollama 请求抉择（同步调用，绝不进主线程）。"""
        answer = None
        try:
            from DyberPet.persona_service import get_persona, _THINK_RE
            persona = get_persona()
            if persona.available():
                letters = '\n'.join(
                    f"{chr(ord('A') + i)}. {c.get('text', '')}"
                    for i, c in enumerate(choices))
                user = (f"【当前抉择】{pending.get('title', '奇遇')}\n"
                        f"{pending.get('narrative', '')}\n你的选项：\n{letters}\n"
                        f"只回答：选项字母|一句理由")
                system = persona.build_prompt('decide', include_memories=False)
                raw = persona._generate(system, user,
                                        model=persona._default_model(),
                                        num_predict=64,
                                        timeout=DECIDE_LLM_TIMEOUT)
                if raw:
                    answer = parse_decision(_THINK_RE.sub('', raw), choices)
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] decide worker error: {e!r}')
        # answer=None（LLM 不可用/解析失败）→ 主线程槽内规则回退
        self._decide_sig.decided.emit({
            'id': str(pending.get('id', '')), 'ts': pending.get('ts'),
            'key': answer[0] if answer else None,
            'reason': answer[1] if answer else ''})

    def _on_decided(self, payload: dict):
        """主线程槽：校验 → 结算 → 兑现 → 汇报（数值 100% choice_service 掷骰）。"""
        try:
            self._watchdog_stop()
            self._deciding_id = None
            pending = (self.world.world.get('pending_choice')
                       if self.world is not None else None)
            if not pending or str(pending.get('id', '')) != payload.get('id'):
                return                          # 已被处理/世界重置，丢弃迟到答案
            key = payload.get('key')
            choices = list(pending.get('choices', []))
            valid = next((c for c in choices if c.get('key') == key), None)
            if valid is None:
                # 规则回退：随机一项（LLM 不可用时桌宠"随缘"处置）
                if not choices:
                    return
                key = random.choice(choices).get('key')
                payload['reason'] = ''
            reason = str(payload.get('reason') or '').strip()
            self._finalize_choice(pending, key, reason)
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] on_decided error: {e!r}')

    def _decide_timeout(self):
        """看门狗：决策线程迟迟不归（Ollama 卡死等）→ 规则回退强制收敛。"""
        try:
            if self._deciding_id is None:
                return
            pending = (self.world.world.get('pending_choice')
                       if self.world is not None else None)
            if not pending or str(pending.get('id', '')) != self._deciding_id:
                self._deciding_id = None
                return
            self._deciding_id = None
            choices = list(pending.get('choices', []))
            if choices:
                self._finalize_choice(pending,
                                      random.choice(choices).get('key'), '')
        except Exception as e:  # noqa: BLE001
            print(f'[world_daemon] decide timeout error: {e!r}')

    def _watchdog_stop(self):
        if self._watchdog is not None:
            self._watchdog.stop()

    def _finalize_choice(self, pending: dict, key: str, reason: str):
        """结算 + 兑现 + 三级汇报（气泡一句 / 通知回禀 / 日志入流）。"""
        result = self.choice.resolve(key)
        if result is None:
            return
        choice_text = next((c.get('text', '') for c in pending.get('choices', [])
                            if c.get('key') == key), '')
        g = result.get('grants') or {}
        if any(g.values()):
            self.apply_player_grants([g])
        # 抉择志（面板回放 + 持久化）
        self.world.world['last_decision'] = {
            'title': pending.get('title', ''), 'day': pending.get('day', 0),
            'narrative': pending.get('narrative', ''),
            'choice_text': choice_text, 'reason': reason,
            'result_text': result.get('text', ''),
            'grants': dict(g), 'echoes': bool(result.get('echoes')),
            'ts': pending.get('ts'),
        }
        self.world.dirty = True
        # 气泡：一句带过（L3）
        self.say(f"「{pending.get('title', '')}」——我{choice_text}。")
        # 通知：完整回禀（用户点名的"事后看一眼"渠道，独立于 world_notify_medium）
        harvest = []
        if int(g.get('exp', 0) or 0) > 0:
            harvest.append(f"修为 +{int(g['exp'])}")
        if int(g.get('stones', 0) or 0) > 0:
            harvest.append(f"灵石 +{int(g['stones'])}")
        if g.get('item'):
            harvest.append(f"「{g['item']}」入背包")
        if g.get('injury'):
            harvest.append('受了些伤，修行暂缓')
        echo_hint = '（因果已种下，回响不知何日归来）' \
            if result.get('echoes') else ''
        why = f"（{reason}）" if reason else ''
        self.notify(
            f"【抉择回禀】遇「{pending.get('title', '')}」，"
            f"我{choice_text}{why}。\n{result.get('text', '')}"
            + (f"\n收获：{'，'.join(harvest)}。" if harvest else '')
            + echo_hint)
        # 日志入流（游历直播线，L3 让翻日志时一定看见）
        try:
            self.world.player_log(
                f"奇遇「{pending.get('title', '')}」：我{choice_text}{why}"
                f"{result.get('text', '')}"
                + (f"（收获：{'，'.join(harvest)}）" if harvest else ''), 3)
        except Exception:  # noqa: BLE001
            pass
        try:
            from DyberPet.persona_service import add_memory
            add_memory(f"奇遇「{pending.get('title', '')}」我{choice_text}",
                       ['world', 'choice'])
        except Exception:  # noqa: BLE001
            pass

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
