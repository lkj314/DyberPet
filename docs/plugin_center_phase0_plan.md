# AI 桌宠插件中心 — Phase 0 实施方案（审批稿）

> 状态：待敲定。敲定后直接按本方案实施。
> 配套蓝图：`docs/plugin_center_blueprint.md`
> 审阅结论见原对话。本方案是蓝图 Phase 0 的**精确落地版**，包含两处相对蓝图的必要偏差（第 2 节）。

---

## 1. 目标与原则

- **验证插件系统架构是否站得住**：PluginManager（发现/加载/生命周期/崩溃守护）+ PetAPI 门面 + 声明式设置落盘。
- **把 LoL 解说插件迁入系统**：删掉 `run_DyberPet.py` / `settings.py` / `pet_chat.py` / `BasicSettingUI.py` 里对 LoL 的硬编码。
- **最小化改动、每步可回滚**：用 `git` 提交点做回滚锚；核心文件（`DyberPet.py`）尽量不动。

---

## 2. 相对蓝图的两处必要偏差（请重点审阅）

**偏差 A — 设置 UI 不重做「schema 自动渲染」，改「切数据源」**
蓝图 Phase 0 写的是"删掉 `BasicSettingUI.py` 写死的 `CompanionGroup`，改由 schema 自动生成"。
经补查，`BasicSettingUI.py` 的 `SettingInterface` 框架目前没有"动态注入卡片"的能力，要做自动渲染需要重构其卡框架（类型→卡映射、双向绑定、动态布局）。这会把 Phase 1 的核心工作量提前塞进 Phase 0，放大风险。
→ **Phase 0 改为**：保留 `CompanionGroup` 这 6 张卡，但把它们的读写数据源从旧全局变量 `settings.lol_companion_*` 切到 `settings.plugins_settings['lol_companion']`。这样 Phase 0 就能完整验证"插件设置通过 `plugins_settings` 落盘 → PetAPI 读写 → UI 可改"全链路，足以证明架构成立。**schema 自动渲染推迟到 Phase 1**（届时 `CompanionGroup` 被彻底替换为动态渲染）。
> 此偏差为推荐方案；若你希望 Phase 0 一步到位做自动渲染，范围/风险见第 7 节备选。

**偏差 B — 共享 LLM 核心提升为 `DyberPet/llm_core.py`**
审阅发现 `pet_chat.py` 复用了 `lol_companion.py` 的 `Caster` / `COMPANION_PROMPT` / `sanitize_commentary`（不仅是 LoL 专属）。把这些"纯 LLM 逻辑"塞进插件目录会导致 chat 在插件未启用时 import 失败。
→ **Phase 0 把 LoL 与 chat 共用的纯逻辑（Caster / GameDataReader / caster_worker / 各 prompt / sanitize / list_ollama_models / emotion_for）提升为内核共享模块 `DyberPet/llm_core.py`**；插件只封装"LoL 轮询 + 驱动 pet 反应"这个**行为**（`LoLCompanionWorker` + `Plugin` 子类）。chat 继续从 `llm_core` 导入，零受影响。

---

## 3. 文件清单

### 新建
| 文件 | 作用 |
|---|---|
| `DyberPet/llm_core.py` | 从 `lol_companion.py` 平移的共享纯 LLM 逻辑（settings-free） |
| `DyberPet/plugin_system/__init__.py` | 包标记 |
| `DyberPet/plugin_system/base.py` | `Plugin` 基类 + 生命周期钩子 |
| `DyberPet/plugin_system/api.py` | `PetAPI` 门面 + `_Events` + `_Settings` |
| `DyberPet/plugin_system/manager.py` | `PluginManager`：发现/加载/启停/崩溃守护 |
| `DyberPet/plugins/__init__.py` | 插件包标记 |
| `DyberPet/plugins/lol_companion/__init__.py` | 插件包标记 |
| `DyberPet/plugins/lol_companion/plugin.json` | 清单 + `settings_schema` |
| `DyberPet/plugins/lol_companion/main.py` | `LoLCompanionPlugin(Plugin)` |
| `DyberPet/plugins/lol_companion/worker.py` | `LoLCompanionWorker(QThread)`（从 llm_core 导入逻辑）|

