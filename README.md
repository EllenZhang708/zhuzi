<p align="center"><img src="assets/AppIcon.png" width="96" alt=""></p>

<h1 align="center">逐字</h1>

<p align="center">把播客誊成文字。贴一个小宇宙链接，或拖入一段音频，半分钟拿到带时间戳的逐字稿。</p>

<p align="center"><img src="docs/screenshot-light.png" width="720" alt="逐字的主界面"></p>

## 能做什么

- **贴链接就能转**：支持小宇宙单集链接，也支持本地的 MP3、M4A、WAV 等音频。
- **很快**：云端识别一期 80 分钟的节目，大约半分钟。
- **免费**：用的是硅基流动和 Groq 的免费额度；你用自己的密钥，额度归你自己。
- **也能离线**：本机离线识别（SenseVoice）不联网、不上传音频，首次使用下载约 230 MB 的模型。
- **好读的稿子**：自动加标点、分段、每段带时间戳，输出 Markdown，可以直接放进 Obsidian 等笔记软件。
- **识别更准**：自动把节目名、主播名告诉识别模型，并用拼音纠正同音错字；会过滤语音模型在片头音乐上“编”出来的句子。

## 下载

在 [Releases](../../releases) 页面下载：

| 你的电脑 | 安装版 | 免安装版 |
|---|---|---|
| 大多数 Windows 电脑（Intel / AMD） | `Zhuzi-x.y.z-x64-setup.exe` | `Zhuzi-x.y.z-x64-portable.zip` |
| 骁龙等 ARM 芯片的 Windows 电脑 | `Zhuzi-x.y.z-arm64-setup.exe` | `Zhuzi-x.y.z-arm64-portable.zip` |

不确定是哪种？打开「设置 → 系统 → 系统信息」，看「系统类型」：写着“基于 ARM”就选 arm64，否则选 x64。

> 第一次运行时，Windows 可能提示“Windows 已保护你的电脑”或“未知发布者”——这是因为本项目没有购买代码签名证书。点「更多信息 → 仍要运行」即可。所有代码都公开在本仓库，可以自行检查或构建。

## 第一次使用：拿一个免费密钥（约一分钟）

打开逐字时会出现欢迎向导，选一家云端服务：

| 服务 | 适合 | 免费额度 | 拿密钥 |
|---|---|---|---|
| **硅基流动**（SenseVoice） | 中国大陆用户 | 语音识别模型免费 | [cloud.siliconflow.cn](https://cloud.siliconflow.cn/account/ak) 用手机号注册，**先完成实名认证**，再在「API 密钥」页新建 |
| **Groq**（Whisper large-v3-turbo） | 海外用户 | 每天约 8 小时音频 | [console.groq.com](https://console.groq.com/keys) 注册，点 Create API Key |

两家都填的话，一家连不上时会自动换另一家。也可以点「先用本机离线识别」跳过这一步。

<p align="center"><img src="docs/screenshot-setup.png" width="480" alt="欢迎向导"></p>

## 隐私

- **云端识别**：音频会被压缩后上传到你选择的服务商（硅基流动或 Groq）处理，适用它们各自的条款。上传前会在本机去掉音乐和静音片段。
- **本机离线识别**：音频不会离开你的电脑。
- **密钥**：用 Windows 的 DPAPI 加密保存在本机，只有你这个 Windows 账户能解开。
- 逐字没有自己的服务器，不收集任何数据。

## 版权提醒

小宇宙链接功能仅为方便个人学习和笔记整理。播客内容的版权属于原作者，请勿将转写稿用于转载、商用或其他侵权用途。本项目与小宇宙、各播客节目均无关联。

## 输出示例

```markdown
---
title: "第 12 期｜声音停下来的地方｜逐字稿"
podcast: "示例播客"
source: "https://www.xiaoyuzhoufm.com/episode/…"
duration: "01:02:05"
model: "Groq whisper-large-v3-turbo"
---

## 完整逐字稿

`00:01:01` 欢迎来到新一期的节目。今天想和你聊聊，声音和文字之间到底隔着什么……
```

## 从源码构建

需要 Windows 10/11 和 Python 3.12。

```powershell
python -m venv .venv-x64                # ARM 电脑用 ARM64 版 Python 建 .venv-arm64
.venv-x64\Scripts\python -m pip install -r requirements.txt
.venv-x64\Scripts\python src\app.py     # 直接运行
.venv-x64\Scripts\python tests\test_text.py; .venv-x64\Scripts\python tests\test_cloud.py
.\build.ps1 -Arch x64                    # 打包到 dist\
```

安装程序由 `installer\zhuzi.iss`（Inno Setup 6.5+）生成；推送 `v*` 标签后，GitHub Actions 会自动构建 x64 与 ARM64 两个版本并起草 Release。

代码结构：

| 文件 | 作用 |
|---|---|
| `src/app.py` | 界面（PySide6） |
| `src/core.py` | 任务流程、小宇宙解析、下载、文字整理、Markdown |
| `src/cloud.py` | 云端识别：硅基流动、Groq，自动切换 |
| `src/local.py` | 本机离线识别：SenseVoice（sherpa-onnx） |
| `src/audio.py`、`src/vad.py` | 解码、切段、压缩；Silero 人声检测 |
| `src/secrets_store.py` | 密钥加密（Windows DPAPI） |

## 许可证

本项目代码以 [MIT](LICENSE) 许可发布。发行版中包含的第三方组件及其许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## English

**Zhuzi (逐字)** turns a podcast episode — a Xiaoyuzhou link or a local audio file — into a timestamped Markdown transcript, in about half a minute for an 80-minute episode. It uses the free tiers of SiliconFlow (SenseVoice, reachable in mainland China) or Groq (Whisper large-v3-turbo), with your own key, or runs fully offline with SenseVoice. Native builds for Windows x64 and ARM64. Keys are encrypted with DPAPI; there is no server and no telemetry. MIT licensed; see THIRD_PARTY_NOTICES.md for bundled components.
