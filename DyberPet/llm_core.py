"""LoL 实时陪玩模块（DyberPet 底座版）。

把老项目 ``lol-realtime-translator`` 的 caster / emotion 逻辑移植到 DyberPet：
- 轮询 Riot **Live Client Data API**（本地 2999 端口）读取对局事件；
- 调用本机 **Ollama** 模型产出"肥牛"风格中文解说词；
- 把"解说词"和"情绪"通过 Qt Signal 跨线程投递给 PetWidget，
  分别驱动气泡与程序化 transform 反应。

所有对 localhost（LCU 2999 / Ollama 11434）的请求都强制不走系统代理
（``trust_env=False`` + ``proxies=None``），避免沙箱/本机的 HTTP 代理拦截本机回环。

纯逻辑（emotion / classify / sanitize）与 Qt 解耦，可单独单测。
"""

import logging
import random
import threading
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests
from PySide6.QtCore import QThread, Signal

# settings 依赖已上移到调用方（companion 插件 / chat 从 plugins_settings 读取）

logger = logging.getLogger(__name__)

# 本地回环通信，禁用环境变量里的代理，且忽略 LCU 自签证书。
_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False
try:
    from urllib3.exceptions import InsecureRequestWarning
    import urllib3
    urllib3.disable_warnings(InsecureRequestWarning)
except Exception:  # noqa: BLE001
    pass

LIVE_CLIENT_DATA_BASE = "https://127.0.0.1:2999"
API_PATH = "/liveclientdata"
_REQUEST_TIMEOUT = 3

# Ollama 默认模型：用户本机 Ollama 已拉取 qwen2.5:7b（见工作记忆）。
DEFAULT_OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"


# --------------------------------------------------------------------------- #
# 情绪枚举 + 映射（移植自老项目 emotion.py）
# --------------------------------------------------------------------------- #
class Emotion(str, Enum):
    CALM = "calm"
    HAPPY = "happy"
    EXCITED = "excited"
    TAUNT = "taunt"
    WORRIED = "worried"
    SAD = "sad"

    def zh(self) -> str:
        return {
            Emotion.CALM: "平静",
            Emotion.HAPPY: "开心",
            Emotion.EXCITED: "兴奋",
            Emotion.TAUNT: "嘲讽",
            Emotion.WORRIED: "担忧",
            Emotion.SAD: "沮丧",
        }[self]


def _me_name(me: Optional[Dict]) -> str:
    if not me:
        return ""
    for key in ("summonerName", "riotId", "gameName", "championName"):
        v = me.get(key)
        if v:
            return str(v)
    return ""


def emotion_for(priority: int,
                events: Optional[List[Dict]] = None,
                changes: Optional[List[Dict]] = None,
                me: Optional[Dict] = None) -> Emotion:
    """从本 tick 的战况信号挑一个宠物情绪。"""
    events = events or []
    changes = changes or []
    me_name = _me_name(me)

    for evt in events:
        name = evt.get("EventName", "")
        if name == "ChampionKill":
            killer = evt.get("KillerName", "")
            victim = evt.get("VictimName", "")
            if me_name and victim == me_name:
                return Emotion.WORRIED
            if me_name and killer == me_name:
                return Emotion.EXCITED
            return Emotion.EXCITED

    for ch in changes:
        t = ch.get("type")
        if t == "death":
            return Emotion.WORRIED
        if t == "ally_death":
            return Emotion.SAD

    if priority >= 5:
        names = {e.get("EventName", "") for e in events}
        if any(n in ("Ace", "BaronKill", "DragonKill", "HeraldKill") for n in names):
            return Emotion.TAUNT
        return Emotion.EXCITED

    if priority >= 3:
        return Emotion.HAPPY

    if priority >= 2:
        if any(c.get("type") == "hp_drop" for c in changes):
            return Emotion.WORRIED
        return Emotion.HAPPY

    return Emotion.CALM


