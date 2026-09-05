<div align="center">

<img src="docs/DyberPet.png" alt="DyberPet" width="260"/>

# DyberPet · AI Remastered

**A desktop pet with fully local AI — cultivation, adventures and game companionship on your desktop, data never leaves your machine.**

[![License](https://img.shields.io/github/license/lkj314/DyberPet.svg)](./LICENSE)
[![Downloads](https://img.shields.io/github/downloads/lkj314/DyberPet/total.svg)](../../releases)
![Version](https://img.shields.io/badge/DyberPet-v0.6.7--ai.1-green)
![Python](https://img.shields.io/badge/python-3.12-blue)
![Ollama](https://img.shields.io/badge/AI-local%20Ollama-black)

[简体中文](./README.md) · English

**[📥 Download (Releases)](../../releases/latest)** · **[🛠️ Build Guide](./BUILD_GUIDE.md)**

</div>

---

## This is NOT an official DyberPet release

This project forked the open-source **DyberPet 0.6.7** (a pure desktop-pet framework) and has since evolved **entirely on its own**, with all AI capabilities built from scratch.

> **The route split in one sentence**: official v0.8+ moved to **cloud LLM channels** (OpenAI / Gemini / OpenRouter);
> this repo sticks to **local Ollama AI** — no API keys, no usage billing, and your conversation data never leaves your computer.

## Features

| Module | What you get |
|---|---|
| 🐶 Desktop pet | Animations / dragging / feeding / affection / shop / day-night cycle (inherited from 0.6.7) |
| ✨ AI chat | Multi-turn chat · TTS voice · offline speech input (vosk) · always-listening, all via local Ollama |
| ✨ Plugin center | Plugin discovery / toggles / auto-rendered settings UI |
| ✨ Cultivation | Idle EXP grind, **40 realms** from Qi Refining to True Immortal; breakthroughs, alchemy, spirit-stone economy |
| ✨ Adventures | Frog-style away narratives: sword-flight departures, Nascent Soul guardian, realm expeditions |
| ✨ Game buddy | Gomoku & Dou Dizhu with AI opponents and pet commentary |
| ✨ League of Legends | LCU live commentary · end-of-game battle reports (team KDA → five cultivation realms) · auto accept / honor / back-to-lobby |

Character assets: **Han Li** (927 frames / 40 actions), **Yin Yue** (125 frames / 7 actions), sub-pets **Han Li Nascent Soul** & **Daoyun Nascent Soul**, and a *A Record of a Mortal's Journey to Immortality* pill pack.

## Quick start

1. Download `DyberPet-v*-win64.zip` from [**Releases**](../../releases/latest), unzip and run `DyberPet/DyberPet.exe` — no Python needed.
2. Optional AI features: install [Ollama](https://ollama.com/), then `ollama pull gemma3:4b`.
3. From source: `pip install -r requirements.txt && python run_DyberPet.py`.

Build the EXE yourself with `.venv\Scripts\python.exe build_dyber.py` — see [BUILD_GUIDE.md](./BUILD_GUIDE.md).

## Credits & License

Based on [ChaozhongLiu/DyberPet](https://github.com/ChaozhongLiu/DyberPet) (desktop-pet framework and part of the art), UI components by [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets). For study purposes only; LoL automation operates the client UI only — use at your own risk. Licensed under [GPL-3.0](./LICENSE).
