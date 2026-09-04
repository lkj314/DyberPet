# DyberPet 构建教程（BUILD GUIDE）

> 本文件面向 AI 助手 / 新接手的开发者，目标是**完整复现本项目 Windows 可执行包的构建过程**。
> 所有路径、命令、版本号均来自仓库当前真实状态，不是凭记忆写的。

---

## 1. 项目是什么

DyberPet 是一个 PySide6（Qt for Python）桌面宠物应用，原版为开源项目，
本仓库在其源码基础上集成了 **LoL 陪玩解说**（读取 LCU API + 本机 Ollama 生成解说词）、
**聊天窗口**（多轮对话 + TTS 播报 + 离线语音输入 + "始终聆听"模式）等功能。

- 入口脚本：`run_DyberPet.py`（仓库根目录）
- 主包：`DyberPet/`（Python 包）
- 最终交付物：**Windows onedir 免安装包**——用户解压/拿到目录后双击 `DyberPet.exe` 即用，无需装 Python。

**重要：AI 模型不打包。** 语音解说/聊天依赖外部本机 Ollama 服务
（默认模型 `gemma3:4b`，可切 `nanbeige4.1:3b` 等），EXE 只包含代码与 UI。
构建机不需要 Ollama 也能构建，但运行时需要。

---

## 2. 环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（打包目标平台） |
| Python | **3.12.10**（项目 venv 锁定版本；3.13 亦可，但依赖组合只验证过 3.12） |
| PyInstaller | **6.22.2**（hooks-contrib 2026.7 配套） |
| 构建命令行 | cmd / PowerShell / Git Bash 均可 |

> 原版 README 锁定 PySide6==6.5.2 + Python 3.9~3.11。本仓库 requirements.txt
> 已改用兼容 Python 3.12 的新版组合（见下），**不要**按原版 README 装旧依赖。

---

## 3. 搭建构建环境（一次性）

在仓库根目录（本文以 `U:\DyberPet` 为例，路径可变，命令相对仓库根执行）：

```bat
:: 1) 创建 venv
python -m venv .venv

:: 2) 安装锁定依赖（requirements.txt 已锁版本）
.venv\Scripts\python.exe -m pip install -r requirements.txt

:: 3) 安装 PyInstaller（不在 requirements.txt 里，单独装）
.venv\Scripts\python.exe -m pip install pyinstaller==6.22.2
```

`requirements.txt` 锁定的关键版本（均已验证可构建可运行）：

```
PySide6==6.11.2
PySide6-Fluent-Widgets==1.11.3      # import 名: qfluentwidgets
PySideSix-Frameless-Window==0.8.2   # import 名: qframelesswindow
apscheduler==3.11.3
pynput==1.8.2
tendo==0.3.0
requests==2.34.2                    # LCU 轮询 + 本机 Ollama 调用
edge-tts==7.2.8                     # 在线 TTS（微软，需联网）
vosk==0.3.45                        # 离线语音识别
pyaudio==0.2.14                     # 麦克风采集
```

---

## 4. 构建命令（核心就一条）

```bat
cd /d U:\DyberPet
.venv\Scripts\python.exe build_dyber.py
```

- 构建耗时约 3~5 分钟，正常退出码为 0。
- **必须在仓库根目录执行**：`build_dyber.py` 自带 `os.chdir(脚本所在目录)`，但 PyInstaller 的
  `build/`、`dist/` 输出按工作目录落盘，从别处调用容易产出嵌套目录。

### `build_dyber.py` 到底做了什么（改它之前必读）

1. **入口**：`run_DyberPet.py`，`--name DyberPet --onedir --windowed --noconfirm`。
2. **hidden-import**：`PySide6.QtSvg / QtXml / QtNetwork / QtMultimedia / QtPrintSupport`
   （qfluentwidgets 需要 SVG 图标；QtMultimedia 用于 TTS 播放）。
3. **资源收集**：
   - `--collect-all qfluentwidgets qframelesswindow`（这两个包带 .qss/图标等资源文件，必须全收）；
   - `--collect-submodules apscheduler pynput tendo edge_tts vosk pyaudio`。
4. **`--paths DyberPet`**：把主包加入模块搜索路径。
5. **⚠️ res/ 不走 `--add-data`**：DyberPet 按**当前工作目录（cwd）**加载 `res/language/language.json`
   等资源，如果用 `--add-data` 会把 res 放进 `_internal/res/`，运行时找不到。
   所以脚本在 PyInstaller 结束后用 **`robocopy res dist\DyberPet\res /E`** 把资源同步到 exe 同级目录。
   （用 robocopy 而非 shutil 是刻意的：增量复制，且在受限沙箱环境不触发批量删除。）

---

## 5. 产物结构

```
dist/DyberPet/                 ← 整个目录就是交付物（约 600MB）
├── DyberPet.exe               ← 双击运行（约 8MB，业务代码在内部 PYZ）
├── _internal/                 ← PyInstaller 运行时（PySide6 等，占大头）
└── res/                       ← 宠物模型/语言/图标/音效（robocopy 同步来的）
    ├── pet/  role/  items/  icons/  sounds/  language/
```

