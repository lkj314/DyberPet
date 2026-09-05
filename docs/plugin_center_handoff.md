# DyberPet 插件中心 —— Phase 0 工作交接文档

> 读者：接手做 EXE 构建验证 + 收尾的 AI（本机有 PySide6 + PyInstaller 6.22.2 环境）
> 作者：前序 AI（已完成代码层改动，但本环境无 PyInstaller，无法构建 EXE）
> 日期：2026-09-04

---

## 0. 一句话背景

用户要为这个 AI 桌宠（PyInstaller onedir + windowed 打包的 Windows EXE）开发**插件中心**。
现有代码里已经有一个"事实上的插件"——游戏 API 实时解说（`LoLCompanionWorker`，一个 QThread，通过 Qt Signal 把解说词/情绪投回 PetWidget），但它被**硬编码**在 4+ 处（`run_DyberPet.py` 实例化、`settings.py` 5 个全局变量、`BasicSettingUI.py` 6 张写死设置卡、`pet_chat.py` 复用其 LLM 核心）。

Phase 0 目标：把这套硬编码抽象成**插件系统地基**，并把现有的 LoL 解说迁进去当第一个真实插件，验证架构站得住。
Phase 1 才做"插件中心 UI"（按 `plugin.json` 的 `settings_schema` 自动渲染设置卡、插件列表/启停页），把现在写死的 `CompanionGroup` 换成动态生成。

---

## 1. 当前完成状态

| 维度 | 状态 | 说明 |
|------|------|------|
| 代码层改动 | ✅ 完成 | 全部源码已落地，`py_compile` 全量通过 |
| 构建配置 | ✅ 完成 | `build_dyber.py` 已改为自动扫描插件目录 |
| **EXE 构建验证** | ✅ **已完成** | 2026-09-04 本机构建 `BUILD_EXIT=0`，插件模块 `LoLCompanionPlugin` 进 PYZ，`plugin.json` 正确落到 `_internal/DyberPet/plugins/lol_companion/`（见下方「本机验证结论」）|
| GUI 运行时确认 | 🟡 部分完成 | offscreen 加载测试通过（discover→动态导入→实例化→on_load 全 OK）；完整 GUI 双击待用户本机确认解说插件加载/开关生效 |
| 提交 | ❌ 未做 | 按用户铁律，不自动 push；等你验证通过后帮他 commit |

> **本机验证结论（2026-09-04，接手 AI 执行）**：代码层本来就**没有构建阻塞**，`build_dyber.py` 当前版本（hidden-import + add-data、未用 `--collect-all DyberPet.plugins`）在本机构建成功。所谓「构建始终无法成功」最可能的真实原因是**构建前没做受控清理**——直接跑 `build_dyber.py` 时 PyInstaller 的 COLLECT 阶段会删旧 `dist/DyberPet`，被本环境批量删除守卫拦截而失败。务必先 `taskkill /F /IM DyberPet.exe` + `rmdir /s /q build\DyberPet dist\DyberPet` 再构建（§4.1）。另：前序 AI 因沙箱无 PyInstaller 从未真正构建过，文档里「未做」是沙箱限制而非代码缺陷。

**关键约束（用户多次强调）：这个项目是桌面 EXE，不是 `python run_DyberPet.py` 能跑的项目。验证闭环必须是"构建 EXE → 查 PYZ → 运行时确认"，不要再用 `python` 跑 app 或自己造 PySide6 桩代替构建验证。**

---

## 2. 改动文件清单（精确）

### 2.1 新增文件

