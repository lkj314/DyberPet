# coding:utf-8
"""世界冒险玩法插件：驱动 core.adventure_service + 缺席叙事 + 桌面浮层。

分层（与修仙系统同构）：
- 数值核心 `DyberPet/adventure_service.py`（主程序模块，纯逻辑，掷骰子）
- 秘境表/事件模板（realms.py / events.py，纯数据）
- 本插件只做：tick 驱动、道韵分身与传讯符浮层、桌宠离场/归来演出、
  Ollama 叙事（persona 人设）、奖励兑现（修为/灵石/丹药/受伤 debuff）。
- 详情/操作 UI：角色面板「历练」页（Dashboard/adventureUI.py）。
"""
from __future__ import annotations

import atexit
import os
from typing import List, Optional

from PySide6.QtCore import QTimer

import DyberPet.settings as settings
from DyberPet.adventure_service import get_service, grant_gaming_buff, is_away
from DyberPet.cultivation_service import get_core
from DyberPet.plugin_system.base import Plugin

from . import events, narrative, realms
from .ui import DaoYunWidget, TalismanWidget


class AdventurePlugin(Plugin):
    def __init__(self, api):
        super().__init__(api)
        self.svc = None
        self.tick_timer: Optional[QTimer] = None
        self.save_timer: Optional[QTimer] = None
        self.daoyun: Optional[DaoYunWidget] = None
        self.talismans: List[TalismanWidget] = []
        self._was_away = False
        self._atexit_installed = False

    # ---- 设置便捷读取（key 里去掉括号说明，兼容文档原文案）----
    def _dur_key(self) -> str:
        raw = str(self.api.settings.get('default_duration', '中程(2时)'))
        return raw.split('(')[0].strip()

    def _risk_key(self) -> str:
        raw = str(self.api.settings.get('risk_preference', '均衡'))
        return raw.split('(')[0].strip()

    def _llm_on(self) -> bool:
        return bool(self.api.settings.get('llm_tale', True))

    # ---- 生命周期 ----
    def on_enable(self):
        data_dir = os.path.join(settings.CONFIGDIR, 'data')
        self.svc = get_service(os.path.join(data_dir, 'adventure.json'))

        self.tick_timer = QTimer()
        self.tick_timer.setInterval(5000)
        self.tick_timer.timeout.connect(self._on_tick)
        self.tick_timer.start()
        self.save_timer = QTimer()
        self.save_timer.setInterval(30000)
        self.save_timer.timeout.connect(self.svc.save_if_dirty)
        self.save_timer.start()

        if not self._atexit_installed:
            atexit.register(self._atexit_save)
            self._atexit_installed = True

        # 启动时若冒险仍在外（关机期间未归）：重建道韵 + 藏桌宠
        self._was_away = self.svc.status()['state'] == 'away'
        if self._was_away:
            self._show_daoyun()
            self.api.pet.hide_pet()

    def on_disable(self):
        for t in (self.tick_timer, self.save_timer):
            if t is not None:
                t.stop()
        self.tick_timer = self.save_timer = None
        self._close_floats()
        try:
            self.api.pet.show_pet()   # 安全兜底：绝不让桌宠消失
        except Exception:  # noqa: BLE001
            pass
        if self.svc is not None:
            try:
                self.svc.save()
            except Exception:  # noqa: BLE001
                pass
        self._was_away = False
        if self._atexit_installed:
            try:
                atexit.unregister(self._atexit_save)
            except Exception:  # noqa: BLE001
                pass
            self._atexit_installed = False

    def launch(self):
        """插件中心「打开」→ 角色面板·历练页。"""
        self.api.pet.open_adventure()

    def _atexit_save(self):
        if self.svc is not None:
            try:
                self.svc.tick()      # 补最后一段
                self.svc.save()
            except Exception:  # noqa: BLE001
                pass

    # ---- 浮层管理 ----
    def _show_daoyun(self):
        if self.daoyun is None:
            self.daoyun = DaoYunWidget(self.api.pet.open_adventure)
        self._place_daoyun()
        self.daoyun.show()

    def _place_daoyun(self):
        if self.daoyun is None:
            return
        try:
            x, y, w, h = self.api.pet.get_position()
            self.daoyun.move(int(x + w * 0.62), int(y + h * 0.18))
        except Exception:  # noqa: BLE001
            pass

    def _close_floats(self):
        if self.daoyun is not None:
            self.daoyun.hide()
            self.daoyun.close()
            self.daoyun = None
        for w in list(self.talismans):
            try:
                w.hide()
                w.close()
            except Exception:  # noqa: BLE001
                pass
        self.talismans.clear()

    def _restack_talismans(self):
        bx, by = TalismanWidget.base_position()
        for i, w in enumerate(self.talismans):
            w.move_to(bx, by - i * 100)

    # ---- 主循环 ----
    def _on_tick(self):
        if self.svc is None:
            return
        try:
            events_ = self.svc.tick()
        except Exception as e:  # noqa: BLE001
            print(f'[adventure] tick error: {e!r}')
            return
        away = self.svc.status()['state'] == 'away'
        if away and not self._was_away:
            self._on_departed()
        self._was_away = away

        for ev in events_:
            kind = ev.get('type')
            try:
                if kind == 'talisman':
                    self._on_talisman(ev)
                elif kind == 'return':
                    self._on_return(ev)
                elif kind == 'stay':
                    self._on_stay(ev)
            except Exception as e:  # noqa: BLE001
                print(f'[adventure] event {kind} error: {e!r}')

        if away:
            if self.daoyun is None:
                self._show_daoyun()
            else:
                self._place_daoyun()
            st = self.svc.status()
            remain = realms.dur_label(st.get('remain', 0))
            self.daoyun.set_tip(f"道韵分身\n本体在「{st['name']}」历练\n约 {remain} 后归来")
        elif self.daoyun is not None:
            self.daoyun.hide()

    # ---- 离场 ----
    def _on_departed(self):
        """桌宠御剑离家：先演离场动作，随后本体离场、道韵留守。"""
        self.api.pet.play_act('sword_fly')
        self._show_daoyun()
        QTimer.singleShot(2600, self._hide_pet_if_still_away)

    def _hide_pet_if_still_away(self):
        if self.svc is not None and self.svc.status()['state'] == 'away':
            self.api.pet.hide_pet()

    # ---- 传讯符 ----
    def _on_talisman(self, ev: dict):
        skeleton = ev.get('skeleton', {})
        idx, total = int(ev.get('idx', 0)), int(ev.get('total', 3))
        text = narrative.preset_talisman(skeleton, idx, total)
        w = TalismanWidget(idx, total, text)
        w.closed.connect(self._on_talisman_closed)
        self.talismans.append(w)
        self._restack_talismans()
        w.popup_at(*TalismanWidget.base_position())
        try:
            self.svc.note_talisman(idx, text)
        except Exception:  # noqa: BLE001
            pass
        if len(self.talismans) > 3:      # 未读也最多静置 3 张，防堆积
            old = self.talismans.pop(0)
            try:
                old.hide()
                old.close()
            except Exception:  # noqa: BLE001
                pass
            self._restack_talismans()
        if self._llm_on():
            narrative.request_talisman(
                skeleton, idx, total,
                callback=lambda t, _w=w, _i=idx: self._talisman_llm_back(_w, _i, t))

    def _talisman_llm_back(self, widget: TalismanWidget, idx: int, text):
        if not text:
            return
        widget.textArrived.emit(text)    # Signal 跨线程队列投递，安全
        try:
            self.svc.note_talisman(idx, text)
        except Exception:  # noqa: BLE001
            pass

    def _on_talisman_closed(self, w):
        if w in self.talismans:
            self.talismans.remove(w)
        self._restack_talismans()

    # ---- 留守事件（低频、错过无惩罚）----
    def _on_stay(self, ev: dict):
        kind = ev.get('kind')
        if kind == 'visitor':
            n = int(ev.get('gift_stones', 5))
            self.api.pet.notify('system', f'道友登门拜访，留了 {n} 灵石做贺礼')
            self.api.pet.add_coins(n)
        elif kind == 'fortune':
            n = int(ev.get('gift_exp', 50))
            self.api.pet.notify('system', '留守期间天降机缘，悟得些许妙理')
            self.api.add_exp(n, '留守机缘')
        else:
            self.api.pet.notify('system', '有妖兽窥探洞府，被道韵剑气惊退了')

    # ---- 归来 ----
    def _on_return(self, ev: dict):
        result = ev.get('result', {})
        offline = bool(ev.get('offline'))
        outcome = result.get('outcome', '小胜')
        skeleton = result.get('skeleton', {})

        # 1) 奖励兑现（修为/灵石/丹药——复用既有信号链）
        exp, stones = int(result.get('exp', 0)), int(result.get('stones', 0))
        if exp > 0:
            self.api.add_exp(exp, '历练归来')
        if stones > 0:
            self.api.pet.add_coins(stones)
        pill = result.get('pill')
        if pill:
            self.api.pet.add_item(1, [str(pill)])
        injury = result.get('injury')
        if injury:
            try:
                mult, seconds = float(injury[0]), float(injury[1])
                get_core().set_rate_modifier('injury', mult, seconds)
            except Exception:  # noqa: BLE001
                pass

        # 2) 本体归位 + 演出
        self.api.pet.show_pet()
        if self.daoyun is not None:
            self.daoyun.hide()
        act = 'qingzhu_fengyun_sword' if outcome in ('大胜', '小胜') else \
            ('read' if outcome in ('失利', '重伤') else 'sword_fly')
        self.api.pet.play_act(act)

        # 3) 叙事：预设秒弹，LLM 异步追补（模板+变量，绝不报数值）
        if offline:
            self.api.pet.notify('system', f"你不在的这些时日，它从「{result.get('name', '')}」回来了")
        preset = narrative.preset_return(skeleton, outcome)
        self.api.pet.say(preset)
        try:
            self.svc.update_last_record_story(preset)
        except Exception:  # noqa: BLE001
            pass
        summary = (f"历练「{result.get('name', '')}」{outcome}"
                   + (f"，寻得「{pill}」" if pill else ''))
        try:
            from DyberPet.persona_service import add_memory
            add_memory(summary, ['adventure'])
        except Exception:  # noqa: BLE001
            pass

        notif = f'历练归来：{outcome}！'
        if exp > 0 or stones > 0:
            notif += f'修为 +{exp}，灵石 +{stones}' if exp > 0 else f'灵石 +{stones}'
        if pill:
            notif += f'，「{pill}」入背包'
        if injury:
            notif += '（带伤，修行减速，会自行恢复）'
        self.api.pet.notify('system', notif)

        if self._llm_on():
            length = str(self.api.settings.get('narrative_length', '短篇(80字)'))
            narrative.request_return(
                skeleton, result, length,
                callback=lambda t: self._return_llm_back(t))

        # 4) 自动再派
        if bool(self.api.settings.get('auto_dispatch', False)):
            QTimer.singleShot(6000, self._auto_redispatch)

    def _return_llm_back(self, text):
        if text:
            self.api.pet.say(text)
            try:
                self.svc.update_last_record_story(text)
            except Exception:  # noqa: BLE001
                pass

    def _auto_redispatch(self):
        """归来后自动再派：取当前可去的最高阶秘境 + 默认时长/策略。"""
        if self.svc is None or self.svc.status()['state'] == 'away':
            return
        try:
            self_group = min(max(get_core().stage(), 0) // 4, 10)
            tier = 0
            for i, t in enumerate(realms.REALM_TIERS):
                if self_group >= t['req']:
                    tier = i
            spec, err = realms.build_spec(tier, self._dur_key(), self._risk_key(),
                                          self_group)
            if spec is None:
                return
            self.svc.dispatch(spec, events.pick(tier))
        except Exception as e:  # noqa: BLE001
            print(f'[adventure] auto redispatch failed: {e!r}')
