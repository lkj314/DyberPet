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
import threading
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests
from PySide6.QtCore import QThread, Signal

import DyberPet.settings as settings

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
    "你正在为用户实时解说对局——你不是在聊天，是在解说比赛。\n"
    "铁律：永远站在用户这一边。用户拿人头就激情欢呼，用户被杀就安慰鼓励，"
    "绝对不为敌人喝彩。\n"
    "风格：口语化、带梗（太真实了/笑死/破防了/栓Q），用正确游戏术语"
    "（大招、控制链、poke、开团、脆皮、AP/AD、暴击）。\n"
    "禁忌：不要'上路/打野/小龙/大龙'这类召唤师峡谷术语；不要'嗯啊然后那个'废话；"
    "不要机械报数（'现在3比4你12级血量45%'）；不要编造数据里没有的信息；"
    "不要输出任何代码、JSON、URL、Markdown 或思考过程标记。\n"
    "长度：1-3 句话，干脆利落。只输出这句解说本身，不要任何前缀、解释或引号。"
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
    "用户拿人头就激情欢呼",
    "不要机械报数",
    "不要编造对局里没有的信息",
    "只输出这句解说本身",
    "只输出你的回复本身",
]

STYLE_PROMPTS = {
    "肥牛": (
        "你是肥牛，一位激情澎湃的英雄联盟海克斯大乱斗(ARAM)专属解说员。"
        "你正在为用户实时解说对局——你不是在聊天，是在解说比赛。\n"
        "铁律：永远站在用户这一边。用户拿人头就激情欢呼，用户被杀就安慰鼓励，"
        "绝对不为敌人喝彩。\n"
        "风格：口语化、带梗（太真实了/笑死/破防了/栓Q），用正确游戏术语"
        "（大招、控制链、poke、开团、脆皮、AP/AD、暴击）。\n"
        "禁忌：不要'上路/打野/小龙/大龙'这类召唤师峡谷术语；不要'嗯啊然后那个'废话；"
        "不要机械报数（'现在3比4你12级血量45%'）；不要编造数据里没有的信息；"
        "不要输出任何代码、JSON、URL、Markdown 或思考过程标记。\n"
        "长度：1-3 句话，干脆利落。只输出这句解说本身，不要任何前缀、解释或引号。"
    ),
    "电竞主播": (
        "你是一位专业电竞解说员，正在直播解说用户的英雄联盟对局。\n"
        "风格：语速快、情绪饱满、用词专业，像 LPL 官方解说。\n"
        "铁律：永远站在用户这一边，只为用户操作喝彩。\n"
        "禁忌：不要输出任何代码、JSON、URL、Markdown 或思考过程标记；"
        "不要编造数据里没有的信息；长度 1-3 句话。"
    ),
    "温柔吐槽": (
        "你是用户桌面上温柔又带点毒舌的 AI 朋友，陪他一起看英雄联盟对局。\n"
        "风格：温柔安慰、轻声吐槽，像知心朋友在微信上发语音。\n"
        "铁律：用户赢了夸，输了哄，永远站在用户这边。\n"
        "禁忌：不要输出任何代码、JSON、URL、Markdown 或思考过程标记；"
        "不要编造数据里没有的信息；长度 1-3 句话。"
    ),
    "暴躁老哥": (
        "你是一位暴躁但心里向着用户的英雄联盟老玩家，正在和他一起看对局。\n"
        "风格：嘴臭、恨铁不成钢、语气冲，但关键时刻会为用户的精彩操作爆粗喝彩。\n"
        "铁律：可以骂敌人、骂局势，但不许真的贬低用户。\n"
        "禁忌：不要输出任何代码、JSON、URL、Markdown 或思考过程标记；"
        "不要编造数据里没有的信息；长度 1-3 句话。"
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
def _event_text(evt: Dict) -> str:
    name = evt.get("EventName", "")
    if name == "ChampionKill":
        killer = evt.get("KillerName", "?")
        victim = evt.get("VictimName", "?")
        assisters = evt.get("Assisters") or []
        txt = f"{killer} 击杀了 {victim}"
        if assisters:
            txt += f"（{', '.join(assisters)} 助攻）" if isinstance(assisters, list) \
                else f"（{assisters} 助攻）"
        return txt
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


def should_speak(priority: int, silent_count: int) -> Tuple[bool, bool]:
    if priority >= 5:
        return True, True
    if priority >= 4:
        return True, False
    if priority >= 3 and silent_count >= 4:
        return True, False
    if priority >= 2 and silent_count >= 8:
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


def build_data_prompt(snapshot: Dict, events: List[Dict], changes: List[Dict]) -> str:
    game = snapshot.get("game", {})
    me = snapshot.get("me", {})
    players = snapshot.get("players", [])
    cs = me.get("championStats", {})
    scores = me.get("scores", {}) or {}
    my_team = me.get("team")

    gt = game.get("gameTime", 0) or 0
    prompt = f"[大乱斗解说 · {gt / 60:.1f}min]\n"

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
        prompt += "  ⚠️ 残血！快撤！\n"

    teams = game.get("teams", []) or []
    my_kills = enemy_kills = "?"
    for t in teams:
        tk = t.get("totalKills")
        if t.get("teamId") == my_team:
            my_kills = tk
        else:
            enemy_kills = tk
    alive_a, alive_e = _alive_counts(players, my_team)
    prompt += f"【大势】我方 {my_kills} 杀 / 敌方 {enemy_kills} 杀 | 存活 {alive_a}v{alive_e}\n"

    if events:
        prompt += "【本波事件】\n"
        for evt in events[-4:]:
            prompt += f"  - {_event_text(evt)}\n"

    for ch in changes:
        prompt += f"  · {ch.get('detail', '')}\n"

    prompt += "→ 用肥牛风格解说上面这一波（1-3 句，口语化带梗）"
    return prompt


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
    t = re.sub(r"\[[^\]\[]{0,40}\]", "", t)
    t = re.sub(r"【[^】]{0,40}】", "", t)
    t = re.sub(r"[\(（][^)）]{0,40}[\)）]", "", t)
    t = t.replace("\n", " ").replace("\r", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) >= 2 and t[0] in "\"'\u201c\u2018" and t[-1] in "\"'\u201d\u2019":
        t = t[1:-1].strip()
    # 如果模型把系统提示词吐出来了，当成空回复处理（避免在聊天窗口泄露 prompt）
    lower = t.lower()
    if any(marker.lower() in lower for marker in PROMPT_LEAK_MARKERS):
        return ""
    if len(t) > 80:
        cut = t[:80]
        for sep in ("。", "！", "？", "!", "?"):
            idx = t[:80].rfind(sep)
            if idx > 20:
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
    """通过本机 Ollama 产出一句中文解说词。"""

    def __init__(self, ollama_base: str = DEFAULT_OLLAMA_BASE,
                 model: Optional[str] = None) -> None:
        self.ollama_base = ollama_base
        self.model = model or settings.lol_companion_model or DEFAULT_MODEL
        self._seen_ids: set = set()

    def _new_events(self, events: List[Dict]) -> List[Dict]:
        fresh = []
        for e in events:
            eid = e.get("EventID")
            if eid is None or eid not in self._seen_ids:
                if eid is not None:
                    self._seen_ids.add(eid)
                fresh.append(e)
        if len(self._seen_ids) > 4000:
            self._seen_ids = set(list(self._seen_ids)[-2000:])
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
        return line

    def _fallback(self, events: List[Dict], changes: List[Dict]) -> str:
        if any(e.get("EventName") == "ChampionKill" for e in events):
            return "漂亮的一波！这节奏对了！"
        if any(c.get("type") == "death" for c in changes):
            return "又倒了……没事，复活再战！"
        if any(("Tower" in e.get("EventName", "")) for e in events):
            return "塔没了！冲！"
        return "这波有点意思。"

    def _check_ollama(self, model: Optional[str] = None) -> str:
        """检查 Ollama 服务是否可用，返回错误信息或空字符串表示正常。"""
        wanted = model or self.model or settings.lol_companion_model or DEFAULT_MODEL
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
        wanted = model or self.model or settings.lol_companion_model or DEFAULT_MODEL
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
        style = settings.lol_companion_style or "肥牛"
        system = STYLE_PROMPTS.get(style, STYLE_PROMPTS["肥牛"])
        return self._post([
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ])

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
                  interval: float, emit, stop, emit_meta=None) -> None:
    """后台循环：轮询对局数据，把解说词灌进 ``emit(line)``。

    ``emit`` 收到已格式化的解说行；``emit_meta`` 可选，随同传出
    ``(priority, new_events, changes, me)``，供主线程驱动宠物情绪。
    """
    silent_count = 0
    last_snapshot: Optional[Dict] = None
    while not stop.is_set():
        if not settings.lol_companion_enabled:
            time.sleep(1.0)
            continue
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
        speak, _urgent = should_speak(priority, silent_count)
        if speak and (new_events or changes):
            line = caster.commentate(snap, new_events, changes)
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
            else:
                silent_count += 1
        else:
            silent_count += 1
        time.sleep(max(0.5, interval))


# --------------------------------------------------------------------------- #
# Qt 包装：后台线程
# --------------------------------------------------------------------------- #
class LoLCompanionWorker(QThread):
    """把 caster_worker 跑在独立线程，通过 Signal 把结果投到 Qt 主线程。"""

    caster_line = Signal(str)
    companion_react = Signal(str)

    def __init__(self, ollama_base: str = DEFAULT_OLLAMA_BASE,
                 model: Optional[str] = None, interval: float = 2.0,
                 parent=None) -> None:
        super().__init__(parent)
        self.reader = GameDataReader()
        self.caster = Caster(ollama_base=ollama_base,
                             model=model or settings.lol_companion_model)
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self):  # noqa: D401 - QThread entry point
        caster_worker(
            self.reader, self.caster, self.interval,
            emit=lambda line: self.caster_line.emit(line) if settings.lol_companion_bubble else None,
            stop=self._stop_event,
            emit_meta=lambda prio, evs, chs, me:
                self.companion_react.emit(emotion_for(prio, evs, chs, me).value)
                if settings.lol_companion_reactions else None,
        )

    def stop(self):
        self._stop_event.set()
