# DyberPet 魔改版 · 项目交接文档（HANDOVER）

> **读者：接手本项目的 AI 助手 / 开发者。** 本文档是项目总入口，目标是让你在 10 分钟内建立全景认知、避开所有已知的坑。
> 最后更新：2026-09-05（由协作 AI 基于仓库真实状态撰写，非凭记忆）
>
> **文档地图（先看哪份）**：
> | 文档 | 内容 |
> |---|---|
> | **本文件 `HANDOVER.md`** | 项目全景：是什么 / 怎么构建 / 架构地图 / 插件 / 铁律 / 坑 |
> | `BUILD_GUIDE.md` | 构建教程细节（环境搭建、spec 禁改史、沙箱执行须知 §10 必读） |
> | `docs/plugin_center_handoff.md` | 插件系统 Phase 0 的设计决策与验证记录 |
> | `.workbuddy/memory/2026-09-*.md` | 逐日开发日志（含每个坑的现场记录，最详细） |

---

## 0. 30 秒速查（如果你只读一节，读这节）

**这是什么**：Windows 桌宠 **EXE**（PyInstaller onedir 打包）。基于官方开源版 0.6.7 深度魔改，自研了端侧 Ollama AI 体系（chat/游戏陪玩/修仙放置/世界冒险）。**成品是 `dist/DyberPet/DyberPet.exe`，不是 `python run_DyberPet.py`**。

**怎么构建（唯一正确姿势）**：

```bat
cd /d U:\DyberPet
taskkill /F /IM DyberPet.exe
.venv\Scripts\python.exe build_dyber.py
```

