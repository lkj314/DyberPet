# 呆啵宠物（DyberPet）插件中心开发蓝图

> 目标：在架构还没固化成"改一处崩三处"之前，引入一套低成本的 in-process 插件系统；
> 用已经真实在跑的「游戏 API 解说插件」（LoL 陪玩）作为第一个、也是验证性的插件。
> 核心理念：复用现有 MOD 发现范式、最小依赖、声明式设置、QThread 隔离。

---

## 0. 代码审阅结论

### 0.1 游戏 API 解说插件 = 已是事实上的插件，只是没被当插件管
- 它就是 `DyberPet/lol_companion.py` 里的 `LoLCompanionWorker`（`QThread`）。
- 它做的事：轮询 LoL Live Client Data API（`localhost:2999`）→ 调本地 Ollama 出解说词 → 通过 Qt Signal 把「解说词 / 情绪」投回 `PetWidget`。
- 它"插"在哪：
  - 实例化 + 连信号：`run_DyberPet.py:69-73`（硬编码）
  - 配置：`settings.py` 里 5 个全局变量 `lol_companion_enabled/model/style/reactions/bubble`
  - UI：`BasicSettingUI.py` 里写死的 `CompanionGroup`（6 张设置卡）

### 0.2 真正的痛点：每加一个插件要复制 4+ 处硬编码
`import` / `实例化` / `连信号` / `settings 全局变量` / `设置 UI` /（可能还有菜单）。
现在只有 1 个，复制成本极低；等堆到 10 个再抽，就是重构地狱。现在引入成本最低——判断正确。

### 0.3 底座已有一套可复用的「发现-加载」范式
MOD 生态（item / role / pet）已经是：
扫文件夹 → 按 mtime 排序 → 读 `info.json` + `*_config.json` → 合并（后者覆盖前者）。
插件中心应**直接套这套**，而不是另起 setuptools `entry_points` / 外部注册表（属于过度工程）。

### 0.4 现成的「插件 API 面」（都在 `PetWidget` 与信号上）
- `pet.show_speech(text)` → 气泡（`DyberPet.py:1403`）
- `pet.react(emotion)` → 程序化情绪反应：蹦 / 摇 / 缩（`DyberPet.py:1409`）
- `pet.register_notification(type, msg)` → 右下角吐司（`DyberPet.py:1390`）
- `pet.register_bubbleText(dict)` → 受控气泡（`DyberPet.py:1394`）
- `pet.register_accessory(accs)` → 生成附件（`DyberPet.py:1458`）
- `pet.use_item(name)` → 触发物品（`DyberPet.py:1561`）
- 宠物对外信号：`hp_updated` / `fv_updated` / `change_note` / `move_sig` / `show_dashboard` / `show_controlPanel` …
- 右键菜单系统 `custom_roundmenu.RoundMenu` 支持 `addAction / addMenu / addWidget`
- 设置卡体系 `qfluentwidgets` 的 `SettingCardGroup` + 各种 Card

---

## 1. 目标架构

### 1.1 插件 = 一个文件夹包
```
DyberPet/plugins/
  lol_companion/
    plugin.json          # 清单（manifest）
    main.py              # Plugin 子类
    helpers/...          # 复用原 lol_companion.py 的纯逻辑
```
`plugin.json`（初版字段）：
- `id`, `name`, `version`, `author`, `description`
- `entry`: `"main:MyPlugin"`（默认 `main.py` 里的 `Plugin` 子类）
- `min_app_version`: `"0.6.7"`
- `settings_schema`: `[...]` 声明式设置（驱动自动生成设置卡）
- `permissions`: `["pet.bubble","pet.react","net.localhost","net.ollama"]`（声明所需能力，UI 展示 + 未来做约束）
- `load_order` / `depends`: 可选，用于拓扑排序

### 1.2 PluginManager（新建 `DyberPet/plugin_manager.py`）
职责：
- `discover()`：扫 `DyberPet/plugins/*/`，按 `load_order` / `depends` 拓扑排序
- `load(manifest)`：校验（id 唯一、min_app_version、entry 存在、schema 合法）
- `lifecycle`：`enable` / `disable` / `start` / `stop` / `reload` / `uninstall`
- 把「启用」状态持久化到 settings
- **崩溃守护**：某插件在 hook 边界抛异常累计 N 次 → 自动 `disable` + 写日志 + 通知用户

### 1.3 Plugin 基类（Hook 接口）
```python
class Plugin:
    def __init__(self, api: "PetAPI"): ...
    def on_load(self): ...            # 注册事件 / 菜单 / 设置就绪
    def on_enable(self): ...          # 启动后台线程等
    def on_disable(self): ...         # 停止、清理
    def on_unload(self): ...
```
后台循环统一用 `QThread`（沿用 LoL 现有做法），保证主线程不被插件卡死 / 带崩。

### 1.4 PetAPI —— 给插件的「干净门面」（新建 `DyberPet/pet_api.py`）
插件**不直接 import 内部模块**，只拿这个对象：
```python
api.pet.say(text)                 # → show_speech
api.pet.react(emotion)            # → react
api.pet.notify(type, msg)         # → register_notification
api.pet.bubble(dict)              # → register_bubbleText
api.pet.use_item(name)            # → use_item
api.pet.add_menu(action)          # → 注册到右键菜单
api.events.hp_changed.connect(cb) # 事件总线（包装宠物信号）
api.events.pet_changed.connect(cb)
api.events.midnight.connect(cb)
api.events.focus_start/end.connect(cb)
api.settings.get/set(key)         # 走插件自己的 settings 子字典
api.app                            # 高级用法兜底（慎用）
```