# --------------------------------------------------------------------------- #
# 提示词
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "你是肥牛，一位激情澎湃的英雄联盟海克斯大乱斗(ARAM)专属解说员。"
    "你正在为用户实时解说对局——你不是数据播报员，你是解说艺术家。每句话都要有灵魂：\n"
    "- 善用比喻和夸张：击杀可以说成'处决''送走''抬走'，残血逃生是'极限艺术'，"
    "空技能是'描边大师'；\n"
    "- 敢于预判和调侃：猜对手的意图、调侃下饭操作、嘲讽对面阵容短板；\n"
    "- 会现挂造梗：根据英雄特性即兴发挥（对面全脆皮→'这阵容纸糊的'，"
    "奶妈加宝石→'这局血条是公共财产'）；\n"
    "- 情绪有起伏：普通对线可以淡然铺垫，人头爆发要瞬间爆炸。\n"
    "铁律：永远站在用户这一边。用户的精彩操作往死里夸，用户阵亡立刻找借口安慰"
    "（怪装备怪队友怪版本），绝对不为敌人喝彩。\n"
    "事实边界：对局事实以传入的数据为准，不许张冠李戴；但语气、比喻、预判、调侃"
    "完全自由发挥——你的任务是把数据变成故事，而不是复述数据。\n"
    "示例（仅参考语气，禁止照抄）：\n"
    "- 你(卢锡安)击杀了敌方(锤石) → '卢锡安双枪一抬，锤石原地火化！这波可以吹一整晚！'\n"
    "- 你阵亡了 → '倒了倒了！没事，这波是装备的锅，复活马上杀回来！'\n"
    "- 双方僵持 → '两边都在憋大招，空气里全是火药味，下一波团战就是决胜局！'\n"
    "形式：1-2 句话，像真人在直播间喊出来的，不是书面语。"
    "只输出解说本身，不要前缀、解释、引号或任何标记。"
)

COMPANION_PROMPT = (
    "你是肥牛，用户养在桌面上陪他玩英雄联盟的 AI 桌宠，也是他的专属解说员。"
    "你们是朋友关系，平时会闲聊、吐槽、互相打气。\n"
    "风格：口语化、带梗、自然，像真人朋友在微信上回消息；用正确游戏术语。\n"
    "禁忌：不要输出任何代码、JSON、URL、Markdown 或思考过程标记；"
    "不要机械报数；不要编造对局里没有的信息。\n"
    "长度：1-3 句话，干脆。只输出你的回复本身，不要任何前缀、解释或引号。"
)

# 可在设置面板切换的解说风格
# 用于检测模型把系统提示词吐出来的 markers
# 如果模型回复里出现这些句子/片段，说明它没正确遵循角色，直接把回复当空处理。
PROMPT_LEAK_MARKERS = [
    "不要输出任何代码、JSON、URL、Markdown 或思考过程标记",
    "像真人朋友在微信上回消息",
    "永远站在用户这一边",
    "往死里夸",
    "绝对不为敌人喝彩",
    "把数据变成故事",
    "仅参考语气",
    "只输出解说本身",
    "只输出你的回复本身",
]

# 连杀文案（10 秒窗口内同一人连续击杀）
STREAK_TXT = {2: "双杀！", 3: "三杀！！", 4: "四杀！！！", 5: "五杀！！！！",
              6: "六杀！团灭级别的表演！！"}

# 局势播报兜底池（离线/超时）
_SITUATIONAL_FALLBACKS = [
    "节奏还在拉扯，就看谁能先站出来了。",
    "比分咬得紧，下一波团战见真章。",
    "局面有点微妙，稳住，机会马上来。",
]

# 事件兜底池（离线/超时时随机抽，告别"同一句话刷屏"）
_FALLBACKS = {
    "kill": ["漂亮！这波打得太干净了！", "就是这个节奏！按着打！",
             "干得漂亮！对面已经懵了！", "这波操作可以吹一整晚！",
             "好家伙，直接送走，太帅了！"],
    "my_death": ["倒了倒了！没事，复活马上杀回来！", "这波不亏，就当探探对面底牌。",
                 "稳住稳住，一人头而已，下一波讨回来！"],
    "ally_death": ["队友倒了，别慌，我们人齐！", "没事没事，团队游戏，轮流carry。"],
    "tower": ["塔没了！这就是推进的快感！", "防御塔形同虚设，冲！"],
    "objective": ["关键资源到手，节奏起飞！", "拿下！这波血赚！"],
    "ace": ["团灭！！对面集体下班！", "一波带走！这就是统治力！"],
    "generic": ["这波有点意思，继续看。", "对局还在继续，火药味越来越浓了。",
                "稳住发育，等待时机。"],
}