| 文件 | 作用 |
|------|------|
| `DyberPet/llm_core.py` | 从旧 `lol_companion.py` 抽出的**共享 LLM 核心**：`Caster`（Ollama 调用，含 `style` 参数）、`GameDataReader`（读对局数据）、`caster_worker`（后台循环）、`emotion_for`（情绪判定）、`COMPANION_PROMPT`、`STYLE_PROMPTS`、`sanitize_commentary`。已做 **settings-free** 改造：`Caster.__init__` 加 `style="肥牛"` 参数，`caster_worker` 加 `cfg` 参数（由调用方传 `plugins_settings['lol_companion']`）。**`pet_chat.py` 复用此模块的 `Caster` / `COMPANION_PROMPT` / `sanitize_commentary`。** |
| `DyberPet/plugin_system/__init__.py` | 包标识 |
| `DyberPet/plugin_system/base.py` | `Plugin` 抽象基类：`on_load / on_enable / on_disable / on_unload` 生命周期钩子（注释约定 `on_load` 只调一次） |
| `DyberPet/plugin_system/api.py` | `PetAPI` 门面：插件**唯一**与宿主交互的入口。`pet.say/react/notify/bubble/use_item/add_menu` + `events.*`（绑 PetWidget/App 信号）+ `settings` + `app`。**插件禁止直接 import 内部模块**，只拿这个门面 |
| `DyberPet/plugin_system/manager.py` | `PluginManager`：扫 `plugins/` 文件夹 → 读 `plugin.json` → 校验 → `discover()`/`start_enabled()`/`stop_all()`/`set_enabled()`；含崩溃计数（`MAX_CONSECUTIVE_ERRORS=5`）自动禁用守护。**寻址逻辑**：`plugins_dir = dirname(dirname(__file__)) + '/plugins'`（冻结后 = `_internal/DyberPet/plugins`，与 `--add-data` 落点一致） |
| `DyberPet/plugins/__init__.py` | 包标识 |
| `DyberPet/plugins/lol_companion/__init__.py` | 包标识 |
| `DyberPet/plugins/lol_companion/plugin.json` | 插件 manifest：`id=lol_companion`、`entry=main:LoLCompanionPlugin`、`settings_schema`（enabled/model/style/reactions/bubble 五项的 key+default+type+label） |
| `DyberPet/plugins/lol_companion/main.py` | `LoLCompanionPlugin(Plugin)`：`on_enable` 里创建 `CompanionWorker`（QThread 桥接）并连 `PetAPI` 信号；`on_disable` 停线程 |
| `DyberPet/plugins/lol_companion/worker.py` | `CompanionWorker(QThread)`：包 `llm_core.caster_worker`，把解说词/情绪经 Signal 投出；由 `main.py` 实例化 |
| `docs/plugin_center_blueprint.md` | 总体蓝图（架构图 + 分阶段路线图） |
| `docs/plugin_center_phase0_plan.md` | Phase 0 实施方案审批稿（文件清单 + 精确 diff + 风险 + 验证） |

### 2.2 修改文件

**`run_DyberPet.py`** —— 去掉硬编码的 `LoLCompanionWorker`，改为插件系统引导：
```python
# 旧：from DyberPet.lol_companion import LoLCompanionWorker ... self.companion = LoLCompanionWorker(); 连信号; start()
# 新：
from DyberPet.plugin_system.manager import PluginManager
self.plugin_manager = PluginManager(pet_widget=self.p, app=self)
self.plugin_manager.discover()
self.plugin_manager.start_enabled()
settings.plugin_manager = self.plugin_manager
self.aboutToQuit.connect(self.plugin_manager.stop_all)
```
删除了 `_stop_companion` 方法（退出清理由 `plugin_manager.stop_all` 统一处理）。

**`DyberPet/settings.py`** —— 设置从"全局变量海"改为声明式 `plugins_settings` 字典：
- 新增全局 `plugins_settings`（落盘到 `data/settings.json`）
- `init()` 里：从旧顶层 `lol_companion_*` 键**迁移**进 `plugins_settings['lol_companion']`（兼容旧用户配置）
- `init()` 里：`nanbeige4.1:3b` 思考型模型自动降级为 `gemma3:4b`（该模型解说/对话会空回复）
- `save_settings()` 把 `plugins_settings` 写入 JSON
- **已删除** 5 个 `lol_companion_*` 顶层全局变量
- 注：本仓库 Windows 上 autocrlf，`git diff HEAD` 对 settings.py 可能显示为空（行尾噪音），但文件内容确实已改（grep 已确认 `plugins_settings` 存在、旧全局变量已移除）

**`DyberPet/DyberSettings/BasicSettingUI.py`** —— `CompanionGroup` 6 张设置卡（开关/模型/风格/情绪反应/气泡/刷新）的数据源从 `settings.lol_companion_*` 切到 `settings.plugins_settings['lol_companion']`：
- import 从 `DyberPet.lol_companion import list_ollama_models` → `DyberPet.llm_core import list_ollama_models`
- `_CompanionEnableChanged` 等 5 个回调写入 `plugins_settings['lol_companion'][key]`，且开关卡额外调用 `pm.set_enabled('lol_companion', ...)` 实时启停插件

