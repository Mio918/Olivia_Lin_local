# BSide: Olivia Lin · 本地离线增强补丁

《BSide: Olivia Lin（林离）》官方服务器停运后的离线改造：不动游戏本体架构，用本地服务端 + 客户端补丁，把**写信、AI 回信、本地曲库、桌面壁纸 MV** 全部救回来。

> 逆向分析报告见 [`analysis/REPORT.md`](analysis/REPORT.md)，服务端详细文档见 [`mockserver/README.md`](mockserver/README.md)。

## 功能

- **写信 + AI 回信**：本地 mock 服务接管 `/toy/letter/*`，接任意 OpenAI 兼容接口（DeepSeek / Ollama / LM Studio…）；人设可编辑（`persona.md`，改完即时生效）；回信带随机延迟（默认 30–120 分钟）、严格按写信顺序回复、每日限额、服务重启后逾期信件自动补发
- **本地曲库**：`mockserver/media/` 扫描成歌单，支持导入任意音频/视频（见下文[添加自己的视频资源](#添加自己的视频资源)）；命中官方歌单的曲目自动套用原版元数据（乐评、波形、按时段选画面）
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
│   ├── START_SILENT.vbs 静默启动（无控制台窗口）
│   ├── STOP_SILENT.vbs  静默停止
│   └── media/           曲库媒体（自己放视频进来）
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

# 1. 静默启动服务（推荐）或手动起服务
#    双击 START_SILENT.vbs
py -3.12 server.py --port 8787         # 或者手动起：前台窗口，Ctrl+C 停止

# 2. 打补丁（先完全退出游戏，包括托盘）
py -3.12 patch_feapp.py --api-base http://127.0.0.1:8787 --apply

# 3. 启动游戏 → 角色旁边的写信入口
```

回滚：`py -3.12 patch_feapp.py --restore`（首次 apply 会自动备份原版 feapp.dat）。

### 服务的启动与停止（静默方式）

| 操作 | 方式 |
|---|---|
| **启动** | 双击 `mockserver/START_SILENT.vbs` —— 无控制台窗口，后台运行，日志写入 `mockserver/server.log`；已在运行则直接提示"nothing to do"，不会起第二个 |
| **停止** | 双击 `mockserver/STOP_SILENT.vbs` —— 只杀本服务的进程（按命令行里含 `server.py` + `8787` 精确匹配），不会误伤其他 Python 程序 |
| 脚本静默调用 | 两个 vbs 都支持 `cscript //nologo START_SILENT.vbs /q`（不弹 MsgBox，适合开机自启/计划任务） |
| 手动前台运行 | `py -3.12 server.py --port 8787`，Ctrl+C 停止 |
| 壁纸播放器停止 | 游戏内点 ✕，或 `py -3.12 tools/wallpaper_player.py --stop` |

> 启动后弹的提示框确认「Server started」即可关掉；如果提示 `/health` 未响应，打开 `server.log` 看原因（多半是端口被占或 Python 路径没找到，vbs 里可改 `PY_CANDIDATES`）。

### 壁纸播放器

需要 mpv：下载 [shinchiro 构建](https://github.com/shinchiro/mpv-winbuild-cmake/releases) 解压到 `tools/mpv/mpv.exe`。
导入曲目后，游戏曲库点歌即可上桌面壁纸；`py -3.12 tools/wallpaper_player.py` 也可独立控制。

## 添加自己的视频资源

曲库就是 `mockserver/media/` 这个目录——服务端**每次请求都实时重扫**，放进去的文件不用重启服务、不用重新打补丁，游戏里重新打开曲库就能看到。有两条路：

### 方式一：`import_song.py` 一条命令导入（推荐）

把 mp3 / wav / flac / m4a / mp4 / mkv / webm / mov 等整理成合规的 H.264+AAC mp4（需要 ffmpeg/ffprobe 在 PATH 里）：

```bash
cd mockserver

# 单个音频：自动配封面（或生成文字卡）封装成 mp4
py -3.12 import_song.py "D:/Music/夜曲.mp3" --name "夜曲" --singer "肖邦" --style local

# 单个视频：直接转封装
py -3.12 import_song.py "D:/Video/演唱会.mp4" --name "演唱会" --mood "热烈"

# 整个目录批量导入
py -3.12 import_song.py "D:/Downloads/一批歌" --style "本地导入"

# 先试跑不落盘，看看会导入什么
py -3.12 import_song.py "D:/Music/夜曲.mp3" --dry-run
```

常用参数：`--name` 曲名（默认取文件名）、`--title` 副标题、`--singer` 演奏者、`--style` 分类、`--mood` 氛围、`--description` 简介、`--opinion` 林离的乐评（都会显示在游戏曲目页）。

### 方式二：手动放官方命名的视频（能吃全套原版元数据）

如果你手里有和原版 CDN 同构的曲目视频，按下面的文件名规则放进 `media/<曲目名>/` 子目录：

```
mockserver/media/
└── PlaySing_Fen_Shua_Jiang_Ziyun_original/
    ├── PlaySing_Fen_Shua_Jiang_Ziyun_original_TOD1200_NI_L.mp4
    ├── PlaySing_Fen_Shua_Jiang_Ziyun_original_TOD1730_WI_L.mp4
    └── PlaySing_Fen_Shua_Jiang_Ziyun_original_TOD2000_NI_L.mp4
```

文件名规则：`<nameKey>_TOD<HHMM>_<视角>[_L].mp4`

| 片段 | 含义 |
|---|---|
| `<nameKey>` | 曲目标识。**与官方歌单（`data/dump/songlist.txt`）对上号的曲目会自动套用原版元数据**：中文名、演奏者、林离的乐评、3846 点波形数据全部白拿 |
| `TOD<HHMM>` | 时段画面：`TOD1200`→白天（TOD12）、`TOD1730`→黄昏（TOD1730）、`TOD2000`→夜晚（TOD20）。同一首可以只放一个时段，游戏按当前时间挑可用的 |
| `<视角>` | `NI` / `WI`（原版的两种演奏视角），写别的字母也行 |
| `_L` | 可选后缀，有和无都会被正确解析 |

一个 nameKey 就是一条曲目，三个时段 × 两个视角最多 6 个文件，缺的时段自动跳过。

### 完全自定义命名

不符合上面规则的 mp4 也能用——每个文件会成为一条独立曲目（曲名 = 文件名），适合快速试播。想规范一点就用方式一的 `import_song.py`，它会生成配套元数据 json。

> **版权提醒**：`media/` 已在 `.gitignore` 里，不会被提交到仓库。请只放自己有权使用的音视频。

## 免责声明

- 本项目仅供学习研究，需要自备正版游戏本体；**不包含、不分发任何游戏资源与媒体文件**（曲库、解包源码、补丁产物均已在 `.gitignore` 排除）
- 请勿将本项目用于商业用途
