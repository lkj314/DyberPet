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

### 4.1 标准命令（推荐：先 cd 进仓库根）

```bat
cd /d U:\DyberPet
.venv\Scripts\python.exe build_dyber.py
```

- 构建耗时约 3~5 分钟，正常退出码为 0。
- **必须在仓库根目录执行**：虽然 `build_dyber.py` 自带 `os.chdir(脚本所在目录)`，但 PyInstaller 在
  生成临时 `DyberPet.spec` 时会参考调用者的当前工作目录（CWD）。从 `C:\WINDOWS\system32` 之类无写权限的
  目录调用，会报 `PermissionError: [Errno 13] Permission denied: 'C:\WINDOWS\system32\DyberPet.spec'`。
  为避免这类问题，脚本已强制 `--specpath` 指向项目根，但仍建议**先 `cd /d U:\DyberPet` 再执行**。

### 4.2 PowerShell 用户特别注意

PowerShell 中，以 `.` 开头的路径会被解析为**模块名**而不是文件路径。直接敲 `.venv\...` 会报错：

```
无法加载模块“.venv”。有关详细信息，请运行“Import-Module .venv”。
CategoryInfo : ObjectNotFound: (.venv\Scripts\python.exe:String) [], CommandNotFoundException
```

PowerShell 里当前目录下的可执行文件必须加 `\.\`：

```powershell
cd /d U:\DyberPet
.\.venv\Scripts\python.exe build_dyber.py
```

或直接用绝对路径（此时也要先 cd 到项目根，避免 CWD 问题）：

```powershell
cd /d U:\DyberPet
U:\DyberPet\.venv\Scripts\python.exe U:\DyberPet\build_dyber.py
```

cmd.exe 里 `.venv\...` 可以直接跑，PowerShell 里必须 `.\.venv\...`。很多 AI 会忽略这个差异而给出在 PowerShell 下必然失败的命令。

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

`--noconfirm` 会让 PyInstaller 直接删除旧 `dist/DyberPet` 再重建。但在某些受限执行环境/沙箱里，
批量删除 4000+ 文件会被"批量删除守卫"拦截，导致 `COLLECT` 阶段退出码 1：

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":4054,"threshold":50,...}
```

本脚本已自动处理：在调用 PyInstaller 之前，会先把已存在的 `build/DyberPet` 和 `dist/DyberPet`
**整体重命名**为 `*.old.YYYYMMDD_HHMMSS`（O(1) 目录重命名，不会被守卫拦截），然后让 PyInstaller
创建全新的目录。因此标准流程简化为：

```bat
:: 1) 先结束正在运行的宠物（exe 被占用时 PyInstaller 写不进去）
taskkill /F /IM DyberPet.exe

:: 2) 直接重建（脚本会自动 rotate 掉旧目录）
cd /d U:\DyberPet
.venv\Scripts\python.exe build_dyber.py
```

如果出于某种原因需要手动清理，**不要用 `rmdir /s /q` 或 `rm -rf`**（大概率触发守卫）。
正确做法是**重命名**或移走：

```bat
:: 把旧目录改名为 backup，而不是删除
move build\DyberPet build\DyberPet.bak
move dist\DyberPet dist\DyberPet.bak
```

历史教训：
- 不清旧目录时，`COLLECT` 阶段删除 `dist/DyberPet`（4000+ 文件）会触发删除守卫导致构建中断（退出码 1）。
- 手动 `rm -rf dist/DyberPet` 在受限环境也会被守卫拦截，所以不要依赖它。

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
- [ ] **先 `cd /d U:\DyberPet`**，再执行构建（不要从 system32 等目录直接跑）
- [ ] cmd 用 `.venv\Scripts\python.exe`；PowerShell 必须用 `.\.venv\Scripts\python.exe`
- [ ] `.venv\Scripts\python.exe build_dyber.py` → 退出码 0（脚本会自动 rotate 旧目录，无需手动 `rmdir`）
- [ ] `dist/DyberPet/DyberPet.exe`、`dist/DyberPet/res/language/language.json` 存在
- [ ] （改过代码时）用 ZlibArchiveReader 验证新符号在 PYZ 里
- [ ] 交付整个 `dist/DyberPet/` 目录，不打 zip

---