**`DyberPet/pet_chat.py`** —— import 从 `DyberPet.lol_companion` 改 `DyberPet.llm_core`（复用 `Caster`/`COMPANION_PROMPT`/`sanitize_commentary`）；两处 `lol_companion_*` 全局访问改为 `plugins_settings['lol_companion']`

**`build_dyber.py`** —— 见第 3 节

### 2.3 删除文件

| 文件 | 原因 |
|------|------|
| `DyberPet/lol_companion.py` | 核心逻辑已拆为 `llm_core.py`（共享）+ `plugins/lol_companion/`（插件本体） |

### 2.4 依赖

**无新增第三方依赖**。`llm_core`/`plugin_system`/`plugins` 只用了 stdlib + 既有 `requests`/`PySide6`。`requirements.txt` 不必改。

---

## 3. 构建配置（最关键，前序 AI 在此栽过跟头）

### 3.1 构建入口只有一条

```
.venv\Scripts\python.exe build_dyber.py
```

- **`DyberPet.spec` 是 PyInstaller 生成的副产物，构建脚本不读它。不要改 spec。**（前序 AI 改过 spec，完全无效，已回退）
- 完整流程见 `BUILD_GUIDE.md`（本仓库必读文档）

### 3.2 插件为什么特殊、怎么打进 EXE

插件 `plugins/lol_companion/` 有两样 PyInstaller 静态分析**抓不到**的东西：
1. `main.py` 经 `importlib.import_module('DyberPet.plugins.lol_companion.main')` **动态导入**
2. `plugin.json` **数据文件**

所以必须显式告诉 PyInstaller 收进去。

### 3.3 `build_dyber.py` 当前写法（已验证语法、与项目既有 collect-* 模式一致）

```python
# 自动扫描 DyberPet/plugins/*/plugin.json，把资源文件和动态模块都打进 EXE
plugin_root = Path("DyberPet/plugins")
for plugin_dir in sorted(plugin_root.iterdir()):
    if not plugin_dir.is_dir():
        continue
    plugin_json = plugin_dir / "plugin.json"
    if not plugin_json.exists():
        continue
    pid = plugin_dir.name
    target = f"DyberPet/plugins/{pid}"
    opts.extend(["--add-data", f"{plugin_json.as_posix()}{SEP}{target}"])
    opts.extend(["--hidden-import", f"DyberPet.plugins.{pid}.main"])
    if (plugin_dir / "worker.py").exists():
        opts.extend(["--hidden-import", f"DyberPet.plugins.{pid}.worker"])
```

**为什么这样写（避坑）：**
- ❌ **禁止** `--collect-all DyberPet.plugins`：对主程序自己的子包用 `--collect-all` 会和 `run_DyberPet` 已分析的 `DyberPet` 主包 Analysis **冲突 → 构建直接失败**。这是用户最初"构建不成功"的真实原因。
- ❌ **不推荐** `--collect-data DyberPet.plugins`：对项目子包调用 collect-data 依赖 distribution 元数据，不可靠（前序 AI 曾用此写法，已替换为 add-data）。
- ✅ **采用** `--hidden-import` 收模块进 PYZ + `--add-data` 逐个插件收 `plugin.json` 到 `_internal/DyberPet/plugins/<pid>/`，与 `PluginManager` 按 `__file__` 相对寻址落点一致。
- 新增插件时，只要 `DyberPet/plugins/<newpid>/plugin.json` 存在，构建脚本**自动**生成对应选项，一般无需再手动改 `build_dyber.py`。

---

## 4. 验证闭环（你——接手者——必须在本机做）

> 前序 AI 所处沙箱无 PySide6 / PyInstaller，所有"验证"只到 `py_compile` + 导入链冒烟，**无法替代 EXE 构建**。以下才是真实验证。

### 4.1 构建

```bat
cd /d U:\DyberPet

:: 结束旧实例 + 清旧产物（受限环境必须，避免删除守卫拦截）
taskkill /F /IM DyberPet.exe
rmdir /s /q build\DyberPet
rmdir /s /q dist\DyberPet

:: 重建（退出码应为 0）
.venv\Scripts\python.exe build_dyber.py
```

