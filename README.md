<div align="center">

<img src="docs/DyberPet.png" alt="DyberPet" width="260"/>

# DyberPet 肥牛 · AI 魔改版

**端侧 AI 桌宠：把修仙、冒险和游戏陪玩装进你的桌面，数据不出本机。**

[![License](https://img.shields.io/github/license/lkj314/DyberPet.svg)](./LICENSE)
[![Downloads](https://img.shields.io/github/downloads/lkj314/DyberPet/total.svg)](../../releases)
![Version](https://img.shields.io/badge/DyberPet-v0.6.7--ai.1-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Ollama](https://img.shields.io/badge/AI-Ollama%20%E7%AB%AF%E4%BE%A7-black)

简体中文 · [English](./README_EN.md)

**[📥 下载 exe（Release）](../../releases/latest)** · **[🛠️ 构建指南](./BUILD_GUIDE.md)** · **[🧩 插件开发](./docs/plugin_center_blueprint.md)**

</div>

---

## 这不是官方 DyberPet 的新版本

本项目 fork 自官方开源版 **0.6.7**（纯桌宠框架），此后**完全独立演进**，自研了全部 AI 能力。

> **路线分歧，一句话说清**：官方 v0.8+ 转向**云端多渠道 LLM**（OpenAI / Gemini / OpenRouter）；
> 本仓库坚持 **端侧 Ollama 本地 AI** —— 不申请 API key、无用量计费、对话数据不出你的电脑。
> 两套底层互不兼容，我们也不打算合并回官方路线。

在官方桌宠框架之上，我们新增了一整套玩法与系统（下表 ✨ 标记部分均为本仓库自研，官方版不存在）。

## 功能全景

| 板块 | 能力 |
|---|---|
| 🐶 桌宠本体 | 动画 / 拖拽 / 喂食 / 好感度 / 摸头 / 商店 / 昼夜作息（继承自官方 0.6.7） |
| ✨ AI 对话 | 多轮对话 · TTS 语音 · 离线语音输入（vosk）· 「始终聆听」模式，全部走本机 Ollama |
| ✨ 插件中心 | 插件发现 / 启停 / 按 `settings_schema` 自动渲染图形化设置卡 |
| ✨ 修仙放置 | 挂机涨修为，炼气 → 真仙 **40 阶**；突破 / 顿悟 / 双修 / 丹药 / 炼丹，与商店货币（灵石）经济打通 |
| ✨ 世界冒险 | 旅行青蛙式缺席叙事：御剑离场 · 元婴留守 · 传讯符 · 秘境历练 |
| ✨ 游戏陪玩 | 五子棋 / 斗地主（AI 对手 + 桌宠实时吐槽 + 胜利联动修为/历练） |
| ✨ 英雄联盟陪玩 | LCU 实时解说 · 结算战报（全队 KDA → 五境界分档）· 自动接受对局 / 自动点赞 / 自动回房 |
| ✨ 角色面板 / 图鉴 | 状态 / 背包 / 商店 / 任务 / 修仙 / 历练 / 角色图鉴 |

配套角色资产：**韩立**（927 帧 / 40 动作）、**银月**（125 帧 / 7 动作）、附属宠物 **韩立元婴** 与 **道韵元婴**、《凡人修仙传》丹药道具包。

## 快速开始

### 方式一：直接下载（推荐）

到 [**Releases**](../../releases/latest) 下载 `DyberPet-v*-win64.zip`，解压后双击 `DyberPet/DyberPet.exe` 即可——无需安装 Python。

### 方式二：AI 功能（可选）

桌宠本体无需任何配置即可游玩。启用 AI 对话 / 吐槽 / 解说：

1. 安装 [Ollama](https://ollama.com/)
2. 拉取模型：`ollama pull gemma3:4b`（默认模型，可在设置中更换）

### 方式三：源码运行

```bash
git clone https://github.com/lkj314/DyberPet.git
cd DyberPet
pip install -r requirements.txt
python run_DyberPet.py
```

## 构建 EXE

完整打包教程见 **[BUILD_GUIDE.md](./BUILD_GUIDE.md)**。一句话版本：

```bash
.venv\Scripts\python.exe build_dyber.py
```

产物在 `dist/DyberPet/DyberPet.exe`（PyInstaller onedir + windowed）。

## 面向开发者

- **插件系统**：在 `DyberPet/plugins/<插件名>/` 放 `plugin.json`（清单 + settings_schema）+ `main.py`（入口类）即可被 `PluginManager` 自动发现；插件通过 `PetAPI` 门面与桌宠交互（气泡 / 通知 / 表情 / 修为联动 / 设置持久化）。参考 `docs/plugin_center_blueprint.md`。
- **Core 服务**：修为（`cultivation_service`）、冒险（`adventure_service`）、人设（`persona_service`）三个无 Qt 纯逻辑服务是数值唯一权威，插件只做 UI 与演出。
- **美术提取**：`tools/unpack_dyberpet.py` 可将官方 `action.dyberpet` 包解包为散图。
- **LCU 探测**：`tools/lcu_probe.py` 用于核对英雄联盟客户端接口返回。

## 致谢

- [ChaozhongLiu/DyberPet](https://github.com/ChaozhongLiu/DyberPet) —— 官方原项目，桌宠框架与部分角色美术的源头
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) —— 设置中心 UI 组件
- 韩立 / 银月等角色美术提取自官方发行包，版权归原权利方所有

## 免责声明

本项目为学习交流用途。英雄联盟相关自动化（自动接受 / 点赞 / 回房）仅操作客户端界面、不涉及游戏内操作，使用产生的账号风险自行承担。

## License

[GPL-3.0](./LICENSE)（继承自官方原项目）