## 10. 给 AI 助手 / 接手者的执行须知（血泪教训）

前面 §1–§9 讲"怎么构建"。这一节专讲一个**反复踩坑的认知陷阱**：AI 在受限执行沙箱里没有 PyInstaller，就误以为"这个项目构建不了，必须让用户手动"，甚至甩回一行在用户机器上也跑不通的命令。

**核心事实**：构建是在**用户的机器**上跑的，不是在你的执行沙箱里。本项目根目录自带 `.venv`，里面已经装好了 `PyInstaller 6.22.2` + `PySide6 6.11.2` 全套依赖。只要在那个目录里用 `.venv\Scripts\python.exe` 跑 `build_dyber.py`，就能成功。**你沙箱里有没有 PyInstaller，跟项目能不能构建毫无关系。**

**DO（照做）：**
1. 先确认工具链在不在：在仓库根目录执行
   `.venv\Scripts\python.exe -c "import PyInstaller, PySide6; print(PyInstaller.__version__)"`
   能打印版本号就说明**可以构建**，直接往下走。
2. **必须先 `cd /d U:\DyberPet`**，再从项目根调用构建脚本。不要从 `C:\WINDOWS\system32` 等无关目录用绝对路径直接跑，否则 PyInstaller 写临时 `DyberPet.spec` 时会因 CWD 无权限而失败。
3. PowerShell 下当前目录的可执行文件路径必须写成 `.\.venv\Scripts\python.exe`，不是 `.venv\Scripts\python.exe`（后者会被 PowerShell 当成模块名）。cmd 下可直接写 `.venv\...`。
4. 用项目自带的 `.venv` 跑构建（脚本会自动 rotate 旧目录，避开批量删除守卫）：
   ```bat
   cd /d U:\DyberPet
   taskkill /F /IM DyberPet.exe
   .venv\Scripts\python.exe build_dyber.py
   ```
   PowerShell 版本：
   ```powershell
   cd /d U:\DyberPet
   taskkill /F /IM DyberPet.exe
   .\.venv\Scripts\python.exe build_dyber.py
   ```
   注意：**不要**在命令前加 `rmdir /s /q` 或 `Remove-Item -Recurse`——
   在受限环境会触发批量删除守卫，反而导致 PyInstaller 在 `COLLECT` 阶段失败。
   脚本开头的 `_rotate_away()` 已经用目录重命名代替删除。
5. 看退出码：0 即成功；非 0 把**完整终端报错**贴出来定位，别直接判死刑。
6. 改过代码后按 §6.2 用 ZlibArchiveReader 验证新符号进了 PYZ；插件类还要确认 `plugin.json` 落到了 `_internal/DyberPet/plugins/<pid>/`。

**DON'T（禁止）：**
1. ❌ 因为你自己的沙箱没装 PyInstaller，就结论"项目无法自动构建、必须由用户手动"。这是把**你环境的限制**当成**项目的限制**。
2. ❌ 甩一行在你沙箱里能想出来的、但用户机器上必然失败的命令（例如在沙箱裸 `python` 里跑 `pyinstaller`、或让用户 `pip install pyinstaller` 后裸跑 `build_dyber.py`、或去改 `DyberPet.spec`）。
3. ❌ 在 PowerShell 里给用户 `.venv\Scripts\python.exe build_dyber.py` 这种命令——它会把 `.venv` 当模块名，报 `CouldNotAutoLoadModule`。正确写法是 `.\.venv\Scripts\python.exe`。
4. ❌ 让用户从 `C:\WINDOWS\system32` 之类目录用绝对路径直接跑构建脚本。PyInstaller 的临时 spec 会写到 CWD，无权限目录直接 `PermissionError`。必须先 `cd /d U:\DyberPet`。
5. ❌ 改 `DyberPet.spec`。构建脚本根本不读它（见 §8.3）；前序 AI 改过，完全无效。
6. ❌ 用 `python run_DyberPet.py` 或自己造 PySide6 桩来"代替"EXE 构建验证。这验证不了打包问题。

**一句话总结**：能跑 `.venv` 就跑 `.venv`；跑不了就如实说"我在当前环境执行不了，但正确命令是 X，且命令本身已验证可行"——**绝不要**编造失败命令，也**绝不要**判定必须手动。