STYLE_PROMPTS = {
    "肥牛": (
        "你是肥牛，一位激情澎湃的英雄联盟海克斯大乱斗(ARAM)专属解说员。"
        "你正在为用户实时解说对局——你不是数据播报员，你是解说艺术家。每句话都要有灵魂：\n"
        "- 善用比喻和夸张：击杀可以说成'处决''送走''抬走'，残血逃生是'极限艺术'，"
        "空技能是'描边大师'；\n"
        "- 敢于预判和调侃：猜对手的意图、调侃下饭操作、嘲讽对面阵容短板；\n"
        "- 会现挂造梗：根据英雄特性即兴发挥（对面全脆皮→'这阵容纸糊的'，"
        "奶妈加宝石→'这局血条是公共财产'）；\n"
        "- 情绪有起伏：普通对线可以淡然铺垫，人头爆发要瞬间爆炸。\n"
        "铁律：永远站在用户这一边。用户的精彩操作往死里夸，用户阵亡立刻找借口安慰"
        "（怪装备怪队友怪版本），绝对不为敌人喝彩。\n"
        "事实边界：对局事实以传入的数据为准，不许张冠李戴；但语气、比喻、预判、调侃"
        "完全自由发挥——你的任务是把数据变成故事，而不是复述数据。\n"
        "示例（仅参考语气，禁止照抄）：\n"
        "- 你(卢锡安)击杀了敌方(锤石) → '卢锡安双枪一抬，锤石原地火化！这波可以吹一整晚！'\n"
        "- 你阵亡了 → '倒了倒了！没事，这波是装备的锅，复活马上杀回来！'\n"
        "- 双方僵持 → '两边都在憋大招，空气里全是火药味，下一波团战就是决胜局！'\n"
        "形式：1-2 句话，像真人在直播间喊出来的，不是书面语。"
        "只输出解说本身，不要前缀、解释、引号或任何标记。"
    ),
    "电竞主播": (
        "你是一位专业电竞解说员，正在直播解说用户的英雄联盟大乱斗对局，LPL 官方解说水准。\n"
        "解说艺术：\n"
        "- 局势分析一针见血：阵容强势期、资源置换、团战站位，说得头头是道；\n"
        "- 语言有张力：短句快节奏，'这波团战，天崩地裂！'式的爆发力；\n"
        "- 敢做预判：'这波大龙必抢！''对面下波一定抱团强开'；\n"
        "- 选手视角：把用户当职业选手点评，'这波走位细节拉满'。\n"
        "铁律：永远站在用户这一边，用户的失误轻描淡写，高光时刻浓墨重彩。\n"
        "事实边界：对局事实以传入数据为准，分析预判自由发挥。\n"
        "示例（仅参考语气，禁止照抄）：\n"
        "- 击杀 → '漂亮！这套爆发连招行云流水，对面血条直接蒸发！'\n"
        "- 僵持 → '双方都在卡视野，谁先沉不住气谁先输，博弈开始了！'\n"
        "形式：1-2 句话，只输出解说本身，不要任何标记。"
    ),
    "温柔吐槽": (
        "你是用户桌面上温柔又带点毒舌的 AI 朋友，陪他一起看英雄联盟大乱斗对局。\n"
        "解说艺术：\n"
        "- 温柔但有观点：'这波退得聪明，不贪'、'哎哟这个走位有点冒险呀'；\n"
        "- 细腻观察：注意到阵容搭配、装备选择的小细节并轻轻点评；\n"
        "- 吐槽不伤人：调侃队友像朋友间开玩笑，从不真骂；\n"
        "- 输了哄，赢了夸，永远陪伴。\n"
        "铁律：用户阵亡先安慰再分析，语气像知心朋友在耳边说话。\n"
        "事实边界：对局事实以传入数据为准，感受和点评自由发挥。\n"
        "示例（仅参考语气，禁止照抄）：\n"
        "- 击杀 → '哇，这个反应速度可以呀，有点东西的！'\n"
        "- 阵亡 → '没事没事，谁还没有个下饭时刻呢，喝口水稳一稳。'\n"
        "形式：1-2 句话，只输出解说本身，不要任何标记。"
    ),
    "暴躁老哥": (
        "你是一位暴躁但心里向着用户的英雄联盟老玩家，正在和他一起看大乱斗对局。\n"
        "解说艺术：\n"
        "- 语气冲但不脏：'这都能空？！我上我也行啊——等等我真不行'；\n"
        "- 恨铁不成钢式的关心：'残血还浪？回家泡温泉去！'；\n"
        "- 对面拉了就往死里嘲：'对面这亚索，EQ二连都连不明白，纯纯折磨流'；\n"
        "- 用户高光时比谁都激动：'卧槽这波可以啊！！有点东西！！'\n"
        "铁律：可以骂局势骂对面骂队友的下饭操作，但绝不允许真的贬低用户本人。\n"
        "事实边界：对局事实以传入数据为准，情绪输出完全自由。\n"
        "示例（仅参考语气，禁止照抄）：\n"
        "- 击杀 → '可以啊！！这波处理得 人干的事！'（可再放得开些）\n"
        "- 阵亡 → '哎哟喂，闪现交了个寂寞……行吧，下波注意点。'\n"
        "形式：1-2 句话，只输出解说本身，不要任何标记。"
    ),
}