### 修改
| 文件 | 改动要点 |
|---|---|
| `DyberPet/settings.py` | 新增 `plugins_settings` 全局 + 落盘/迁移；删除 `lol_companion_*` 5 个旧全局变量 |
| `DyberPet/pet_chat.py` | import 改 `llm_core`；2 处设置读取改 `plugins_settings['lol_companion']` |
| `DyberPet/DyberSettings/BasicSettingUI.py` | `CompanionGroup` 6 张卡数据源切到 `plugins_settings`；import 改 `llm_core`；Enable 卡运行期启停插件 |
| `run_DyberPet.py` | 删 LoL 硬编码；接 `PluginManager` |

### 删除
| 文件 | 说明 |
|---|---|
| `DyberPet/lol_companion.py` | 内容已迁到 `llm_core.py` + 插件目录 |

---

## 4. 精确改动

### 4.1 `DyberPet/llm_core.py`（新建，从 `lol_companion.py` 平移）
平移原文件第 1–612 行全部逻辑（`Emotion` / `emotion_for` / `classify_priority` / `diff_me` / `build_data_prompt` / `sanitize_commentary` / `Caster` / `GameDataReader` / `caster_worker` / 各 prompt / `list_ollama_models` / `_LOCAL_SESSION`）。
**关键改造（settings-free）**：
- `Caster.__init__(self, ollama_base, model=None, style='肥牛')`：构造时收 `model` 与 `style`，**不再读 `settings.lol_companion_*`**。
- `Caster._call_llm`：`style = self.style`（移除去 `settings.lol_companion_style`）。
- `Caster._check_ollama` / `_post`：`wanted = model or self.model`（去掉 `settings.lol_companion_model` 兜底，由调用方保证传入）。
- `caster_worker(reader, caster, interval, cfg, emit, stop, emit_meta)`：新增 `cfg: dict` 参数；循环内用 `cfg.get('enabled')` / `cfg.get('bubble')` / `cfg.get('reactions')` 替代原 `settings.lol_companion_enabled/bubble/reactions`；`cfg.get('model')` 变化时重建 `caster`（保留原有"线程常驻、改模型即时响应"语义）。
- 保留 `list_ollama_models`（供设置 UI 刷新模型列表）。
- 顶部 `import DyberPet.settings as settings` 移除（不再依赖）。

### 4.2 `DyberPet/plugin_system/base.py`
```python
from typing import Optional
from .api import PetAPI

class Plugin:
    """所有插件的基类。插件只通过 api 与宿主交互，禁止 import 内部模块。"""
    plugin_id: str = ""
    manifest: dict = {}

    def __init__(self, api: PetAPI):
        self.api = api
        self.worker = None

    def on_load(self):     """加载、设置就绪后调用一次（注册事件/菜单）。""" pass
    def on_enable(self):   """启用/重启后调用，启动后台任务。""" pass
    def on_disable(self):  """禁用/退出前调用，停止并清理。""" pass
    def on_unload(self):   """卸载前调用。""" pass
```