### 4.2 基础产物检查

```bat
dir dist\DyberPet\DyberPet.exe
dir dist\DyberPet\res\language\language.json
```

### 4.3 确认插件模块真的进了 PYZ（应输出 True）

```bat
.venv\Scripts\python.exe -c "import marshal,sys;z=__import__('PyInstaller.archive.readers',fromlist=['ZlibArchiveReader']).ZlibArchiveReader(r'build/DyberPet/PYZ-00.pyz');d=z.extract('DyberPet.plugins.lol_companion.main');d=d if isinstance(d,bytes) else marshal.dumps(d);print('LoLCompanionPlugin in PYZ:', b'LoLCompanionPlugin' in d)"
```

### 4.4 确认 plugin.json 资源被收进 _internal

```bat
dir dist\DyberPet\_internal\DyberPet\plugins\lol_companion\plugin.json
```

### 4.5 运行时确认（最重要）

双击 `dist/DyberPet/DyberPet.exe`，重点确认：
- 启动无报错（插件 discover → start_enabled 正常）
- 设置面板里「Lober/陪玩」分组 6 张卡正常显示、读写 `plugins_settings`
- 开关卡能**即时**启停解说线程（调用 `pm.set_enabled`）
- 游戏内或模拟对局时，宠物能正常解说 + 情绪反应

---

## 5. 已知风险 / 待确认

1. **插件寻址在冻结后是否 100% 对**：`manager.py` 用 `dirname(dirname(__file__)) + '/plugins'`。`__file__` 冻结后指向 `_internal/DyberPet/plugin_system/manager.py`，所以 plugins 目录 = `_internal/DyberPet/plugins`，与 `--add-data` 落点一致。逻辑上成立，但**需 4.4 步骤实证**。若实际路径有偏差（如多一层 `_internal`），按 4.4 结果微调 `manager.py` 寻址即可。

2. **旧用户配置迁移**：`settings.init()` 把旧 `lol_companion_*` 顶层键迁移进 `plugins_settings['lol_companion']`。新用户直接用 schema 默认种子。已处理，但需运行时确认迁移路径不报错。

3. **`nanbeige4.1:3b` 降级**：若该模型在旧配置里，会自动切 `gemma3:4b`。属防御性代码，需确认不意外覆盖用户显式设置。

4. **接手者若构建失败**：把**完整终端报错**贴回，按真实错误定位。不要再用 `--collect-all DyberPet.plugins`（已知会冲突）。

---

## 6. 给接手 AI 的"不要再做"清单

- ❌ 不要改 `DyberPet.spec`（构建不读它）
- ❌ 不要对主程序子包用 `--collect-all DyberPet.plugins`（已知构建冲突）
- ❌ 不要用 `python run_DyberPet.py` 或自己造 PySide6 桩来代替 EXE 构建验证
- ❌ 不要自动 `git push`（用户铁律：先审批再执行，且常驻授权仅适用于用户明确允许的仓库）
- ✅ 只改 `build_dyber.py` 做打包配置
- ✅ 验证必须走 §4 的真实构建闭环

---

## 7. 后续 Phase 1 方向（供参考，本次不要求）

1. 按 `plugin.json` 的 `settings_schema` **自动渲染**设置卡，删除 `BasicSettingUI.py` 写死的 `CompanionGroup`
2. 插件列表/启停页（"插件中心" UI 本体）
3. 更多钩子（Phase 2+）：菜单/通知/定时任务等事件总线补全、插件沙箱/权限、插件市场分发

---

## 8. 关键文件速查

| 想了解 | 看 |
|--------|----|
| 整体架构 + 路线图 | `docs/plugin_center_blueprint.md` |
| Phase 0 精确方案 + diff | `docs/plugin_center_phase0_plan.md` |
| 本仓库构建教程 | `BUILD_GUIDE.md`（§6/§8.7/§9 最重要） |
| 插件管理器逻辑 | `DyberPet/plugin_system/manager.py` |
| 插件门面 API | `DyberPet/plugin_system/api.py` |
| 首个插件本体 | `DyberPet/plugins/lol_companion/` |
| 打包脚本 | `build_dyber.py` |