### 1.5 设置：从「全局变量海」改为「声明式 schema」
- `settings.py` 不再为插件加全局变量；改为 `plugins_settings: {plugin_id: {...}}`，随 `save_settings()` 一起落盘。
- 插件用 `settings_schema` 声明字段 → 设置面板**自动渲染**对应 Card（Switch / Combo / Slider / Text）。
- 兼容性：首次启动把现有 `lol_companion_*` 全局变量迁移进插件的 settings，旧全局变量删除。

---

## 2. 第一步（也是验证）：把 LoL 解说插件拆进系统
这是最关键的一步——用真实需求驱动，做完系统就算跑通：
1. 新建 `plugins/lol_companion/`，`plugin.json` 描述 `id=lol_companion`、依赖 `net.localhost` / `net.ollama`、`settings_schema`（enabled / model / style / reactions / bubble 五字段）。
2. 把 `lol_companion.py` 的**纯逻辑**（`Emotion` / `emotion_for` / `classify_priority` / `diff_me` / `build_data_prompt` / `sanitize_commentary` / `Caster` / `GameDataReader` / `caster_worker`）平移为插件内部 helper，不依赖 Qt 的部分原样保留。
3. `main.py` 的 `LoLCompanionPlugin(Plugin)`：`on_enable()` 起 `LoLCompanionWorker` 并把信号接到 `api.pet.say` / `api.pet.react`；`on_disable()` stop 线程。
4. 从 `run_DyberPet.py` 删掉硬编码的那几行，改成 `plugin_manager.start_enabled()`。
5. 从 `BasicSettingUI.py` 删掉写死的 `CompanionGroup`，改由 schema 自动生成（或在设置面板里挂一个「插件设置」区，把已启用插件的卡片动态挂进去）。
6. 迁移旧设置、自测：开关、模型切换、风格、情绪反应、崩溃时主程序不挂。

---

## 3. 插件中心 UI
- **入口**：设置面板新增「插件中心」分组 / 独立页（或在仪表盘加入口）。
- **列表**：已装插件卡片（名称、版本、作者、描述、启用开关、设置、打开文件夹、卸载）。
- **安装**：选文件夹 / zip → 复制到 `DyberPet/plugins/<id>/` → 校验清单 → 刷新列表。
  纯 Python in-process，**不跑 pip**；依赖在 schema 里可选声明，缺失时插件自行降级（沿用 `pet_chat.py` 的容错 import 风格）。
- **开发者模式**：热重载（改代码后点「重载」即停即起）。

---

## 4. 分阶段路线图（建议按 Tier 推进）

**Phase 0 — 地基（必须先做，且由 LoL 插件验证）**
- PluginManager + 清单格式 + Plugin 基类 + PetAPI 门面 + 事件总线 + 声明式设置引擎。
- 把 LoL 解说插件迁入系统，删掉 `run_DyberPet.py` 与 `BasicSettingUI.py` 的硬编码。
- 产物：系统跑通 + 1 个真实插件。

**Phase 1 — 插件中心 UI**
- 列表 / 启用 / 禁用 / 设置 / 安装 / 卸载 / 打开文件夹 / 开发者热重载。

**Phase 2 — 更丰富的钩子**
- 事件补全：`pet_changed`、`item_used`、`midnight`、`focus_start/end`、`chat_message`。
- 菜单注册、附件 API、聊天指令（插件可向桌宠对话注入「命令」，如 `/roll`）。
- 多语言：插件字符串走 `res/language` 体系。

**Phase 3 — 生态**
- 插件分享格式（单文件夹打包）、导入 / 导出。
- 崩溃守护 + 权限展示 + 安全模式（沙箱暂不做，靠线程隔离 + 异常边界 + 自动禁用）。
- 可选依赖提示与一键装（仅边缘，优先级低）。

**Phase 4 — 进阶（仅在确有需求时）**
- 进程外插件 / WebView 插件（隔离更强，但复杂度陡增，前期严禁）。

---

## 5. 关键决策与风险

- **in-process Python，无真沙箱**：靠 (a) 后台逻辑跑 `QThread`、(b) hook 边界统一 `try/except`、
  (c) 崩溃计数自动禁用、(d) 文档约定不碰危险模块。不引入多进程（过度工程，且断 Signal 链路）。
- **不引入 setuptools entry_points / 外部注册表**：直接复用 MOD 文件夹扫描，最小依赖。
- **向后兼容**：旧 `lol_companion_*` 全局变量首次启动自动迁移，不破坏现有用户存档。
- **可逆**：Phase 0 先做「加系统、旧逻辑包一层」，再删硬编码；每步可单独回滚。
- **范围克制**：Phase 0+1 足以交付「插件中心」，Phase 2+ 按需。先不碰市场 / 热更新 / 沙箱。

---

## 6. 推荐起点

按一贯的 Tier 习惯，建议**直接批准 Phase 0**（地基 + 把 LoL 解说插件迁进去作为活样本）：
- 成本最低、风险可控、且立刻验证架构是否站得住；
- Phase 1 紧随其后补 UI。

需要开工时，我会先出 Phase 0 的实施方案（文件清单 + 改动 diff 审批），再动手。
