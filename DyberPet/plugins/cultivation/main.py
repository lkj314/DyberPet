# coding:utf-8
"""修仙放置玩法插件：驱动核心服务 + 桌宠演出。

分层（项目文档 §一）：
- 核心服务 `DyberPet/cultivation_service.py`（主程序模块，常驻，数值 100% 代码）
- 详情/操作 UI：角色面板「修仙之路」页（Dashboard/cultiUI.py），
  本插件只做：驱动 tick、灵石兑现、事件演出（台词/动作）、Ollama 感言。
  面板手动突破/游戏联动/服丹/炼丹产生的事件经 core.pending 队列回流，
  由本插件统一演出。被禁用时核心停摆但存档保留，重启用后离线收益照算。
"""
from __future__ import annotations

import atexit
import os
from typing import Optional

from PySide6.QtCore import QTimer

import DyberPet.settings as settings
from DyberPet.cultivation_service import REALMS, get_core
from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
from DyberPet.plugin_system.base import Plugin

from .commentary import REALM_ACT, Commentator


class CultivationPlugin(Plugin):
    def __init__(self, api):
        super().__init__(api)
        self.core = None
        self.comm: Optional[Commentator] = None
        self.tick_timer: Optional[QTimer] = None
        self.save_timer: Optional[QTimer] = None
        self._atexit_installed = False

    # ---- 生命周期 ----
    def on_enable(self):
        # 1) 核心（单例 + 读档；存档在 data/cultivation.json）
        data_dir = os.path.join(settings.CONFIGDIR, 'data')
        self.core = get_core(os.path.join(data_dir, 'cultivation.json'))
        self.core.auto_break = bool(self.api.settings.get('auto_break', True))

        # 2) 表达层
        self.comm = Commentator(
            use_llm=bool(self.api.settings.get('llm_talk', False)),
            model=str(self.api.settings.get('model', 'qwen2.5:7b')),
            ollama_base=DEFAULT_OLLAMA_BASE)

        # 3) 驱动：5s 结算 tick（时间戳差值，CPU 近零）+ 30s 延迟存档
        self.tick_timer = QTimer()
        self.tick_timer.setInterval(5000)
        self.tick_timer.timeout.connect(self._on_tick)
        self.tick_timer.start()
        self.save_timer = QTimer()
        self.save_timer.setInterval(30000)
        self.save_timer.timeout.connect(self.core.save_if_dirty)
        self.save_timer.start()

        # 4) 用户抚摸/点击桌宠 → 抚慰 + 打断双修
        try:
            self.api.events.touched.connect(self._on_touched)
        except Exception:  # noqa: BLE001
            pass

        # 5) 退出兜底（quit() 链路未必经过 on_disable）
        if not self._atexit_installed:
            atexit.register(self._atexit_save)
            self._atexit_installed = True

    def on_disable(self):
        if self.core is not None:
            try:
                self.core.tick()   # 结算最后一段
                self.core.save()
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

    def launch(self):
        """插件中心「打开」→ 角色面板·修仙之路页。"""
        self.api.pet.open_cultivation()

    # ---- 槽 ----
    def _on_touched(self, *args):
        if self.core is not None:
            self.core.mark_touch()

    def _atexit_save(self):
        if self.core is not None:
            try:
                self.core.tick()
                self.core.save()
            except Exception:  # noqa: BLE001
                pass

    # ---- 结算 + 兑现 + 演出 ----
    def _on_tick(self):
        if self.core is None:
            return
        try:
            events = self.core.tick()
            # 灵石兑现（修炼产出 → 商店货币；禁用期间余额留存，启用即补发）
            stones = self.core.take_stones()
            if stones > 0:
                self.api.pet.add_coins(stones)
            # 面板手动突破 / 游戏联动 / 服丹 / 炼丹事件统一在此演出
            events = list(events) + list(self.core.drain_pending())
        except Exception as e:  # noqa: BLE001
            print(f'[cultivation] tick error: {e!r}')
            return
        if not events:
            return
        # 合并连破（离线一夜可能连破多次——只播一场，铁律：克制）
        bks = [e for e in events if e['type'] == 'breakthrough']
        if bks:
            merged = dict(bks[-1])
            merged['from'] = bks[0]['from']
            self._play(merged)
            for e in events:
                if e['type'] != 'breakthrough':
                    self._play(e)
        else:
            for e in events:
                self._play(e)

    # ---- 演出实现 ----
    def _play(self, ev: dict):
        kind = ev.get('type')
        realm_idx = ev.get('to', self.core.stage() if self.core else 0)
        try:
            if kind == 'breakthrough':
                if ev.get('ascended'):
                    self._say('ascend', realm_idx, act=REALM_ACT['真仙'],
                              notify='渡劫成功，飞升仙界！', llm_kind='ascend')
                else:
                    self._say('breakthrough', realm_idx,
                              act=self.comm.act_for_realm(realm_idx),
                              notify=f'突破成功 → {ev.get("stage", "")}',
                              llm_kind='breakthrough', ctx_from=ev.get('from'))
            elif kind == 'break_fail':
                self._say('break_fail', realm_idx, act='read',
                          notify=f'突破失败，修为受损（{ev.get("stage", "")}）',
                          llm_kind='break_fail')
            elif kind == 'epiphany':
                self._say('epiphany', realm_idx, act='meditate',
                          notify='顿悟！修为大涨', llm_kind='epiphany')
            elif kind == 'exp_gain':
                self._say('exp_gain', realm_idx, act=None, notify=None)
            elif kind == 'alchemy_start':
                # 灵石费用在此经主程序信号链扣除（数据+商店/背包 UI 同步刷新）
                cost = int(ev.get('cost', 0))
                if cost > 0:
                    self.api.pet.add_coins(-cost)
                self._say('alchemy', realm_idx, act='alchemy',
                          notify=f'开炉炼制「{ev.get("pill", "")}」')
            elif kind == 'alchemy_done':
                # 丹药入背包（与商店同源物品）
                try:
                    self.api.pet.add_item(1, [str(ev.get('pill', ''))])
                except Exception:  # noqa: BLE001
                    pass
                self._say('alchemy_done', realm_idx, act='alchemy',
                          notify=f'丹成！「{ev.get("pill", "")}」已放入背包')
            elif kind == 'pill_used':
                self._say('pill_used', realm_idx, act=None,
                          notify=f'服丹：{ev.get("desc", "")}')

            # 历练记忆（persona L3）：关键事件写进人设记忆，供日后检索引用
            try:
                from DyberPet.persona_service import add_memory as _mem
                if kind == 'breakthrough' and ev.get('ascended'):
                    _mem('渡劫功成，飞升仙界', ['ascend'])
                elif kind == 'breakthrough':
                    _mem(f"突破至{ev.get('stage', '')}", ['breakthrough'])
                elif kind == 'break_fail':
                    _mem(f"突破{ev.get('stage', '')}失败，闭关疗伤", ['setback'])
                elif kind == 'epiphany':
                    _mem('修炼中忽有顿悟，修为大进', ['epiphany'])
                elif kind == 'alchemy_done':
                    _mem(f"开炉炼成「{ev.get('pill', '')}」", ['alchemy'])
                elif kind == 'pill_used':
                    _mem(f"服下「{ev.get('pill', '')}」", ['pill'])
                elif kind == 'exp_gain':
                    _mem('与道友斗法一场，胜有所悟', ['game'])
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            print(f'[cultivation] play error: {e!r}')

    def _say(self, event: str, realm_idx: int, act: Optional[str] = None,
             notify: Optional[str] = None, llm_kind: Optional[str] = None,
             ctx_from: Optional[int] = None):
        """台词（预设秒弹）+ 动作 + 通知；LLM 感言异步追补。"""
        text = self.comm.on_event(event, realm_idx)
        # 冒险离场期间桌宠隐藏：不出气泡/动作/LLM（通知保留，维持存在感）
        try:
            from DyberPet.adventure_service import is_away as _away
            if _away():
                text = None
                act = None
                llm_kind = None
        except Exception:  # noqa: BLE001
            pass
        if text:
            self.api.pet.say(text)
        if act:
            self.api.pet.play_act(act)
        if notify:
            try:
                self.api.pet.notify('system', notify)
            except Exception:  # noqa: BLE001
                pass
        if llm_kind and self.comm.use_llm:
            ctx = {'stage': REALMS[min(realm_idx // 4, 9)] + ' · 修行中'
                   if 0 <= realm_idx <= 39 else '飞升',
                   'to': realm_idx}
            if ctx_from is not None:
                ctx['from'] = ctx_from
            self.comm.request_talk(
                llm_kind, ctx,
                callback=lambda t: self.api.pet.say(t))