### 4.3 `DyberPet/plugin_system/api.py`
```python
from PySide6.QtCore import QObject
import DyberPet.settings as settings

class _PetFacade:
    def __init__(self, widget): self._w = widget
    def say(self, text):
        if text: self._w.show_speech(str(text))
    def react(self, emotion):      self._w.react(str(emotion))
    def notify(self, t, m):        self._w.register_notification(t, m)
    def bubble(self, d: dict):     self._w.register_bubbleText(d)
    def use_item(self, name):      self._w.use_item(name)
    def add_menu(self, action):
        if hasattr(self._w, 'addContextMenuAction'):
            self._w.addContextMenuAction(action)

class _Events:
    """桥接 PetWidget / App 已有信号（更多信号 Phase 2 补全）。"""
    def __init__(self, widget, app):
        self.hp_changed   = widget.hp_updated
        self.fv_changed   = widget.fv_updated
        self.pet_changed  = widget.change_note
        self.midnight     = app.date_changed

class _Settings:
    def __init__(self, plugin_id): self._pid = plugin_id
    def get(self, key, default=None):
        return settings.plugins_settings.get(self._pid, {}).get(key, default)
    def set(self, key, value, save=True):
        settings.plugins_settings.setdefault(self._pid, {})[key] = value
        if save: settings.save_settings()
    def all(self):
        return settings.plugins_settings.get(self._pid, {})

class PetAPI:
    def __init__(self, widget, app, plugin_id):
        self.pet = _PetFacade(widget)
        self.events = _Events(widget, app)
        self.settings = _Settings(plugin_id)
        self.app = app
```
> 门面目标方法均已实测存在：`register_notification`(1390) / `register_bubbleText`(1394) / `show_speech`(1403) / `react`(1409) / `register_accessory`(1458) / `use_item`(1561)。

### 4.4 `DyberPet/plugin_system/manager.py`（核心）
职责：`discover()` 扫 `DyberPet/plugins/*/plugin.json` → 校验（id 唯一、entry 存在、schema 合法）→ 用 schema defaults 补全 `plugins_settings` 缺失字段；`start_enabled()` 启所有已启用插件；`set_enabled(pid, flag)` 运行期启停；`stop_all()` 退出清理；**崩溃守护**：调用 `on_enable/on_load/事件回调` 统一 `try/except`，单插件异常累计 ≥5 次自动 `disable` + 写日志 + `api.pet.notify` 告知用户。
导入方式：插件在 `DyberPet/plugins/<id>/` 且各含 `__init__.py`，用 `importlib.import_module(f'DyberPet.plugins.{pid}.{entry_mod}')` 标准包导入（未来外部路径 Phase 1/3 再扩展）。
持有 `settings.plugin_manager` 引用（在 `run_DyberPet.py` 创建后赋值），供 `BasicSettingUI` 运行期启停调用。

### 4.5 `DyberPet/plugins/lol_companion/plugin.json`
```json
{
  "id": "lol_companion",
  "name": "LoL 游戏陪玩解说",
  "version": "1.0.0",
  "author": "DyberPet",
  "description": "轮询英雄联盟 Live Client Data API，用本地 Ollama 实时解说对局并驱动桌宠情绪反应。",
  "entry": "main:LoLCompanionPlugin",
  "min_app_version": "0.6.7",
  "permissions": ["pet.bubble", "pet.react", "net.localhost", "net.ollama"],
  "settings_schema": [
    {"key": "enabled",   "type": "switch", "default": true,  "label": "启用游戏陪玩"},
    {"key": "model",     "type": "combo",  "default": "gemma3:4b", "label": "解说模型", "options_ref": "ollama_models"},
    {"key": "style",     "type": "combo",  "default": "肥牛", "label": "解说风格", "options": ["肥牛","电竞主播","温柔吐槽","暴躁老哥"]},
    {"key": "reactions", "type": "switch", "default": true,  "label": "情绪反应"},
    {"key": "bubble",    "type": "switch", "default": true,  "label": "解说气泡"}
  ]
}
```