- 退出码 0 = 成功，产物在 `dist/DyberPet/`（整个目录就是交付物，不打 zip）。
- **PowerShell 必须写 `.\.venv\Scripts\python.exe`**（少写 `.\` 会把 `.venv` 当模块名报错）。
- 仓库自带 `.venv`（Python 3.12.10 + PySide6 6.11.2 + PyInstaller 6.22.2），**无需用户手动装任何东西**。

**三条最高优先级禁令**：

1. ❌ **绝不**用裸 `pyinstaller` / `python -m PyInstaller` / 让用户 `pip install` 后裸跑——构建入口只有 `build_dyber.py` 这一条。
2. ❌ **绝不**改 `DyberPet.spec`——它是副产物，构建脚本根本不读它；改打包配置只改 `build_dyber.py`。
3. ❌ **绝不**因为"我的沙箱里 import PyInstaller 失败"就断定项目没法构建/让用户手动构建——用错了 Python。检查对象应该是 `U:\DyberPet\.venv\Scripts\python.exe`。

**构建后必做一步（最容易丢用户数据的地方）**：构建脚本会把旧 `dist/DyberPet` 整体轮转成 `dist/DyberPet.old.<时间戳>/`，**用户的运行期存档（修为/好感/设置/记忆）全在里面**。必须把旧目录 `data/` 下的所有文件拷回新 `dist/DyberPet/data/`，并把 `settings.json` 的 `default_pet` 设为 `'韩立'`（用户指定的默认角色）。

---

## 1. 项目是什么（产品视角）

一只 Qt 桌宠，用户叫它"肥牛"，当前主形象是**韩立**（《凡人修仙传》，官方 v0.8.10 提取的美术）。功能全景：

| 板块 | 功能 | 位置 |
|---|---|---|
| 桌宠本体 | 动画/拖拽/喂食/好感度/摸头/商店道具/昼夜作息 | `DyberPet/DyberPet.py`（主窗口 PetWidget） |
| **AI chat** | 多轮对话 + TTS 语音播报 + 离线语音输入（vosk）+ "始终聆听" | `DyberPet/pet_chat.py` |
| **插件中心** | 插件发现/启停/settings_schema 自动渲染设置卡（switch/combo/slider 三种类型） | `DyberPet/plugin_system/` + `DyberSettings/PluginCenterUI.py` |
| **修仙放置** | 挂机涨修为，炼气→真仙 40 阶，突破/顿悟/双修/丹药/炼丹，与商店货币（灵石）打通 | `cultivation_service.py` + `plugins/cultivation/` |
| **世界冒险** | 旅行青蛙式缺席叙事：御剑离场、道韵元婴留守、传讯符、秘境历练 | `adventure_service.py` + `plugins/adventure/` |
| **游戏陪玩** | 五子棋、斗地主（AI 对手 + 桌宠吐槽 + 胜利联动修为/历练） | `plugins/gomoku/`、`plugins/doudizhu/` |
| LoL 解说 | 读取 LCU API + Ollama 生成解说词（第一个插件，Phase 0 验证品） | `plugins/lol_companion/` |
| 角色面板 | 角色状态/背包/商店/日常任务/动作管理/**修仙之路**/**历练** | `DyberPet/Dashboard/` |
| 系统面板 | 基础设置/游戏存档/道具/附属宠物/插件中心 | `DyberPet/DyberSettings/` |
| 角色图鉴 | 右键菜单读取角色 info/ 展示封面/标签/介绍（借鉴官方 0.8.10） | `DyberPet/RoleGallery.py` |

**与官方版本的关系（重要，路线认同）**：本仓库 fork 自官方 0.6.7（纯桌宠框架，无 AI）。此后所有 AI 能力（Ollama 内置/chat/插件中心/游戏陪玩/修仙/冒险/人设）都是**在完全未借鉴官方的情况下自研的**。官方 0.8.10 走多 LLM 云端渠道（OpenAI/Gemini/OpenRouter），与我们**底层路线截然不同**。官方新角色美术可提取复用（`tools/unpack_dyberpet.py`），但其 LLM 方案**永远不引入**（详见 §7 铁律 1）。

---

## 2. 目录地图

```
U:\DyberPet\
├── run_DyberPet.py          # 启动脚本（EXE 入口）：初始化 settings → PetWidget → 各面板 → 接线信号
├── build_dyber.py           # ⭐ 唯一构建入口（改打包配置只改这里，绝不改 .spec）
├── dyber_smoke.py           # 应用级 offscreen 冒烟测试（改代码后必跑）
├── BUILD_GUIDE.md           # 构建教程（细节）
├── HANDOVER.md              # 本文件
├── requirements.txt         # 锁定依赖（PySide6 6.11.2 / qfluentwidgets 1.11.3 / edge-tts / vosk …）
├── DyberPet.spec            # ⚠️ 副产物，构建脚本不读它，别改
├── .venv\                   # ⭐ 项目 Python 3.12.10 环境（含 PyInstaller 6.22.2）
├── DyberPet\                # 主 Python 包
│   ├── DyberPet.py          # 主窗口 PetWidget：动画/事件/信号中枢（Signal 大本营）
│   ├── run 内真实类名        # 主窗口类叫 PetWidget（不是 DyberPetWidget）
│   ├── settings.py          # 全局设置（模块级全局变量风格，load/save/init 三段）
│   ├── conf.py              # PetConfig 角色配置解析、ItemData 道具系统、PetData 好感喂养
│   ├── modules.py           # InteractionWorker 等后台 worker
│   ├── Notification.py      # 通知系统（读 res/role/<角色>/note/note.json 播事件音效）
│   ├── RoleGallery.py       # 角色图鉴弹窗
│   ├── pet_chat.py          # AI chat 窗口（TTS 链路参考实现：常驻 QMediaPlayer+QAudioOutput）
│   ├── llm_core.py          # Ollama 调用封装（LOL 解说用 Caster；插件各有自己的 _generate）
│   ├── cultivation_service.py  # ⭐ 修为 core（纯逻辑无 Qt）：40 阶/突破/顿悟/灵石/丹药效果/炼丹
│   ├── persona_service.py   # ⭐ 人设 core（统一出口）：四层拼装 system prompt + 历练记忆
│   ├── persona.json         # 出厂人设（韩立 L0+L1；用户可放 data/persona.json 覆盖）
│   ├── adventure_service.py # ⭐ 冒险 core（纯逻辑无 Qt）：秘境状态机/成功率/结果掷定/传讯符
│   ├── plugin_system\       # 插件框架：base.py(Plugin 基类) / manager.py(发现启停) / api.py(PetAPI 门面)
│   ├── plugins\             # 五个插件（见 §4）
│   ├── Dashboard\           # ⭐ 角色面板（FluentWindow）：状态/背包/商店/任务/动作/修仙/历练
│   └── DyberSettings\       # 系统面板（ControlMainWindow）：基础设置/插件中心/游戏存档…
├── res\                     # 全部资源（构建后 robocopy 同步到 exe 同级，按 cwd 加载！）
│   ├── role\                # 角色：Kitty/ChrisKitty/银月/韩立/sys（散图帧 + act_conf.json/pet_conf.json）
│   ├── pet\                 # 附属宠物：派蒙/韩立元婴
│   ├── items\               # 道具包（FanRenXiuXianZhuan 丹药 19 种 = 商店/炼丹数据源）
│   ├── icons\               # 图标 + qss（Dashboard 和 Settings 各有 qss 目录）
│   ├── language\            # language.json 多语言
│   └── sounds\              # 音效
├── data\                    # ⚠️ 运行期用户数据（settings.json/pet_data.json/task_data.json）——构建会轮转 dist，必须迁移！
├── tools\                   # 测试与工具脚本（见 §5）
├── dist\DyberPet\           # ⭐ 最终交付物（DyberPet.exe + _internal\ + res\ + data\）
└── dist\DyberPet.old.*\     # 历次构建轮转的旧包（用户存档在这里，迁移后可留可删）
```

---

## 3. 运行时架构（接手前先看懂这张图）

### 3.1 启动链（run_DyberPet.py）

```
settings.init() → QApplication → PetWidget(p) → DashboardMainWindow(board) 角色面板
                            ├→ ControlMainWindow(conp) 系统面板
                            └→ __connectSignalToSlot():
       pet.show_controlPanel → conp.show_window
       pet.show_dashboard    → board.show_window        # 角色面板
       pet.show_culti_page   → board.show_cultivation   # 修仙之路页定位
       pet.show_adventure_page → board.show_adventure    # 历练页定位
       + 插件管理器 discover/plugins/ → 逐个 on_enable(api)
