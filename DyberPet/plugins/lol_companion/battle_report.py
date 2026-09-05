# -*- coding: utf-8 -*-
"""对局战报核心：KDA 统计 + 修仙境界判档 + 文案生成 + 落盘留档。

铁律 5：数值只认代码——境界 100% 由 KDA 数值掷定，LLM 零参与。
判词来自静态模板池（离线兜底：预设文案永远是兜底）。
落档：data/lol_reports.json（相对 cwd，与存档同目录），保留最近 30 局。
"""
import os
import json
import random
import datetime

# ------------------------------------------------------------------ #
# 境界分档：KDA = (K + A) / max(D, 1)，阈值从高到低，首条命中即判
# ------------------------------------------------------------------ #
REALM_TIERS = [
    (5.0, "化神期"),
    (3.5, "元婴期"),
    (2.0, "金丹期"),
    (1.0, "筑基期"),
    (0.0, "炼气期"),
]

# 判词模板池：每境界多条随机抽（静态兜底，离线也有战报）
REALM_QUOTES = {
    "化神期": [
        "剑意通神，一念万里——此子已窥大道之门。",
        "神游太虚，杀伐果断，化神之气尽显无疑。",
    ],
    "元婴期": [
        "元婴凝实，进退有度，道基愈发坚实。",
        "灵台清明，元婴之光初显，可堪大用。",
    ],
    "金丹期": [
        "金丹初成，锋芒毕露，然道心尚需打磨。",
        "丹火纯青，一战可称豪杰。",
    ],
    "筑基期": [
        "筑基已稳，道途方启，仍需历练。",
        "根基尚浅，胜在心诚，来日可期。",
    ],
    "炼气期": [
        "道途漫漫，回炉再修，莫要气馁。",
        "灵气未聚，凡胎未脱，且去打坐。",
    ],
}

_REPORTS_KEEP = 30  # 留档上限（局）


def kda_value(kills, deaths, assists):
    return (kills + assists) / max(deaths, 1)


def realm_for(kda_val):
    """KDA → 境界名（纯代码掷定，无随机）。"""
    for threshold, name in REALM_TIERS:
        if kda_val >= threshold:
            return name
    return REALM_TIERS[-1][1]


def quote_for(realm):
    """境界判词（模板池随机）。"""
    pool = REALM_QUOTES.get(realm)
    return random.choice(pool) if pool else "道心坚定，继续前行。"


def _row(player):
    """eog player → 战报行（防御式取值，字段缺失按 0/'' 兜底）。"""
    st = player.get("stats") or {}
    k = int(st.get("kills") or 0)
    d = int(st.get("deaths") or 0)
    a = int(st.get("assists") or 0)
    val = round(kda_value(k, d, a), 2)
    return {
        "name": str(player.get("summonerName") or "?"),
        "champ": str(player.get("championName") or "?"),
        "summoner_id": player.get("summonerId") or "",
        "team": player.get("team"),
        "k": k, "d": d, "a": a, "kda": val,
        "realm": realm_for(val),
        "win": bool(st.get("win")),
    }


def build_report(eog, my_name):
    """从 eog-stats-block 构建完整战报 dict。数据无效返回 None。

    返回字段：
      game_id / mode / time / duration_min / result(win|lose)
      my: 自己的行    mvp: 全场 KDA 最高
      players: 全部玩家（KDA 降序）
      bubble: 气泡判词短句    notify: 通知栏完整战报文本
    """
    raw = eog.get("players") or []
    if not raw:
        return None
    rows = [_row(p) for p in raw]
    me = next((r for r in rows if r["name"] == my_name), None)
    if me is None:
        # 兜底：名字对不上（改过名/大小写差异）时取 stats.win 与主队一致的策略失效，
        # 直接按第一行同名宽松匹配失败 → 取 rows[0] 不合适；标记数据存疑仍出报告
        me = rows[0]
    my_team = me["team"]
    result = "win" if me["win"] else "lose"
    mvp = max(rows, key=lambda r: r["kda"])
    rows.sort(key=lambda r: r["kda"], reverse=True)

    duration = eog.get("gameDuration") or 0
    mode = str(eog.get("gameMode") or "对局")
    mode_cn = {"CLASSIC": "召唤师峡谷", "ARAM": "嚎哭深渊"}.get(mode, mode)
    minutes = max(int(round(duration / 60)), 1)

    bubble = f"此局{me['realm']} ({me['k']}/{me['d']}/{me['a']}) · {quote_for(me['realm'])}"
    if result == "win":
        bubble = "凯旋而归！ " + bubble
    else:
        bubble = "惜败。 " + bubble

    lines = [f"【战报·{mode_cn}】{'胜利！' if result == 'win' else '败北'}（{minutes}分钟）",
             f"▍你：{me['realm']} · {me['champ']} · "
             f"{me['k']}/{me['d']}/{me['a']} (KDA {me['kda']})"]

    mates = [r for r in rows if r is not me and r["team"] == my_team]
    if mates:
        lines.append("─ 我方道友 ─")
        for r in mates:
            lines.append(f"{r['realm']} {r['name']}({r['champ']}) "
                         f"{r['k']}/{r['d']}/{r['a']}")
    enemies = [r for r in rows if r["team"] != my_team]
    if enemies:
        top = max(enemies, key=lambda r: r["kda"])
        lines.append(f"─ 敌方最高 ─ {top['realm']} {top['name']}({top['champ']}) "
                     f"{top['k']}/{top['d']}/{top['a']}")
    lines.append(f"判词：{quote_for(me['realm'])}")

    return {
        "game_id": eog.get("gameId"),
        "mode": mode,
        "mode_cn": mode_cn,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "duration_min": minutes,
        "result": result,
        "my": me,
        "mvp": mvp,
        "players": rows,
        "bubble": bubble,
        "notify": "\n".join(lines),
    }


# ------------------------------------------------------------------ #
# 落盘留档：data/lol_reports.json（相对 cwd，与存档同目录）
# ------------------------------------------------------------------ #
def save_report(report):
    """追加留档，保留最近 _REPORTS_KEEP 局。失败静默（不打扰主流程）。"""
    try:
        path = os.path.join("data", "lol_reports.json")
        os.makedirs("data", exist_ok=True)
        data = {"reports": []}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        reports = data.get("reports", [])
        # 按 gameId 去重（补结算重拉等场景）
        reports = [r for r in reports if r.get("game_id") != report.get("game_id")]
        reports.insert(0, {
            "game_id": report.get("game_id"),
            "time": report["time"],
            "mode": report["mode_cn"],
            "duration_min": report["duration_min"],
            "result": report["result"],
            "my": report["my"],
            "players": report["players"],
        })
        data["reports"] = reports[:_REPORTS_KEEP]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