### 4.6 `DyberPet/plugins/lol_companion/worker.py`
```python
from PySide6.QtCore import QThread, Signal
import threading
from DyberPet.llm_core import (GameDataReader, Caster, caster_worker,
                               emotion_for, DEFAULT_OLLAMA_BASE)

class LoLCompanionWorker(QThread):
    caster_line = Signal(str)
    companion_react = Signal(str)
    def __init__(self, cfg: dict, ollama_base=DEFAULT_OLLAMA_BASE,
                 model=None, interval=2.0, parent=None):
        super().__init__(parent)
        self.cfg = cfg  # 直接引用 plugins_settings['lol_companion']，UI 改即生效
        self.reader = GameDataReader()
        self.caster = Caster(ollama_base=ollama_base, model=model or cfg.get('model'),
                             style=cfg.get('style', '肥牛'))
        self.interval = interval
        self._stop_event = threading.Event()
    def run(self):
        caster_worker(self.reader, self.caster, self.interval, self.cfg,
            emit=lambda line: self.caster_line.emit(line) if self.cfg.get('bubble', True) else None,
            stop=self._stop_event,
            emit_meta=lambda p,ev,ch,me: self.companion_react.emit(
                emotion_for(p,ev,ch,me).value) if self.cfg.get('reactions', True) else None)
    def stop(self): self._stop_event.set()
```

### 4.7 `DyberPet/plugins/lol_companion/main.py`
```python
import DyberPet.settings as settings
from DyberPet.plugin_system.base import Plugin
from DyberPet.llm_core import DEFAULT_OLLAMA_BASE
from .worker import LoLCompanionWorker

class LoLCompanionPlugin(Plugin):
    def on_load(self): pass
    def on_enable(self):
        cfg = self.api.settings.all()
        self.worker = LoLCompanionWorker(cfg=cfg, ollama_base=DEFAULT_OLLAMA_BASE,
                                         model=cfg.get('model'), interval=2.0)
        self.worker.caster_line.connect(lambda line: self.api.pet.say(line))
        self.worker.companion_react.connect(lambda emo: self.api.pet.react(emo))
        self.worker.start()
    def on_disable(self):
        w = getattr(self, 'worker', None)
        if w is not None:
            w.stop(); w.wait(2000)
```

### 4.8 `DyberPet/settings.py`（精确 diff）
**顶部删除**（第 11–16 行）：
```python
# [LoL 陪玩] 默认开启，可在设置中关闭
lol_companion_enabled = True
lol_companion_model = "gemma3:4b"
lol_companion_style = "肥牛"
lol_companion_reactions = True
lol_companion_bubble = True
```
**顶部新增**：
```python
# [插件] 各插件设置子字典，随 settings.json 落盘；结构由 plugin.json 的 settings_schema 定义
plugins_settings = {}
```
**`init_settings()`**：`global` 列表去掉 5 个旧变量、加 `plugins_settings`；在读取 `data_params` 后追加迁移+补全：
```python
plugins_settings = data_params.get('plugins_settings', {})
# 首次迁移：旧顶层 lol_companion_* 键 → plugins_settings['lol_companion']
if not plugins_settings.get('lol_companion') and any(
        k.startswith('lol_companion_') for k in data_params):
    plugins_settings['lol_companion'] = {
        'enabled':   data_params.get('lol_companion_enabled', True),
        'model':     data_params.get('lol_companion_model', 'gemma3:4b'),
        'style':     data_params.get('lol_companion_style', '肥牛'),
        'reactions': data_params.get('lol_companion_reactions', True),
        'bubble':    data_params.get('lol_companion_bubble', True),
    }
    if plugins_settings['lol_companion']['model'] == 'nanbeige4.1:3b':
        plugins_settings['lol_companion']['model'] = 'gemma3:4b'
```
文件不存在的 `else` 分支：`plugins_settings = {}`。
保存旧 nanbeige 修复块（第 350–353 行）删除（已并入上面迁移）。
**`save_settings()`**：`global` 列表去掉 5 个旧变量、加 `plugins_settings`；`data_js` 去掉 5 个旧键、加 `'plugins_settings': plugins_settings`。