```

**新页面/新信号的接线模式**：PetWidget 加 Signal → 插件经 `PetAPI` emit → run_DyberPet.py 连接到目标面板的 `show_xxx()`。改完要确认 run 里的连接没漏（这是"按钮点了没反应"的最常见原因）。

### 3.2 core 服务三件套（主程序模块，非插件；插件间零依赖的关键）

| 服务 | 职责 | 谁在驱动 |
|---|---|---|
| `cultivation_service` | 修为数值（40 阶/突破 roll/顿悟/速率倍率/灵石/丹药效果/炼丹炉），时间戳差值结算 | cultivation 插件 5s tick；面板页只读 |
| `persona_service` | AI 人设统一出口（L0 人设/L1 境界人格/L2 实时状态/L3 记忆 → system prompt），四种长度模式 | 各插件 LLM 前先问它 |
| `adventure_service` | 秘历状态机（派出时一次掷定结果/传讯符时刻表/留守事件/历练志） | adventure 插件 5s tick |

**设计不变式**：core 是唯一数值权威（`get_core()` 单例），插件只做 UI/演出/驱动；跨方事件（面板手动突破、游戏加修为、丹药服用）进 core 的 `pending` 队列，由插件 tick 统一 drain 演出——**单一驱动者**，避免两处结算打架。

### 3.3 PetAPI 门面（插件能摸到的一切）

`plugin_system/api.py`：`pet.say/react/notify/play_audio/play_act/get_position/open_cultivation/open_adventure`、`events.hp_changed/fv_changed/pet_changed/midnight/touched`、`settings.get/set`、`api.add_exp(修为)`、`api.add_coins(灵石)`、`api.add_item(道具)`、`api.add_adventure_buff(斗法战意)`、`api.add_menu(右键菜单)`。给插件加新能力 = 在这里加方法（薄包装，别让插件直接摸 PetWidget 内部）。

### 3.4 两个面板，别再搞混（血泪教训）

| | 角色面板 Dashboard | 系统面板 DyberSettings |
|---|---|---|
| 窗口类 | `DashboardMainWindow`（run 里叫 `board`） | `ControlMainWindow`（run 里叫 `conp`） |
| 页面 | 角色状态/背包/商店/任务/动作/**修仙之路(cultiUI)**/**历练(adventureUI)** | 基础设置/插件中心/游戏存档… |
| 定位 | **玩法详情页放这里**（用户拍板） | 纯系统设置，不放玩法页 |
| 查找陷阱 | 页面标题走 `tr('Status')`+语言文件翻译，**源码 grep 中文搜不到**——按目录/类名找 | 同左 |

qfluentwidgets **`ExpandLayout.addWidget` 不重挂 parent**——进滚动布局的卡片必须 `parent=self.scrollWidget`，否则整页叠加+点击失效（踩过两次，诊断手法：offscreen `mapTo(window)` 几何探针 + `childAt` 命中测试）。

---

## 4. 插件清单（`DyberPet/plugins/`，共 5 个）

| id | 版本 | 类型 | 状态 | 关键联动 |
|---|---|---|---|---|
| `lol_companion` | 1.0.0 | 常驻 worker | Phase 0 验证品，可用 | LCU API + Ollama 解说 |
| `gomoku` | 1.0.0 | launchable 游戏 | 可用 | 胜利 → `add_exp(600)` + `add_adventure_buff` |
| `doudizhu` | 2.0.0 | launchable 游戏 | 可用（真实斗地主手感版：预合成牌型语音 60 条、军师记牌） | 胜利 → `add_exp(600)`、春天 +1000 + buff |
| `cultivation` | 1.2.0 | 常驻系统 | 可用（修仙放置 v1.2：灵石/炼丹/丹药效果全通） | 驱动 cultivation core；tick 演出 |
| `adventure` | 1.0.0 | 常驻系统 | 可用（世界冒险） | 驱动 adventure core；归来入账 |

**插件打包规则（build_dyber.py 已自动化）**：构建脚本自动扫描 `DyberPet/plugins/*/`，对每个插件生成 `--hidden-import DyberPet.plugins.<pid>.main`（importlib 动态导入抓不到）+ 把 `plugin.json` 等**全部非 .py 资源**（如 `voice/*.mp3`）按相对路径 `--add-data` 进包。新增插件通常**不用改构建脚本**；新增主程序 core 服务（函数内 import 的）要手动加 `--hidden-import`（现在有 cultivation/persona/adventure 三个）。

**settings_schema 类型**：`switch` / `combo`（静态 options 或 `options_ref`）/ `slider`——PluginCenterUI 自动渲染，schema 是 UI 唯一真相源。

---

## 5. 验证闭环（改代码后的标准流程，一步都别省）

```bat
:: 1) 语法（只是底线，过了不代表能用）
.venv\Scripts\python.exe -m py_compile <改动的文件...>

:: 2) 应用级冒烟（offscreen，不需要显示窗口）
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe dyber_smoke.py

:: 3) 数值回归（改了修为/冒险/人设相关必跑）
.venv\Scripts\python.exe tools\cultivation_test.py    # 12 组：节奏/突破/防作弊/400天长跑飞升
.venv\Scripts\python.exe tools\adventure_test.py      # 12 组：公式/分布/传讯/离线/存档
.venv\Scripts\python.exe tools\persona_probe.py       # 探针：离线预算 23 项（加 --online 可真连 Ollama）

:: 4) 构建
taskkill /F /IM DyberPet.exe
.venv\Scripts\python.exe build_dyber.py               # → BUILD_EXIT=0

:: 5) 迁移用户存档（dist 被轮转后！）
::    把 dist\DyberPet.old.<最新>\data\* 拷回 dist\DyberPet\data\
::    settings.json 的 default_pet 设为 '韩立'

:: 6) 打包验证：新符号真的进 PYZ 了吗（模板见下）
```

**ZlibArchiveReader 检查模板**（`<模块名>` 填 `DyberPet.xxx` 或 `DyberPet.plugins.<pid>.main`，`<符号>` 填函数/类名或任意字符串）：

```bat
.venv\Scripts\python.exe -c "import marshal,sys;z=__import__('PyInstaller.archive.readers',fromlist=['ZlibArchiveReader']).ZlibArchiveReader(r'build/DyberPet/PYZ-00.pyz');d=z.extract(sys.argv[1]);d=d if isinstance(d,bytes) else marshal.dumps(d);print(sys.argv[2], sys.argv[2].encode() in d)" <模块名> <符号>
```

数据文件（如 `persona.json`）不走 PYZ，要确认落在 `dist/DyberPet/_internal/DyberPet/` 下。

---

## 6. 近期里程碑（2026-09-04 ~ 09-05 两天大开发）

| 里程碑 | 内容 |
|---|---|
| 插件系统 Phase 0→1.5 | 插件框架 + 插件中心 UI（settings_schema 动态渲染），LoL 解说迁移为第一个插件（`docs/plugin_center_handoff.md`） |
| 五子棋陪玩 | TTS 修复（QAudioOutput 必须常驻成员，GC 会吃掉临时音频输出 → 静默无声） |
| 斗地主 v2 | 按用户定位重做：去精分、60 条预合成牌型语音、Ollama 转军师、提示循环候选 |
| 官方美术提取 | v0.8.10 的韩立(927帧/40动作)/银月/韩立元婴/丹药包，`tools/unpack_dyberpet.py` 安全解包 pickle 帧，零代码接入 |
| 借鉴四件套 | 角色图鉴 + note 事件音效（零代码白捡）+ 附属宠物入口 + 昼夜模式 |
| **修仙放置 v1.2** | core 数值层 + 面板化（用户否决独立悬浮窗）+ 商店联动（灵石/炼丹/丹药效果） |
| **人设系统** | persona_service 统一出口，三插件接入，探针测试锁稳定性 |
| **世界冒险** | 缺席叙事全链（道韵元婴浮层/传讯符/历练页/生态咬合） |

---

## 7. 铁律清单（违反必返工，按伤害排序）

1. **AI 路线（用户拍板，最高优先级）**：坚持端侧 Ollama，**绝不引入**官方多 LLM 渠道方案（OpenAI 兼容/Gemini/OpenRouter）。llm_core 的端侧路线是本魔改版与官方的根本分叉，强行套官方 LLM 会引入大量 bug。
2. **构建三禁**：禁裸 pyinstaller、禁改 .spec、禁因沙箱缺包判"不能构建"。见 §0 与 BUILD_GUIDE §10。
3. **构建后必迁移用户存档**：`dist/DyberPet.old.*/data/` → 新 `dist/DyberPet/data/`，`default_pet='韩立'`。漏了=用户修为/好感全丢。
4. **玩法详情页进角色面板 Dashboard**，不进系统面板（用户明确拒绝过一次）。
5. **数值只认 core**：修为/冒险数值 100% 代码掷骰，LLM 零参与决策；阶位由突破推进**绝不**由修为反查（会架空成功率机制）；演出克制（连破合并一场、无弹窗轰炸）。
6. **Qt 坑四连**：QAudioOutput/QMediaPlayer 必须常驻成员（GC 静默吃声音）；Plugin 基类非 QObject（`QTimer(self)` 会崩，用无 parent + 实例持引用）；ExpandLayout 卡片必须 `parent=scrollWidget`；需要事件循环的媒体对象不在工作线程创建。
7. **工具坑**：U 盘 Edit 有"报成功实未落盘"的假象，**改完必须 grep 验证**（大文件补丁优先用 python 脚本替换）；`py_compile` 只证明语法不崩，绝不当作"功能完成"；界面文案走 `tr()`+语言文件，**grep 中文找不到对应代码**，按目录/类名找。
8. **叙事/人设缰绳**：LLM 只讲故事不碰数值；prompt 喂事实清单（模板+变量），明令不报数字；预设文案永远是兜底（离线也有故事）；改 persona prompt 后必跑 `tools/persona_probe.py`。

---

## 8. 当前状态与已知待办

- **可用状态**：EXE 构建链路稳定（BUILD_EXIT=0 可复现），五个插件全部可用，用户存档在 `dist/DyberPet/data/`（元婴·初期，进度由用户持续挂机增长）。
- **默认角色**：韩立（settings.py 三处回退逻辑 + settings.json 已固化；用户指定）。
- **待用户实测**：附属宠物元婴的召唤链路（道具 → SubPet 跟随）逻辑上兼容、未实测。
- **调参点**：修为节奏（`cultivation_service.py` 顶部 RATES/NEEDS）、灵石产出率、丹药效果表 `PILL_EFFECTS`、炼丹配方 `ALCHEMY_RECIPES`、秘境表 `plugins/adventure/realms.py`——改完跑对应 tools 测试回归。
- **未做**（用户明确砍掉）：官方多 LLM 渠道、software_monitor 考古、action.dyberpet 导出兼容。
- **git**：按用户习惯，改动后**不自动 commit/push**，等用户验证后指示。

---

## 9. 给接手 AI 的工作方式建议（用户偏好速记）

- **先读代码再动手**，先诊断再行动；方案先给用户过目再执行（尤其数据库/破坏性操作）。
- 最小化改动，拒绝过度工程；交付物必须是可直接使用的生产级 EXE。
- 用户反馈问题（尤其截图）= 需求修正信号，先复现/定位根因，别急着改。
- 验证闭环永远是：编译 → offscreen 集成测试 → 冒烟 → 构建 → PYZ 检查 → 迁移存档 → 让用户双击实测。
- 用户不耐烦时（连着失败），停下来系统性审查，而不是继续试错。
