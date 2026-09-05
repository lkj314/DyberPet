# coding:utf-8
"""更新检测、去重与提醒文案。

注意力铁律（设计文档 §6.2，延续项目一贯原则）：
- ❌ 不弹窗、不红点、不做"今日必看"任务
- ✅ 气泡提醒 3 秒自动消失；一天最多提醒一次（remind_hour 之后的第一个 tick）
- ✅ 多部番合并成一条；同一话只提醒一次（notified.json 持久化，重启不重提）

数值与叙事分离：本模块只算"有没有更新、第几话"（确定性），怎么说交给
修仙人设（persona_service quip 模式，异步追补，失败静默）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from typing import Callable, Optional

from .episode import calc_air_episode, should_notify_today
from .subscription import sub_key

_TEMPLATES = [
    "道友，《{name}》第{ep}话已更新。",
    "今日有更新：《{name}》第{ep}话。",
    "{name} 更新到第{ep}话了，道友莫要错过。",
]


class UpdateChecker:
    def __init__(self, notified_path: str):
        self.path = notified_path

    # ---- 去重记录 ----
    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            print(f"[bangumi] notified save failed: {e!r}")

    def already_notified_today(self, today: _dt.date) -> bool:
        return self._load().get("last_date") == today.isoformat()

    # ---- 核心检测 ----
    def check(self, subs: list[dict], today: _dt.date,
              timezone_mode: str = "大陆时间",
              bangumi_stale: bool = False) -> list[dict]:
        """返回今天有新话数的更新列表 [{sub, episode}]。

        - bili 条目：watch.last_air_episode / watch.today_eps 由
          bili_client.fetch_season_update 预先写入（真实 pub_time）；
        - bangumi 条目：按 air_weekday 推算；bangumi_stale=True（断网
          用了过期缓存）时跳过——宁漏报不错报；bili 是真实数据不受影响。
        同时把 latest 回写进 sub（调用方负责 save）。
        去重键 sub_key："source:id"（旧版纯 id 键自动兼容读取）。"""
        updates = []
        notified = self._load().get("eps", {})
        for sub in subs:
            notify = sub.get("notify") or {}
            if not notify.get("enabled", True):
                continue
            key = sub_key(sub)
            w = sub.setdefault("watch", {})
            if sub.get("source") == "bili":
                latest = int(w.get("last_air_episode", 0) or 0)
                today_eps = [int(n) for n in (w.get("today_eps") or [])]
                cur = int(w.get("current_episode", 0) or 0)
                seen = int(notified.get(key, 0))
                new_eps = [n for n in today_eps if n > cur and n > seen]
                if new_eps:
                    updates.append({"sub": sub, "episode": max(new_eps)})
                continue
            # ---- bangumi 条目 ----
            if bangumi_stale:
                continue
            if not should_notify_today(sub, today, timezone_mode):
                continue
            ep = calc_air_episode(sub, today)
            if ep > int(w.get("last_air_episode", 0) or 0):
                w["last_air_episode"] = ep
            cur = int(w.get("current_episode", 0) or 0)
            # 有新话 + 没追平 + 这一话没提醒过 → 提醒
            seen = int(notified.get(key,
                     notified.get(str(sub.get("subject_id")), 0)))
            if ep > cur and ep > seen:
                updates.append({"sub": sub, "episode": ep})
        return updates

    def mark_notified(self, updates: list[dict], today: _dt.date) -> None:
        data = self._load()
        eps = data.setdefault("eps", {})
        for u in updates:
            eps[sub_key(u["sub"])] = int(u["episode"])
        data["last_date"] = today.isoformat()
        # 记录条目超过 500 时瘦身（防止无限膨胀）
        if len(eps) > 500:
            keep = sorted(eps.items(), key=lambda kv: kv[1], reverse=True)[:300]
            data["eps"] = dict(keep)
        self._save(data)

    # ---- 文案 ----
    def build_text(self, updates: list[dict], merge: bool = True) -> str:
        if not updates:
            return ""
        if merge and len(updates) > 1:
            parts = [f"《{u['sub'].get('name_cn') or u['sub'].get('name', '')}》"
                     f"第{u['episode']}话" for u in updates]
            return "今日更新：" + "、".join(parts) + "。"
        u = updates[0]
        name = u['sub'].get('name_cn') or u['sub'].get('name', '')
        text = _TEMPLATES[hash(name) % len(_TEMPLATES)].format(
            name=name, ep=u['episode'])
        total = int(u['sub'].get('eps_total', 0) or 0)
        if total and u['episode'] >= total:
            text += "（最终话！）"
        return text

    # ---- 修仙人设播报（异步追补，失败静默）----
    @staticmethod
    def request_quip(updates: list[dict],
                     callback: Callable[[str], None]) -> None:
        """persona_service quip 润色提醒文案；不可用/失败时不回调
        （模板文案已在气泡里兜底，见 main.py 的两段式播报）。"""
        def _run():
            try:
                from DyberPet.persona_service import get_persona
                p = get_persona()
                if not p.available():
                    return
                if len(updates) == 1:
                    name = (updates[0]['sub'].get('name_cn')
                            or updates[0]['sub'].get('name', ''))
                    ask = (f"你追的动画《{name}》第{updates[0]['episode']}话"
                           "今天更新了，按你的性子提醒道友一声。")
                else:
                    names = "、".join(
                        f"《{u['sub'].get('name_cn') or u['sub'].get('name', '')}》"
                        f"第{u['episode']}话" for u in updates[:4])
                    ask = f"今天更新的动画有：{names}。按你的性子提醒道友一声。"
                text = p.chat(ask, mode='quip', include_memories=False)
                if text and callback:
                    callback(text)
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_run, daemon=True).start()