### 4.9 `DyberPet/pet_chat.py`（精确 diff）
- 第 25 行：`from DyberPet.lol_companion import Caster, COMPANION_PROMPT, sanitize_commentary` → `from DyberPet.llm_core import Caster, COMPANION_PROMPT, sanitize_commentary`
- 第 330 行：`self.caster = Caster(model=settings.chat_model or settings.lol_companion_model)` → `self.caster = Caster(model=settings.chat_model or settings.plugins_settings.get('lol_companion', {}).get('model'))`
- 第 429 行：`style = settings.lol_companion_style` → `style = settings.plugins_settings.get('lol_companion', {}).get('style', '肥牛')`
> 注：pet_chat 的 `Caster` 实例走 `reply_chat`（用 `COMPANION_PROMPT`，不依赖 style），故 style 参数对其无副作用，仅 companion 插件用到。

### 4.10 `DyberPet/DyberSettings/BasicSettingUI.py`（精确 diff）
- 第 19 行：`from DyberPet.lol_companion import list_ollama_models` → `from DyberPet.llm_core import list_ollama_models`
- `CompanionGroup` 6 张卡的初始化读取（第 218、231、250、259、271 行）全部从 `settings.lol_companion_*` 改为 `settings.plugins_settings['lol_companion'].get('<key>', <default>)`：
  - `enabled`(218) / `model`(231) / `style`(250) / `reactions`(259) / `bubble`(271)
- 5 个回调（第 535–553 行）改为写 `plugins_settings` 并运行期启停：
```python
def _CompanionEnableChanged(self, isChecked):
    settings.plugins_settings.setdefault('lol_companion', {})['enabled'] = bool(isChecked)
    settings.save_settings()
    pm = getattr(settings, 'plugin_manager', None)
    if pm: pm.set_enabled('lol_companion', bool(isChecked))

def _CompanionModelChanged(self, value):
    settings.plugins_settings.setdefault('lol_companion', {})['model'] = value
    settings.save_settings()

def _CompanionStyleChanged(self, value):
    settings.plugins_settings.setdefault('lol_companion', {})['style'] = value
    settings.save_settings()

def _CompanionReactionChanged(self, isChecked):
    settings.plugins_settings.setdefault('lol_companion', {})['reactions'] = bool(isChecked)
    settings.save_settings()

def _CompanionBubbleChanged(self, isChecked):
    settings.plugins_settings.setdefault('lol_companion', {})['bubble'] = bool(isChecked)
    settings.save_settings()
```
- 第 343 行 `_populate_combo(self.CompanionModelCard, RECOMMENDED_MODELS, settings.lol_companion_model)` → 改读 `settings.plugins_settings['lol_companion'].get('model', 'gemma3:4b')`
- 第 630 行 `_populate_combo(self.CompanionModelCard, merged, settings.lol_companion_model)` → 同上读 `plugins_settings`
- `CompanionRefreshCard` 刷新逻辑（第 234–240、609–634 行）保留，仅目标下拉框值来源改 `plugins_settings`。

### 4.11 `run_DyberPet.py`（精确 diff）
- 删除第 18 行 `from DyberPet.lol_companion import LoLCompanionWorker`
- 删除第 67–73 行 LoL 硬编码块（实例化+连信号+start+aboutToQuit），替换为：
```python
# 插件系统：发现并启动已启用的插件（LoL 陪玩作为首个插件被加载）
from DyberPet.plugin_system.manager import PluginManager
self.plugin_manager = PluginManager(pet_widget=self.p, app=self)
self.plugin_manager.discover()
self.plugin_manager.start_enabled()
settings.plugin_manager = self.plugin_manager
```
- 删除第 187–192 行 `_stop_companion()` 方法，替换为 `aboutToQuit.connect(self.plugin_manager.stop_all)`（放在 chat 的 `aboutToQuit.connect(self._stop_chat)` 附近）。
- `__connectSignalToSlot` 中若有引用 `self.companion` 的代码需一并移除（目前无）。

> `DyberPet.py` 中 `sig_caster_line` / `sig_companion_react` 两个信号（389–390、441–442）Phase 0 **保留不删**（dead code，无害；删除需动核心文件，风险高，留待 Phase 1 清理）。

---

