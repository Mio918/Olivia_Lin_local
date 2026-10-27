# BSide: Olivia Lin · 本地离线增强补丁

《BSide: Olivia Lin（林离）》官方服务器停运后的离线改造：不动游戏本体架构，用本地服务端 + 客户端补丁，把**写信、AI 回信、本地曲库、桌面壁纸 MV** 全部救回来。

> 逆向分析报告见 [`analysis/REPORT.md`](analysis/REPORT.md)，服务端详细文档见 [`mockserver/README.md`](mockserver/README.md)。

## 功能

- **写信 + AI 回信**：本地 mock 服务接管 `/toy/letter/*`，接任意 OpenAI 兼容接口（DeepSeek / Ollama / LM Studio…）；人设可编辑（`persona.md`，改完即时生效）；回信带随机延迟（默认 30–120 分钟）、严格按写信顺序回复、每日限额、服务重启后逾期信件自动补发
- **本地曲库**：`mockserver/media/` 扫描成歌单，`import_song.py` 一条命令导入 mp3/wav/flac/mp4；命中官方歌单的曲目自动套用原版元数据（乐评、波形、按时段选画面）
- **桌面壁纸 MV**：点歌直接在桌面壁纸层播放（mpv + Win32 `SetParent` 到 WorkerW），游戏 UI 进度条/播放状态正常联动，停止后完美恢复原生房间场景
- **零依赖服务端**：`server.py` 纯 Python 标准库，单进程单端口（写信 + 回信调度 + 曲库媒体流 + 壁纸播放）

## 目录结构

```
├── mockserver/          本地服务端（主交付物）
│   ├── server.py        服务本体（标准库零依赖）
│   ├── wallpaper.py     桌面壁纸 MV 播放器
│   ├── patch_feapp.py   客户端补丁 / 回滚
│   ├── import_song.py   曲目导入工具
│   ├── persona.md       人设文件（可编辑）
│   ├── config.example.json  配置模板（复制为 config.json 使用）
│   └── START_SILENT.vbs / STOP_SILENT.vbs  静默启停
├── analysis/            逆向阶段产物（报告、桥接协议、解包脚本）
├── tools/               自写工具（截屏、UI 点击、PE 解析）+ mpv 运行时
├── reference/           参考项目的人设与补丁脚本
└── feapp_unpacked/      解包后的前端源码（仅本地使用，不入库）
```

## 快速开始

```bash
# 0. 准备配置
cd mockserver
copy config.example.json config.json   # 填入你的 LLM api_key（留空则用内置模板回信）

# 1. 起服务
py -3.12 server.py --port 8787         # 或双击 START_SILENT.vbs

# 2. 打补丁（先完全退出游戏，包括托盘）
py -3.12 patch_feapp.py --api-base http://127.0.0.1:8787 --apply

# 3. 启动游戏 → 角色旁边的写信入口
```

回滚：`py -3.12 patch_feapp.py --restore`（首次 apply 会自动备份原版 feapp.dat）。

### 壁纸播放器

需要 mpv：下载 [shinchiro 构建](https://github.com/shinchiro/mpv-winbuild-cmake/releases) 解压到 `tools/mpv/mpv.exe`。
导入曲目后，游戏曲库点歌即可上桌面壁纸；`py -3.12 tools/wallpaper_player.py` 也可独立控制。

### 导入曲目

```bash
py -3.12 import_song.py 歌名.mp3      # 自动转码 + 元数据 + 封面
```

或直接把符合命名规则的视频放进 `mockserver/media/<曲目>/`。

## 免责声明

- 本项目仅供学习研究，需要自备正版游戏本体；**不包含、不分发任何游戏资源与媒体文件**（曲库、解包源码、补丁产物均已在 `.gitignore` 排除）
- 请勿将本项目用于商业用途