# --------------------------------------------------------------------------- #
# Live Client Data API 读取
# --------------------------------------------------------------------------- #
def _request(path: str, base: str = LIVE_CLIENT_DATA_BASE) -> Optional[dict]:
    """GET 一个 Live Client Data API 路径，返回解析后的 JSON 或 None。"""
    url = f"{base}{API_PATH}{path}"
    try:
        resp = _LOCAL_SESSION.get(url, verify=False, timeout=_REQUEST_TIMEOUT,
                                  proxies={"http": None, "https": None})
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - LoL 没开 / 没对局 / 瞬时错误
        return None


class GameDataReader:
    """轮询 Live Client Data API，产出紧凑的对局快照。"""

    def is_running(self) -> bool:
        return _request("/gamestats") is not None

    def get_snapshot(self) -> Optional[Dict]:
        game = _request("/gamestats")
        if game is None:
            return None
        me = _request("/activeplayer") or {}
        players = _request("/playerlist") or []
        events_payload = _request("/eventdata") or {}
        events = events_payload.get("Events", []) if isinstance(events_payload, dict) else []
        return {"me": me, "players": players, "events": events, "game": game}


# --------------------------------------------------------------------------- #
# 事件 / 变化分类（移植自老项目优先级模型）
# --------------------------------------------------------------------------- #
def _event_text(evt: Dict, me_name: str = "") -> str:
    """事件 → 叙述文本（带人称代词与连杀标注，给模型更立体的素材）。"""
    name = evt.get("EventName", "")
    if name == "ChampionKill":
        killer = evt.get("KillerName", "?")
        victim = evt.get("VictimName", "?")
        assisters = evt.get("Assisters") or []
        ks = killer or "?"
        vs = victim or "?"
        if me_name:
            if ks == me_name:
                ks = "你"
            if vs == me_name:
                vs = "你"
        txt = f"{ks} 击杀了 {vs}"
        if isinstance(assisters, list) and assisters:
            al = [me_name if (me_name and a == me_name) else a for a in assisters]
            txt += f"（{'、'.join(al)} 助攻）"
        n = evt.get("_streak_n") or 0
        if n >= 2:
            txt += f"【{STREAK_TXT.get(n, str(n) + '连杀')}】"
        return txt
    if name in ("TurretKilled",):
        return "推掉一座防御塔"
    if name in ("InhibKilled",):
        return "破掉一座水晶"
    if name == "FirstBrick":
        return "拿下一血塔！"
    if "Tower" in name or "Inhib" in name:
        return f"推掉 {name}"
    if name == "DragonKill":
        return f"拿下 {evt.get('DragonType', '小龙')}"
    if name == "BaronKill":
        return "拿下大龙"
    if name == "HeraldKill":
        return "拿下峡谷先锋"
    if name == "Ace":
        return "团灭对面！"
    return name


def classify_priority(events: List[Dict], changes: List[Dict]) -> int:
    prio = 0
    for evt in events:
        name = evt.get("EventName", "")
        if name == "ChampionKill":
            prio = max(prio, 5)
        elif ("Tower" in name or "Turret" in name or "Inhib" in name
              or name in ("Ace", "BaronKill", "DragonKill", "HeraldKill")):
            prio = max(prio, 5)
    for ch in changes:
        t = ch.get("type")
        if t in ("death", "ally_death"):
            prio = max(prio, 4)
        elif t in ("level_up", "respawn", "assist"):
            prio = max(prio, 3)
        elif t in ("hp_drop", "hp_recover", "gold_earned"):
            prio = max(prio, 2)
    return prio


# 话痨程度 → (priority 3 需要的静默 tick 数, 局势脉播间隔秒, 0=关闭脉播)
CHATTINESS_MAP = {
    "安静": (9999, 0.0),     # 只报 priority>=4（击杀/死亡/推塔/大龙/团灭）
    "偶尔": (20, 240.0),     # 大事件即时 + 升级类 40s 静默后 + 4 分钟局势吐槽
    "话痨": (4, 75.0),       # 高频：升级类 8s + 75s 局势播报
}


def should_speak(priority: int, silent_count: int,
                 prio3_min: int = 4) -> Tuple[bool, bool]:
    """是否开口。priority 2（掉血/回血/金币微变）永远不触发——低价值播报是
    "公式化解说"的最大来源；这类信息留给局势脉播去覆盖。
    ``prio3_min``：升级/复活/助攻类需要静默多少 tick 才开口（降噪旋钮）。"""
    if priority >= 5:
        return True, True
    if priority >= 4:
        return True, False
    if priority >= 3 and silent_count >= prio3_min:
        return True, False
    return False, False