## 5. 实施步骤顺序（每步一个 git 锚点，可单独回滚）
1. 新建 `llm_core.py`（平移+settings-free 改造），先**不删** `lol_companion.py`。
2. 改 `pet_chat.py` 与 `BasicSettingUI.py` 的 import/读取指向 `llm_core`（此时旧 `lol_companion.py` 仍可被 run 引用，验证 chat/UI 不被破坏）。
3. 新建 `plugin_system/*` 与 `plugins/lol_companion/*`。
4. 改 `settings.py`（plugins_settings + 迁移 + 删旧变量）。
5. 改 `run_DyberPet.py`（接 PluginManager，删 LoL 硬编码）。
6. 删 `DyberPet/lol_companion.py`。
7. 端到端自测（第 6 节）。

---

## 6. 验证清单（自测）
- [ ] 应用正常启动，无 import 错误；`DyberPet/plugins/lol_companion/__init__.py` 存在使包可导入。
- [ ] 启动后桌宠正常，开一局 LoL（或 mock LCU）能出解说气泡 + 情绪反应。
- [ ] 设置面板 LoL Companion 6 张卡读写为 `plugins_settings`，改模型/风格/开关即时或重启后生效。
- [ ] 关闭「启用游戏陪玩」→ 运行期插件停止（线程退出）；重新开启 → 重启线程。
- [ ] 删除 `data/settings.json` 中 `lol_companion_*` 旧键后首次启动，自动迁移进 `plugins_settings` 且行为不变；`nanbeige4.1:3b` 旧值自动修正为 `gemma3:4b`。
- [ ] 崩溃守护：在 `on_enable` 故意抛异常 5 次，插件被自动禁用且用户收到通知，主程序不崩。
- [ ] `pet_chat` 对话功能正常（复用 llm_core.Caster，未受影响）。

---

## 7. 不在 Phase 0 范围（明确划界）
- 插件中心 UI（列表/启用禁用/安装/卸载/打开文件夹/热重载）→ **Phase 1**
- `settings_schema` 驱动设置卡**自动渲染**（替换 `CompanionGroup`）→ **Phase 1**
- 事件总线补全（`focus_start/end`、`item_used`、`chat_message` 等）→ **Phase 2**
- 多语言（插件字符串走 `res/language`）→ **Phase 2**
- 插件分享格式 / 导入导出 / 权限展示 / 安全模式 → **Phase 3**

### 备选：若坚持 Phase 0 一步到位做 schema 自动渲染
- 需重构 `BasicSettingUI.SettingInterface`：新增"插件"动态分组，`manager` 遍历已启用插件 `settings_schema`，按 `type`（switch/combo/slider/text）生成对应 qfluentwidgets 卡并双向绑定到 `plugins_settings[pid][key]`；删除 `CompanionGroup` 整块。
- 收益：彻底消灭硬编码设置 UI，Phase 1 只剩"列表/安装/卸载"等外壳。
- 代价：Phase 0 风险显著上升（动设置框架 + 新增类型→卡映射 + 动态布局 + 模型刷新按钮的重新挂接），与"最小化改动"铁律相悖。**非推荐**。

---

## 8. 风险评估
| 风险 | 等级 | 缓解 |
|---|---|---|
| `llm_core` settings-free 改造引入 Caster 调用方遗漏 | 中 | pet_chat/插件均显式传 model/style；自测覆盖 chat |
| 插件包导入路径在打包（PyInstaller）后失效 | 中 | `DyberPet/plugins/__init__.py` 标记包；Phase 1 再评估 spec 配置 |
| `plugins_settings` 迁移破坏旧用户存档 | 低 | 保留旧键读取做一次性迁移，旧键不再写回；自测覆盖 |
| 运行期启停插件时 worker 未完全退出 | 低 | `on_disable` 用 `stop()`+`wait(2000)`，沿用原 `_stop_companion` 语义 |
| 崩溃守护误伤正常插件 | 低 | 阈值 5 次且每次独立 try/except，仅统计 `on_*`/事件回调异常 |