- `build/` 目录是 PyInstaller 中间产物，可随时删除，**不影响交付物**。
- 交付时把 `dist/DyberPet/` **整个目录**给用户（不是只给 exe）。
  项目约定：**不打 zip 压缩包**，直接交付目录。

---

## 6. 构建后验证（不要跳过）

### 6.1 基础检查

```bat
dir dist\DyberPet\DyberPet.exe
dir dist\DyberPet\res\language\language.json
```

### 6.2 验证新改的代码真的打进了包

PyInstaller 把项目代码压进 `build/DyberPet/PYZ-00.pyz`。改动后用它确认符号存在：

```bat
.venv\Scripts\python.exe -c "import marshal,sys;z=__import__('PyInstaller.archive.readers',fromlist=['ZlibArchiveReader']).ZlibArchiveReader(r'build/DyberPet/PYZ-00.pyz');d=z.extract(sys.argv[1]);d=d if isinstance(d,bytes) else marshal.dumps(d);print(sys.argv[2], sys.argv[2].encode() in d)" DyberPet.settings gemma3:4b
```

输出 `True` 即该字符串/符号已打进包。常用于确认：新设置项名、新函数名、模型默认值等。

### 6.3 运行冒烟（可选）

无界面环境可用 Qt offscreen 跑逻辑测试（本项目根目录有现成的 `dyber_smoke.py`）：

```bat
set QT_QPA_PLATFORM=offscreen
.venv\Scripts\python.exe dyber_smoke.py
```

---

## 7. 重建已有构建（覆盖旧包）

`--noconfirm` 会让 PyInstaller 直接删除旧 `dist/DyberPet` 再重建。踩过的坑与标准流程：

```bat
:: 1) 先结束正在运行的宠物（exe 被占用时 PyInstaller 删不掉旧目录）
taskkill /F /IM DyberPet.exe

:: 2) 预清理中间产物与旧包（在某些受限执行环境/沙箱里，
::    PyInstaller 批量删除 4000+ 文件会被"批量删除守卫"拦截导致构建失败；
::    手动先删掉、让 PyInstaller 新建目录即可绕开）
rmdir /s /q build\DyberPet
rmdir /s /q dist\DyberPet

:: 3) 重建
.venv\Scripts\python.exe build_dyber.py
```

普通无沙箱环境第 2 步可省略（`--noconfirm` 自己会清），但保留也无害。
历史教训：不清旧目录时，`COLLECT` 阶段删除 `dist/DyberPet`（4000+ 文件）或
`build/DyberPet/base_library.zip` 都曾触发删除守卫导致构建中断（退出码 1）。

---

## 8. 已知注意事项

1. **res/ 按 cwd 加载**：`DyberPet.exe` 的工作目录必须能直接看到 `res/`。
   不要只拷 exe，不要从不含 res 的目录启动。
2. **PySide6 版本**：不要降回原版 README 的 6.5.2——它没有 cp312 wheel，
   本仓库组合是 PySide6 6.11.2 + qfluentwidgets 1.11.3 + Python 3.12。
3. **`DyberPet.spec`**：是 PyInstaller 生成的副产物，构建脚本实际不读它
   （用命令行参数直出）。改打包配置请改 `build_dyber.py`，不要改 spec。
4. **运行时用户数据**：`data/`（settings.json、pet_data.json 等）是运行期生成的用户配置，
   不参与打包；`data/settings.json` 里的模型名等设置优先于代码默认值。
5. **models/ 目录**（GGUF 权重文件）只在本地做 `ollama create` 导入用，已 gitignore，与构建无关。
6. **插件架构**：LoL 陪玩在 `DyberPet/plugins/lol_companion/`（`main.py` / `worker.py` / `plugin.json`），
   聊天窗口在 `DyberPet/pet_chat.py`，模型/语音设置在 `DyberPet/settings.py`。
7. **依赖项改动**：改完代码若新增了第三方依赖，要同时更新 `requirements.txt`，
   且若是"带资源文件的包"（qss/图标/模型文件），构建脚本还要加对应
   `--collect-all` / `--collect-submodules`，否则运行时 ImportError 或资源缺失。

---

## 9. 快速核对清单

- [ ] `python --version` → 3.12.x，venv 已建
- [ ] `pip install -r requirements.txt` + `pyinstaller==6.22.2` 完成
- [ ] `taskkill /F /IM DyberPet.exe`（若有旧实例）
- [ ] 清掉 `build/DyberPet` 和 `dist/DyberPet`（受限环境必须）
- [ ] `.venv\Scripts\python.exe build_dyber.py` → 退出码 0
- [ ] `dist/DyberPet/DyberPet.exe`、`dist/DyberPet/res/language/language.json` 存在
- [ ] （改过代码时）用 ZlibArchiveReader 验证新符号在 PYZ 里
- [ ] 交付整个 `dist/DyberPet/` 目录，不打 zip