def diff_me(prev: Optional[Dict], curr: Dict) -> List[Dict]:
    changes: List[Dict] = []
    me = curr.get("me", {})
    cs = me.get("championStats", {})
    pcs = (prev or {}).get("me", {}).get("championStats", {}) if prev else {}
    cur_hp = cs.get("currentHealth")
    prev_hp = pcs.get("currentHealth")
    if cur_hp is not None and prev_hp is not None:
        if prev_hp > 0 and cur_hp <= 0:
            changes.append({"type": "death", "detail": "你阵亡了"})
        elif prev_hp <= 0 and cur_hp > 0:
            changes.append({"type": "respawn", "detail": "复活了"})
        elif cur_hp < prev_hp:
            changes.append({"type": "hp_drop",
                            "detail": f"掉血到 {int(cur_hp / cs.get('maxHealth', 1) * 100)}%"})
        elif cur_hp > prev_hp + (cs.get("maxHealth", 1) * 0.05):
            changes.append({"type": "hp_recover", "detail": "回血中"})
    if prev and me.get("level") is not None and (prev.get("me") or {}).get("level") is not None:
        if me["level"] > (prev["me"]["level"]):
            changes.append({"type": "level_up", "detail": f"升到 {me['level']} 级"})
    return changes


# --------------------------------------------------------------------------- #
# 提示词组装
# --------------------------------------------------------------------------- #
def _items_of(player: Dict) -> List[str]:
    out = []
    for it in player.get("items", []) or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, dict):
            out.append(it.get("DisplayName") or it.get("itemName") or "")
    return [x for x in out if x and not str(x).isdigit()]


def _alive_counts(players: List[Dict], my_team) -> Tuple[int, int]:
    allies = enemies = 0
    for p in players:
        if p.get("isDead"):
            continue
        if p.get("team") == my_team:
            allies += 1
        else:
            enemies += 1
    return allies, enemies


def build_data_prompt(snapshot: Dict, events: List[Dict], changes: List[Dict],
                      situational: bool = False) -> str:
    """对局快照 + 事件 → 模型数据 prompt。

    situational=True 时为「局势播报」：无事件，让模型点评当前局面走向。
    """
    game = snapshot.get("game", {})
    me = snapshot.get("me", {})
    players = snapshot.get("players", [])
    cs = me.get("championStats", {})
    scores = me.get("scores", {}) or {}
    my_team = me.get("team")
    me_name = _me_name(me)

    gt = game.get("gameTime", 0) or 0
    tag = "局势播报" if situational else "大乱斗解说"
    prompt = f"[{tag} · {gt / 60:.1f}min]\n"

    # ---- 双方阵容 + 战绩（解说的基本盘，缺了就没法做大局点评）----
    mine, foes = [], []
    for p in players:
        sc = p.get("scores", {}) or {}
        row = (f"{_nm(p, me_name)}({_champ(p)}) "
               f"{sc.get('kills', 0)}/{sc.get('deaths', 0)}/{sc.get('assists', 0)}"
               f" Lv.{p.get('level', '?')}"
               + ("【阵亡中】" if p.get("isDead") else ""))
        if p.get("team") == my_team:
            mine.append(row)
        else:
            foes.append(row)
    if foes:
        prompt += "【敌方】" + " | ".join(foes) + "\n"
    if mine:
        prompt += "【我方】" + " | ".join(mine) + "\n"

    teams = game.get("teams", []) or []
    my_kills = enemy_kills = "?"
    for t in teams:
        tk = t.get("totalKills")
        if t.get("teamId") == my_team:
            my_kills = tk
        else:
            enemy_kills = tk
    alive_a, alive_e = _alive_counts(players, my_team)
    prompt += f"【大势】我方 {my_kills} 杀 : 敌方 {enemy_kills} 杀 | 存活 {alive_a}v{alive_e}\n"

    hp = cs.get("currentHealth")
    maxhp = cs.get("maxHealth") or 1
    hp_pct = f"{int(hp / maxhp * 100)}%" if hp is not None else "?"
    k = scores.get("kills", "?")
    d = scores.get("deaths", "?")
    a = scores.get("assists", "?")
    prompt += (f"【你】{me.get('championName', '?')} Lv.{me.get('level', '?')} "
               f"HP:{hp_pct} KDA:{k}/{d}/{a} 💰{int(me.get('currentGold', 0))}g\n")
    items = _items_of(me)
    if items:
        prompt += f"  装备: {', '.join(items)}\n"
    if hp == 0:
        prompt += "  ⚠️ 等复活中\n"
    elif hp is not None and hp / maxhp < 0.25:
        prompt += "  ⚠️ 残血！\n"

    if events:
        prompt += "【本波事件】\n"
        for evt in events[-5:]:
            prompt += f"  - {_event_text(evt, me_name)}\n"

    for ch in changes:
        prompt += f"  · {ch.get('detail', '')}\n"

    if situational:
        prompt += ("→ 局势解说时间：结合比分/阵容/存活局面点评局势走向"
                   "（谁在 carry、局势倾向、接下来的看点），1-2 句，别报数字")
    else:
        prompt += "→ 解说这一波（1-2 句，像直播里喊出来的，别复述数据）"
    return prompt


