# -*- coding: utf-8 -*-
"""
DyberPet 打包脚本（onedir + windowed）

把"已集成 LoL 陪玩功能的 DyberPet 源码"打成 Windows 可执行程序：
  - 入口：run_DyberPet.py
  - 资源：res/ 整个目录（宠物/角色/语言/图片）必须随包，DyberPet 按 cwd 加载 res/language/language.json
  - onedir：exe 在 dist/DyberPet/DyberPet.exe，旁边带 res/ 与 _internal/，双击即运行，无需 Python

用法（在仓库根目录执行）：
  .venv\Scripts\python.exe build_dyber.py
"""
import os
import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

# Windows 下 --add-data / --add-binary 用分号分隔
SEP = ";"  # Windows

opts = [
    "run_DyberPet.py",
    "--name", "DyberPet",
    "--onedir",
    "--windowed",          # 无控制台黑框，双击即用
    "--noconfirm",
    # 注：res/ 不通过 --add-data 挂载（会被放到 _internal/res，
    #     而 DyberPet 按 cwd 加载 res/）。改为打包后用 robocopy 同步到 exe 同级目录。
    # PySide6 子模块（qfluentwidgets 依赖 SVG 图标等）
    "--hidden-import", "PySide6.QtSvg",
    "--hidden-import", "PySide6.QtXml",
    "--hidden-import", "PySide6.QtNetwork",
    "--hidden-import", "PySide6.QtMultimedia",
    "--hidden-import", "PySide6.QtPrintSupport",
    # qfluentwidgets / qframelesswindow 带资源文件（.qss / 图标），用 collect-all 全收
    "--collect-all", "qfluentwidgets",
    "--collect-all", "qframelesswindow",
    # 第三方包子模块（apscheduler 作者专门修过 pyinstaller bug）
    "--collect-submodules", "apscheduler",
    "--collect-submodules", "pynput",
    "--collect-submodules", "tendo",
    # 项目包路径
    "--paths", "DyberPet",
]

if __name__ == "__main__":
    PyInstaller.__main__.run(opts)
    # res/ 必须放在 exe 同级目录（DyberPet 按 cwd 加载 res/）。
    # 用 robocopy 增量同步，避免触发本环境的批量删除守卫。
    import subprocess
    src = os.path.join(HERE, "res")
    dst = os.path.join(HERE, "dist", "DyberPet", "res")
    r = subprocess.run(
        ["robocopy", src, dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS"],
        check=False,
    )
    print("Synced res/ ->", dst, "(robocopy exit", r.returncode, ")")
