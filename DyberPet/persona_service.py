# coding:utf-8
"""修仙者人设服务（统一出口）——四层拼装 + 四种输出模式。

设计文档：《桌宠修仙者人设系统设计.md》

四层结构：
- L0 核心人设 / L1 境界人格：persona.json（出厂默认 DyberPet/persona.json；
  用户可在 CONFIGDIR/data/persona.json 覆盖同名文件）
- L2 实时状态：每次请求从 cultivation_service 实时读取（境界/状态/心情），
  绝不写死
- L3 记忆片段：CONFIGDIR/data/persona_memories.json，检索 ≤3 条
  （关键词重合 + 时间衰减，退化时取最近几条）

输出模式（长度卡死，防止下个棋来两百字感言）：
- quip         下棋/打牌吐槽  ≤15 字
- narrate      历练见闻       ~80 字
- chat         日常闲聊       长短随意
- breakthrough 突破感言       简短有仪式感

铁律：
- 插件只传场景参数（mode + user_input），绝不自己写人设——
  改设定只改 persona.json，一处生效全局。
- system prompt ≤ 400 token（约 620 汉字）：超预算先砍 L3 记忆。
- LLM 只表达感受，绝不念数值、绝不泄露隐藏信息（forbidden 禁令）。
- 仅 applicable_pets 中的角色启用人设；其他角色 available()=False，
  chat() 返回 None，调用方回退各自预设台词池。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from typing import Iterable, List, Optional, Set

try:
    from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
except Exception:  # noqa: BLE001
    DEFAULT_OLLAMA_BASE = 'http://localhost:11434'

# system prompt 字符预算（qwen 系中文约 1.5 字符/token，620 字 ≈ 400 token）
SYSTEM_CHAR_BUDGET = 620
MEMORY_MAX = 200                   # 记忆条数上限（超出淘汰最旧）
MEMORY_RECALL_K = 3                # 每次检索最多带几条
MEMORY_DECAY_HALF_LIFE = 30.0      # 记忆半衰期（天）

# 输出模式约束（设计文档 §4.2 原文）
MODE_CONSTRAINT = {
    'quip':         '只输出一句话，不超过15个字，口语化，不要解释。',
    'talisman':     '写一张外出历练的传讯符：45字以内，第一人称，报近况但绝不透露此行结果。',
    'narrate':      '写一段80字以内的第一人称见闻，有画面感，不要报数字。',
    'chat':         '自然对话，长短随意，保持人设口吻。',
    'breakthrough': '一句突破感言，简短，有仪式感，体现当前境界的气度。',
    'tale':         '写一段200字以内的第一人称归来讲述，有画面感，不报具体数字。',
}
# 模式 → 生成长度上限（num_predict；给足余量防截断又不浪费算力）
NUM_PREDICT = {'quip': 48, 'narrate': 200, 'chat': 320, 'breakthrough': 96,
               'talisman': 96, 'tale': 420}

# 古风浓度三档（设计文档 §六；默认「适中」）
STYLE_LINES = {
    '清淡': '用现代汉语为主，偶带古语词，自然如常人说话。',
    '适中': '半文半白，简洁好读。',
    '浓郁': '文言为主，古雅庄重，可稍用辞藻。',
}

_THINK_RE = re.compile(r'<think>.*?</think>', re.S | re.I)
_QUOTE_RE = re.compile(r'^[「"“」\s]+|[」"”\s]+$')


def _clean(text: str) -> str:
    """LLM 输出清洗：去思考段、去首尾引号空白。"""
    text = _THINK_RE.sub('', text)
    if '</think>' in text:                      # 未闭合思考段取后半
        text = text.split('</think>', 1)[1]
    return _QUOTE_RE.sub('', text.strip())


def _grams(text: str) -> Set[str]:
    """简易 2-gram 分词（免 jieba 依赖），用于记忆关键词重合。"""
    s = re.sub(r'[\s，。！？、,.!?:：;；()（）\[\]{}"\']', '', str(text))
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


class PersonaService:
    """人设统一出口。线程安全；Ollama 调用为同步阻塞——调用方放后台线程。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._mem_lock = threading.Lock()
        self._memories: List[dict] = []
        self._core = None                        # cultivation core 懒加载
        self.reload()

    # ------------------------------------------------------------------ #
    # 配置加载
    # ------------------------------------------------------------------ #
    def reload(self):
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        conf = self._load_json(os.path.join(pkg_dir, 'persona.json')) or {}
        try:                                     # 用户覆盖层
            import DyberPet.settings as settings
            user_path = os.path.join(settings.CONFIGDIR, 'data', 'persona.json')
            user = self._load_json(user_path)
            if user:
                merged = dict(conf)
                merged.update(user)              # 顶层键覆盖
                conf = merged
        except Exception:  # noqa: BLE001
            pass
        with self._lock:
            self.conf = conf
            self.L0 = conf.get('L0', {})
            self._style_level = conf.get('style_level', '适中')
            self._l1 = conf.get('L1', [])
            self._applicable = set(conf.get('applicable_pets', ['韩立']))
            self._l1_by_realm = {}
            for item in self._l1:
                for r in item.get('realms', []):
                    self._l1_by_realm[r] = item.get('style', '')
        self._load_memories()

    @staticmethod
    def _load_json(path: str) -> Optional[dict]:
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------ #
    # 角色守卫：非修仙人设角色返回 False，调用方回退预设台词
    # ------------------------------------------------------------------ #
    def available(self) -> bool:
        try:
            import DyberPet.settings as settings
            return getattr(settings, 'petname', '') in self._applicable
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # L1 境界人格
    # ------------------------------------------------------------------ #
    def l1_style(self, realm_idx: int) -> str:
        """阶索引(0..39+) → 该境界的语气描述。"""
        try:
            from DyberPet.cultivation_service import REALMS
            realm = REALMS[min(max(realm_idx, 0) // 4, len(REALMS) - 1)]
            return self._l1_by_realm.get(realm, '')
        except Exception:  # noqa: BLE001
            return ''

    # ------------------------------------------------------------------ #
    # L2 实时状态（从 cultivation_service 读，不写死）
    # ------------------------------------------------------------------ #
    def _core_or_none(self):
        if self._core is not None:
            return self._core
        try:
            from DyberPet.cultivation_service import get_core
            self._core = get_core()
        except Exception:  # noqa: BLE001
            self._core = None
        return self._core

    def _mood(self, core, now: float) -> str:
        """心情：由最近事件/互动推断（不新增状态字段，纯读）。"""
        if core is None:
            return '平静'
        log = getattr(core, 'log', []) or []
        if log:
            last = log[-1]
            if now - float(last.get('t', 0)) < 600:
                mood = {'breakthrough': '意气风发（刚刚突破）',
                        'epiphany': '欣喜（若有所悟）',
                        'break_fail': '有些沮丧（突破受挫）',
                        'pill': '药力温养，神清气爽',
                        'exp_gain': '战意犹存（刚斗过法）',
                        'alchemy_done': '颇为自得（丹炉开炉）'}.get(last.get('kind'))
                if mood:
                    return mood
        if getattr(core, 'dual_on', False):
            return '入定双修，心无旁骛'
        touch = float(getattr(core, 'last_touch', 0) or 0)
        if touch and now - touch <= 120:
            return '愉悦（刚被道友抚慰）'
        if touch and now - touch > 7200:
            return '有些寂寥（久无人问）'
        return '平静'

    def l2_status(self, realm_idx: Optional[int] = None) -> str:
        core = self._core_or_none()
        now = time.time()
        if realm_idx is None:
            realm_idx = core.stage() if core is not None else 0
        try:
            from DyberPet.cultivation_service import stage_name
            stage = stage_name(int(realm_idx))
        except Exception:  # noqa: BLE001
            stage = '炼气 · 初期'
        conds = []
        if core is not None:
            if now < float(getattr(core, 'weak_until', 0) or 0):
                conds.append('带伤（突破反噬）')
            if getattr(core, 'dual_on', False):
                conds.append('闭关双修中')
            if now < float(getattr(core, 'buff_until', 0) or 0):
                conds.append('药力加持')
        if not conds:
            conds.append('无伤')
        return f'当前境界：{stage}｜状态：{"、".join(conds)}｜心情：{self._mood(core, now)}'

    # ------------------------------------------------------------------ #
    # L3 记忆（历练志 / 事件日志；关键词重合 + 时间衰减）
    # ------------------------------------------------------------------ #
    def _mem_path(self) -> str:
        import DyberPet.settings as settings
        return os.path.join(settings.CONFIGDIR, 'data', 'persona_memories.json')

    def _load_memories(self):
        data = self._load_json(self._mem_path())
        with self._mem_lock:
            self._memories = data if isinstance(data, list) else []

    def add_memory(self, text: str, tags: Optional[List[str]] = None):
        """写入一条记忆（去重、限长、落盘）。任何线程可调。"""
        text = str(text).strip()
        if not text:
            return
        with self._mem_lock:
            if any(m.get('text') == text for m in self._memories[-20:]):
                return
            self._memories.append({'t': time.time(), 'text': text,
                                   'tags': list(tags or [])})
            if len(self._memories) > MEMORY_MAX:
                self._memories = self._memories[-MEMORY_MAX:]
            snapshot = list(self._memories)
        try:
            path = self._mem_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(snapshot, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            print(f'[persona] memory save failed: {e!r}')

    def on_breakthrough(self, new_realm: str):
        """突破时：记忆一笔（L1 语气查表即得，无需存状态）。"""
        self.add_memory(f'突破至{new_realm}', tags=['breakthrough'])

    def _retrieve(self, keywords: Set[str], k: int = MEMORY_RECALL_K) -> List[str]:
        now = time.time()
        with self._mem_lock:
            items = list(self._memories)
        if not items:
            return []
        scored = []
        for m in items:
            age = max(0.0, now - float(m.get('t', 0)))
            decay = 0.5 ** (age / 86400.0 / MEMORY_DECAY_HALF_LIFE)
            overlap = len(keywords & _grams(m.get('text', ''))) if keywords else 0
            scored.append((overlap * 2.0 + decay, m.get('text', '')))
        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored[:k]]

    # ------------------------------------------------------------------ #
    # system prompt 拼装（≤ 预算；超了先砍 L3）
    # ------------------------------------------------------------------ #
    def build_prompt(self, mode: str, *, include_memories: bool = True,
                     extra_context: Optional[dict] = None,
                     realm_idx: Optional[int] = None,
                     memories: Optional[List[str]] = None) -> str:
        """拼装 system prompt，可直接喂给 Ollama。人设不适用时返回 ''。"""
        if not self.available():
            return ''
        if mode not in MODE_CONSTRAINT:
            mode = 'chat'
        with self._lock:
            L0 = dict(self.L0)
            style_line = STYLE_LINES.get(self._style_level,
                                         STYLE_LINES['适中'])
        if not L0:
            return ''

        parts = [
            f"你是修仙桌宠「{L0.get('name', '修士')}」。{L0.get('origin', '')}。",
            f"自称「{L0.get('self_title', '我')}」，"
            f"称对方「{L0.get('master_title', '道友')}」。"
            f"性格：{'、'.join(L0.get('personality', []))}。"
            f"说话风格：{L0.get('speech_style', '')}。{style_line}",
        ]
        base = '\n'.join(parts)
        facts = '；'.join(L0.get('known_facts', []))
        forbid = '；'.join(L0.get('forbidden', []))
        fact_part = (f"你必知的过往：{facts}。\n"
                     f"绝对禁止：{forbid}。"
                     f"不知道的事就说「{L0.get('unknown_response', '记不清了')}」。")
        l1 = self.l1_style(realm_idx if realm_idx is not None
                           else (self._core_or_none().stage()
                                 if self._core_or_none() is not None else 0))
        l1_part = f"当前语气：{l1}。" if l1 else ''
        l2_part = self.l2_status(realm_idx=realm_idx)
        mode_part = MODE_CONSTRAINT[mode]

        # 预算内尽量塞：L0+L1+L2+mode 必保；L3 记忆按预算裁
        system = '\n'.join([base, fact_part, l1_part, l2_part, mode_part])
        if include_memories and len(system) < SYSTEM_CHAR_BUDGET:
            if memories is None:
                ecs = str((extra_context or {}).get('keywords', ''))
                memories = self._retrieve(_grams(ecs))
            for i, m in enumerate(memories or []):
                cand = ('相关记忆：' if i == 0 else '') + \
                       (f'- {m}' if i > 0 else m)
                cand = (system + '\n相关记忆：' + '；'.join(
                    (memories or [])[:i + 1]) + '。')
                if len(cand) <= SYSTEM_CHAR_BUDGET:
                    system = cand
                else:
                    break
        return system

    # ------------------------------------------------------------------ #
    # Ollama 调用（同步；调用方放后台线程）
    # ------------------------------------------------------------------ #
    def chat(self, user_input: str, mode: str = 'chat', *,
             include_memories: bool = True,
             extra_context: Optional[dict] = None,
             realm_idx: Optional[int] = None,
             model: Optional[str] = None,
             ollama_base: Optional[str] = None,
             timeout: int = 40) -> Optional[str]:
        """拼 prompt + 调 Ollama + 清洗，返回回复文本；失败/不适用返回 None。"""
        if not self.available():
            return None
        kws = _grams(user_input)
        for v in (extra_context or {}).values():
            kws |= _grams(v)
        mems = (self._retrieve(kws) if include_memories else [])
        system = self.build_prompt(mode, include_memories=False,
                                   extra_context=extra_context,
                                   realm_idx=realm_idx, memories=mems)
        if not system:
            return None
        text = self._generate(system, user_input, model=model or self._default_model(),
                              num_predict=NUM_PREDICT.get(mode, 200),
                              ollama_base=ollama_base, timeout=timeout)
        return _clean(text) if text else None

    def _default_model(self) -> str:
        """默认模型跟主程序 chat 设置走（人设与聊天同一个"人"，同一张嘴）。"""
        try:
            import DyberPet.settings as settings
            m = getattr(settings, 'chat_model', '')
            if m:
                return m
        except Exception:  # noqa: BLE001
            pass
        return 'qwen2.5:7b'

    def _generate(self, system: str, user: str, model: Optional[str] = None,
                  num_predict: int = 200, ollama_base: Optional[str] = None,
                  timeout: int = 40) -> Optional[str]:
        base = (ollama_base or DEFAULT_OLLAMA_BASE).rstrip('/')
        payload = json.dumps({
            'model': model or self._default_model(),
            'system': system,
            'prompt': user,
            'stream': False,
            'options': {'num_predict': num_predict},
        }).encode('utf-8')
        req = urllib.request.Request(
            f'{base}/api/generate', data=payload,
            headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:  # noqa: BLE001  网络失败/超时/HTTP 错误一律 None
            print(f'[persona] ollama call failed: {e!r}')
            return None
        return data.get('response')


# ---------------------------------------------------------------------- #
# 模块级单例
# ---------------------------------------------------------------------- #
_PERSONA: Optional[PersonaService] = None
_PERSONA_LOCK = threading.Lock()


def get_persona() -> PersonaService:
    global _PERSONA
    if _PERSONA is None:
        with _PERSONA_LOCK:
            if _PERSONA is None:
                _PERSONA = PersonaService()
    return _PERSONA


def add_memory(text: str, tags: Optional[Iterable[str]] = None):
    """便捷入口：任何插件一行写入历练记忆（人设不适用时静默跳过）。"""
    try:
        p = get_persona()
        if p.available():
            p.add_memory(text, list(tags or []))
    except Exception as e:  # noqa: BLE001
        print(f'[persona] add_memory failed: {e!r}')