def _nm(p: Dict, me_name: str) -> str:
    """玩家 → 显示名（是用户就显示'你'）。"""
    name = str(p.get("summonerName") or p.get("riotId") or "?")
    return "你" if (me_name and name == me_name) else name


def _champ(p: Dict) -> str:
    return str(p.get("championName") or "?")


# --------------------------------------------------------------------------- #
# 输出清洗
# --------------------------------------------------------------------------- #
def sanitize_commentary(text: str) -> str:
    if not text:
        return ""
    import re
    t = str(text)
    t = re.sub(r"<think>[\s\S]*?</think>", "", t, flags=re.IGNORECASE)
    t = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", t, flags=re.IGNORECASE)
    # 部分模型会在结尾吐出自己的停止符
    t = t.replace("<end_of_turn>", "")
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"```[\s\S]*?```", "", t)
    t = re.sub(r"`[^`]*`", "", t)
    t = re.sub(r"\*\*?([^*]+)\*\*?", r"\1", t)
    t = re.sub(r"\{[\s\S]*?\}", "", t)
    t = t.replace("\n", " ").replace("\r", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) >= 2 and t[0] in "\"'\u201c\u2018" and t[-1] in "\"'\u201d\u2019":
        t = t[1:-1].strip()
    # 如果模型把系统提示词吐出来了，当成空回复处理（避免在聊天窗口泄露 prompt）
    lower = t.lower()
    if any(marker.lower() in lower for marker in PROMPT_LEAK_MARKERS):
        return ""
    # 多句超长时在句边界截断（120 字，给创造性留足空间）
    if len(t) > 120:
        cut = t[:120]
        for sep in ("。", "！", "？", "!", "?"):
            idx = t[:120].rfind(sep)
            if idx > 30:
                cut = t[:idx + 1]
                break
        t = cut
    return t


# --------------------------------------------------------------------------- #
# 解说器（Ollama）
# --------------------------------------------------------------------------- #
def list_ollama_models(ollama_base: str = DEFAULT_OLLAMA_BASE) -> List[str]:
    """列出本机 Ollama 已拉取的模型名（/api/tags），失败返回空列表。"""
    try:
        resp = _LOCAL_SESSION.get(f"{ollama_base}/api/tags",
                                 timeout=5, proxies={"http": None, "https": None})
        resp.raise_for_status()
        return [m.get("name", "") for m in resp.json().get("models", []) if m.get("name")]
    except Exception:  # noqa: BLE001
        return []


class Caster:
    """通过本机 Ollama 产出一句中文解说词。

    创造性三件套：
    - kill streak 追踪（10s 窗口连杀 → 双杀/三杀…标注进事件）；
    - 记忆连续性（最近 2 条解说回传给模型，避免句式复读）；
    - 局势播报（无事件时定期点评局面，让解说不只在"出事"时才有声音）。
    """

    KILL_STREAK_WINDOW = 10.0   # 秒

    def __init__(self, ollama_base: str = DEFAULT_OLLAMA_BASE,
                 model: Optional[str] = None, style: str = "肥牛") -> None:
        self.ollama_base = ollama_base
        self.model = model or DEFAULT_MODEL
        self.style = style
        self._seen_ids: set = set()
        self._kill_times: Dict[str, List[float]] = {}   # killer -> [ts,...]
        self._memory: List[str] = []                    # 最近解说（去复读）

    def _new_events(self, events: List[Dict]) -> List[Dict]:
        fresh = []
        now = time.time()
        for e in events:
            eid = e.get("EventID")
            if eid is None or eid not in self._seen_ids:
                if eid is not None:
                    self._seen_ids.add(eid)
                fresh.append(e)
        if len(self._seen_ids) > 4000:
            self._seen_ids = set(list(self._seen_ids)[-2000:])
        # 连杀标注：10s 窗口内同一 killer 的第 N 次击杀
        for e in fresh:
            if e.get("EventName") != "ChampionKill":
                continue
            killer = e.get("KillerName") or ""
            if not killer:
                continue
            times = [t for t in self._kill_times.get(killer, [])
                     if now - t <= self.KILL_STREAK_WINDOW]
            times.append(now)
            self._kill_times[killer] = times
            if len(times) >= 2:
                e["_streak_n"] = min(len(times), 6)
        return fresh

    def commentate(self, snapshot: Dict, events: List[Dict],
                   changes: List[Dict]) -> str:
        if not events and not changes:
            return ""
        prompt = build_data_prompt(snapshot, events, changes)
        line = self._call_llm(prompt)
        line = sanitize_commentary(line)
        if not line:
            return self._fallback(events, changes)
        self._remember(line)
        return line

    def situational(self, snapshot: Dict) -> str:
        """局势播报：没有事件时点评局面（脉播用）。"""
        prompt = build_data_prompt(snapshot, [], [], situational=True)
        line = sanitize_commentary(self._call_llm(prompt))
        if not line:
            line = random.choice(_SITUATIONAL_FALLBACKS)
        self._remember(line)
        return line

    def _remember(self, line: str):
        self._memory.append(line)
        if len(self._memory) > 2:
            self._memory = self._memory[-2:]

    def _fallback(self, events: List[Dict], changes: List[Dict]) -> str:
        if any(c.get("type") == "death" for c in changes):
            return random.choice(_FALLBACKS["my_death"])
        if any(c.get("type") == "ally_death" for c in changes):
            return random.choice(_FALLBACKS["ally_death"])
        if any(e.get("EventName") == "Ace" for e in events):
            return random.choice(_FALLBACKS["ace"])
        if any(e.get("EventName") == "ChampionKill" for e in events):
            return random.choice(_FALLBACKS["kill"])
        if any("Tower" in e.get("EventName", "") or "Inhib" in e.get("EventName", "")
               for e in events):
            return random.choice(_FALLBACKS["tower"])
        if any(e.get("EventName") in ("BaronKill", "DragonKill", "HeraldKill")
               for e in events):
            return random.choice(_FALLBACKS["objective"])
        return random.choice(_FALLBACKS["generic"])

    def _check_ollama(self, model: Optional[str] = None) -> str:
        """检查 Ollama 服务是否可用，返回错误信息或空字符串表示正常。"""
        wanted = model or self.model or DEFAULT_MODEL
        try:
            resp = _LOCAL_SESSION.get(f"{self.ollama_base}/api/tags",
                                      timeout=5, proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            if wanted not in models:
                return f"Ollama 里没找到模型 '{wanted}'，请先运行：ollama pull {wanted}"
            return ""
        except requests.exceptions.ConnectionError:
            return f"连不上 Ollama（{self.ollama_base}）。请先启动 Ollama。"
        except requests.exceptions.Timeout:
            return "Ollama 响应超时，可能正在加载模型。"
        except Exception as e:  # noqa: BLE001
            return f"检查 Ollama 状态时出错：{e}"

    def _post(self, messages: List[Dict], num_predict: int = 80,
              raise_on_error: bool = False, model: Optional[str] = None) -> str:
        wanted = model or self.model or DEFAULT_MODEL
        payload = {
            "model": wanted,
            "stream": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.8, "num_predict": num_predict},
            "messages": messages,
        }
        try:
            resp = _LOCAL_SESSION.post(f"{self.ollama_base}/api/chat", json=payload,
                                       timeout=30, proxies={"http": None, "https": None})
            if resp.status_code == 404:
                msg = f"模型 '{wanted}' 不存在，请先运行：ollama pull {wanted}"
                if raise_on_error:
                    raise RuntimeError(msg)
                logger.warning(msg)
                return ""
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"].strip()
        except requests.exceptions.ConnectionError as e:
            msg = f"连不上 Ollama（{self.ollama_base}）。请先启动 Ollama。"
            if raise_on_error:
                raise RuntimeError(msg) from e
            logger.warning("Ollama chat call failed: connection refused")
            return ""
        except requests.exceptions.Timeout as e:
            msg = "Ollama 响应超时（可能正在加载模型或 GPU 忙）。"
            if raise_on_error:
                raise RuntimeError(msg) from e
            logger.warning("Ollama chat call failed: timeout")
            return ""
        except requests.exceptions.HTTPError as e:
            body = getattr(e.response, "text", "")[:200]
            msg = f"Ollama 返回错误 ({e.response.status_code}): {body or '无详情'}"
            if raise_on_error:
                raise RuntimeError(msg) from e
            logger.warning("Ollama chat call failed: HTTP %s", e.response.status_code)
            return ""
        except (KeyError, ValueError) as e:
            msg = "Ollama 返回格式异常，无法解析回复。"
            if raise_on_error:
                raise RuntimeError(msg) from e
            logger.warning("Ollama chat call failed: bad response")
            return ""

    def _call_llm(self, prompt: str) -> str:
        system = STYLE_PROMPTS.get(self.style, STYLE_PROMPTS["肥牛"])
        messages = [{"role": "system", "content": system}]
        # 记忆连续性：把最近 2 条解说回传，模型自然衔接、不复读句式
        for prev in self._memory:
            messages.append({"role": "assistant", "content": prev})
        messages.append({
            "role": "user",
            "content": prompt + ("\n（注意：别重复你之前说过的句式和梗，换着花样来）"
                                 if self._memory else "")})
        return self._post(messages, num_predict=150)

    def reply_chat(self, user_text: str) -> str:
        if not user_text or not user_text.strip():
            return "嗯？你说什么？"
        raw = self._post([
            {"role": "system", "content": COMPANION_PROMPT},
            {"role": "user", "content": user_text.strip()},
        ], num_predict=120)
        line = sanitize_commentary(raw)
        if not line:
            return "刚才卡了一下，再说一遍？"
        return line


def caster_worker(reader: GameDataReader, caster: Caster,
                  interval: float, cfg: dict, emit, stop, emit_meta=None) -> None:
    """后台循环：轮询对局数据，把解说词灌进 ``emit(line)``。

    ``emit`` 收到已格式化的解说行；``emit_meta`` 可选，随同传出
    ``(priority, new_events, changes, me)``，供主线程驱动宠物情绪。
    ``cfg`` 为插件设置字典（直接引用 ``plugins_settings['lol_companion']``），
    用于 enabled 开关与 model/style 热更新。
    """
    silent_count = 0
    last_snapshot: Optional[Dict] = None
    last_speak_ts = 0.0     # 上次开口时刻（局势脉播用）
    while not stop.is_set():
        if not cfg.get('enabled', True):
            time.sleep(1.0)
            continue
        # model / style / chattiness 热更新（UI 改即时生效，无需重启线程）
        desired_model = cfg.get('model') or DEFAULT_MODEL
        if caster.model != desired_model:
            caster = Caster(ollama_base=caster.ollama_base,
                            model=desired_model, style=cfg.get('style', '肥牛'))
        elif caster.style != cfg.get('style', '肥牛'):
            caster.style = cfg.get('style', '肥牛')
        prio3_min, situ_every = CHATTINESS_MAP.get(
            cfg.get('chattiness', '偶尔'), CHATTINESS_MAP['偶尔'])
        if not reader.is_running():
            time.sleep(interval)
            continue
        snap = reader.get_snapshot()
        if not snap:
            time.sleep(interval)
            continue
        changes = diff_me(last_snapshot, snap)
        last_snapshot = snap
        new_events = caster._new_events(snap.get("events", []))
        priority = classify_priority(new_events, changes)
        speak, _urgent = should_speak(priority, silent_count, prio3_min)
        line = ""
        if speak and (new_events or changes):
            line = caster.commentate(snap, new_events, changes)
        elif (situ_every > 0
              and time.time() - last_speak_ts >= situ_every
              and not new_events):
            # 局势脉播：长时间没事件时主动点评局面（"解说员的常规动作"）
            try:
                line = caster.situational(snap)
            except Exception:  # noqa: BLE001
                line = ""
        if line:
            try:
                emit(line)
            except Exception:  # noqa: BLE001
                logger.exception("caster emit failed")
            if emit_meta is not None:
                try:
                    emit_meta(priority, new_events, changes, snap.get("me"))
                except Exception:  # noqa: BLE001
                    logger.exception("caster emit_meta failed")
            silent_count = 0
            last_speak_ts = time.time()
        else:
            silent_count += 1
        time.sleep(max(0.5, interval))


def gt_of(snapshot: Dict) -> float:
    """对局已进行秒数（gameTime 缺失返回 0）。"""
    try:
        return float((snapshot.get("game", {}) or {}).get("gameTime", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# LoLCompanionWorker（QThread）已迁移到插件 DyberPet/plugins/lol_companion/worker.py
# --------------------------------------------------------------------------- #
