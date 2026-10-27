# BSide: Olivia Lin · 本地写信服务

停服后的《BSide: Olivia Lin》只剩离线态：曲库、定制演奏、写信、分享全部灰掉。这个项目把**写信**单独救回来——不动游戏本体，只在 `feapp.dat` 里打一层补丁，配一个本地假服务接管 `/toy/letter/*`。

游戏里还是原来那个界面、原来的信封动画、原来的阅读节奏，只是对面回信的人换成了你自己配的模型。

```
C:\CodeAI\Olivia_Lin\
├── mockserver\              本地假服务（主要工作区）
│   ├── server.py            服务本体，标准库零依赖
│   ├── persona.md           人设：改这个就能换她的性格
│   ├── config.json          模型配置、回信节奏、记忆条数
│   ├── patch_feapp.py       给客户端打补丁 / 回滚（曲库页 + 播放驱动 + 待机音乐联动）
│   ├── patch_webplayer.py   去掉壁纸层斜向 UID 水印（webplayer.dat）
│   ├── START.bat            双击起服务
│   ├── data\letters.json    信箱数据
│   └── dist\feapp.dat       生成的补丁包
├── analysis\                逆向阶段产物（报告、桥接协议、解包脚本）
├── tools\                   自写工具（截屏、UI 点击、PE 解析、字符串提取）
├── reference\               参考项目 OliviaSoul 的人设与补丁脚本
└── feapp_unpacked\          解包后的前端源码（改代码时查这里）
```

## 三步用起来

```bash
# 1. 起服务（窗口别关，或者双击 START.bat）
cd C:\CodeAI\Olivia_Lin\mockserver
py -3.12 server.py --port 8787

# 2. 打补丁（先完全退出游戏，包括托盘）
py -3.12 patch_feapp.py --api-base http://127.0.0.1:8787 --apply
py -3.12 patch_webplayer.py --apply        # 壁纸层去 UID 水印

# 3. 生成曲库封面（从视频抽帧，新导入曲目后重跑即可）
py -3.12 ..\tools\gen_covers.py

# 4. 启动游戏 → 角色旁边的写信入口
```

回滚（第一次 apply 时已自动备份原版 `feapp.dat`）：

```bash
py -3.12 patch_feapp.py --restore
py -3.12 patch_webplayer.py --restore
```

### 播放链路的三个修复（2026-10）

| 问题 | 根因 | 修法 |
| --- | --- | --- |
| 播放时进度条不动 | `ToyPianistClient.addEventListener` 被 QCefView 用 defineProperty 锁死（writable:false），注入脚本的包装静默失败，timeupdate 进不了 store | 绕过事件桥直驱 pinia：每 0.5s 写 `playlist.songProgress`（裸 ref，可写）；播完调 `handlePlayNext()`，切不动就 `stopCurrentSong()` 收尾 |
| 曲库/播放条没有封面 | 封面 iconUrl 指向已停服的 CDN | `tools/gen_covers.py` 从视频抽帧生成 `media/<key>/<key>.jpg`，`/catalog` 自动挂上 |
| 放歌时待机背景音乐不停 | ambience 与 mpv 视频音轨叠加 | 壁纸开播时 `ambientSound.setMuted(true)`，停播/切歌时恢复原状态 |
| 壁纸层斜向 "10000" 水印 | 水印不在 feapp（其水印已被补丁杀掉），而在原生加载的 webplayer.dat：它的 WatermarkOverlay 从 URL `?uid=` 读值 | `patch_webplayer.py` 往其 index.html 注入 `.watermark-overlay{display:none!important}` |
| 点播放瞬间闪一下系统壁纸 | 原生收到 sendWebPlayerControlCmd(play) 会立即隐藏自己的壁纸窗口，而 mpv 要 0.3~0.5s 后才 attach（高频截屏实验证实） | 播放命令不再转发原生（`Ct` 只走本地播放器），房间场景一直显示到 mpv 就绪，无缝衔接 |

### 播单功能（2026-10）

曲库左侧风格栏「轻音乐」旁边的**我的播单**入口，点开是一个独立的播单管理面板：

- 多个播单，自定义名字（行内编辑），localStorage 持久化（键 `olv_playlists_v1`）
- 从曲库添加曲目（支持搜索过滤），曲目可上移/下移排序、移出
- 三种播放模式：**顺序**（播完停）/ **循环**（列表内循环）/ **随机**（随机切歌，不重复当前首）
- ▶ 播放全部，或从任意一首开始播；正在播放的曲目在面板里高亮
- 从曲库直接点歌会自动取消播单接管，互不干扰

## 为什么不能简单关掉离线模式

这是踩了最久的一个坑，记下来免得重走。

关掉 `offlineMode` 之后应用会要求**真实登录**（Steam 票据 → 原生 `showThirdPartyQuickLoginPanel`），而服务器已经停了，必然弹「登录态校验失败，请重试」。所以离线态和在线态是个死结：留着它功能锁死，关掉它登不进去。

**正确做法是保留离线态，只拆掉写信这条链路上的锁。** 一共 9 处，全在 `assets/main-*.js` 里：

| 改什么 | 为什么 |
|---|---|
| `$` 里强制 `toyApiUrl` 指向本地 | 让 axios 的 baseURL 打到 `127.0.0.1:8787` |
| `N3=!1` → `N3=!0` | 信箱功能总开关，原版写死关闭 |
| `e.isOfflineMode&&(mailWidget=false...)` → 恒 `true` | 离线态不再锁桌面入口 |
| `initSettings` 里强制 `mailWidget:!0` | 原生把这个值持久化成 false 了，得覆盖回来 |
| `if(t.isOfflineMode)throw new Ol(e)` → `if(!1)` | 请求拦截器不再短路 |
| `"hide-write":o(p)||!o(N3)` → `!1` | 写信按钮的隐藏条件 |
| 信箱列表 `p.value\|\|` 守卫去掉 | 离线时原本不拉列表 |
| 离线态也启动 `startPolling()` | 新信红点靠它 |
| 离线会话建立后调 `Z.toggleLetterEntry({new_status:!0})` | 通知原生画出那个入口图标 |

> 还有一条死路：`window.cefViewQuery` 是 QCefView 用 `defineProperty` 锁死的不可配置属性，赋值和 redefine 都会失败（`Cannot redefine property`）。所以「劫持桥接返回值」这条思路走不通，只能改业务代码。

## 人设：`persona.md`

**改完不用重启**，下一封回信立即生效。整份文件会作为 system prompt 送给模型。

结构是「人格层 + 技艺层」：

| 节 | 内容 |
|---|---|
| 一、硬规则 | 十条不能破的底线（不说自己是 AI、不列要点、不谄媚、不诊断…） |
| 二、身份 | 22 岁、上海、音乐学院主修钢琴辅修心理学、黑胶/老电影/雨天 |
| 三、人格 | 温柔而克制、先感受后道理、"后来才看懂"型 |
| 四、语言风格 | 句长、标点、五个修辞习惯、语气锚点 |
| 五、情感模式 | 对方难过/开心/愤怒时分别怎么接 |
| 六、书信技艺 | 结构、篇幅表、九种场景策略、红线 |
| 七、记忆 | 怎么自然地承接前几封信 |
| 八、官方语料 | 欢迎信原文 + 破冰对话（学文风，禁止照抄） |

身份和语料取自游戏本体（`feapp` 的 `zh-cn` 语言包），人格框架参考了 OliviaSoul 的蒸馏档案。

## 记忆：她会记得前几封

`llm.history_letters`（默认 6）把前几封信拼成多轮对话喂进去，所以第二封起能自然承接「你上次说报告要交」。

只带 **LLM 真正生成**的回信。模板回信句式机械，混进上下文会把人设带偏，所以每封信落盘时记一个 `replySource` 字段，模板信不进记忆。

## 不开游戏也能调人设

这是最常用的命令，改完 `persona.md` 立刻看效果：

```bash
# 直接看回信
py -3.12 server.py --reply "今天下了一天雨，我在窗边坐了很久。"

# 顺便打印实际发给模型的完整 messages（人设有没有生效、历史带了几条）
py -3.12 server.py --reply "今天下了一天雨。" --show-prompt

# 模拟第一封信（不带历史）
py -3.12 server.py --reply "第一次给你写信。" --no-history

# 来信太长，存成 txt
py -3.12 server.py --reply-file my_letter.txt
```

`--show-prompt` 里的「历史消息：N 条」如果一直是 0，说明前面几封都是模板回的，还没产生真实记忆。

## 回信怎么产生

`config.json`：

```json
{
  "daily_letter_limit": 3,
  "reply_delay_min_minutes": 30,
  "reply_delay_max_minutes": 120,
  "reply_resend_delay_minutes": 2,
  "persona_file": "persona.md",
  "llm": {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-...",
    "model": "deepseek-chat",
    "temperature": 0.9,
    "max_tokens": 900,
    "history_letters": 6
  }
}
```

- `api_key` 留空 → 用内置模板拼一封（断网可用，但只有几十种组合，很单调）
- 填上 → 走 OpenAI 兼容接口，按 `persona.md` 写。任何兼容 `/chat/completions` 的服务都行（DeepSeek、中转站、Ollama、LM Studio）
- `reply_delay_min_minutes` / `reply_delay_max_minutes` 是**回信延迟区间**。每封信寄出时在这个区间里随机抽一个时间点写进信件记录（`deliverAt`），到点才真正生成回信。默认 30–120 分钟，模拟"她不是随时在线"。想立刻验证效果，把两个值都改成 `0`
- `reply_resend_delay_minutes` 是重寄的短延迟（上一封失败了重发，不用再等两小时）
- `reply_delay_seconds` 保留做兼容：填一个 >0 的固定秒数就忽略上面的随机区间

状态机：`待处理(1) → 生成中(3) → 已回复(4)`，失败可重寄（`/letter/resend` 重新生成）。
等待期间信件一直是 `待处理(1)`，前端显示"她还没回"。延迟不靠线程 sleep，而是后台调度器每 15 秒扫一次「已到点」的信，所以**服务中途关掉再开，逾期未回的信会自动补上**。

**回信顺序**：她是一封一封读、一封一封回的，所以做了两层保证——

1. 写信时算送达时间：`deliverAt = max(现在 + 随机延迟, 上一封的送达时间 + 5~15 分钟)`。后写的信不可能排到前面去。
2. 调度器放行时，同一个 uid 只处理**最早那一封还没回的**。哪怕后面那封因为随机到更短的延迟提前到点，也会一直等着。

这样连写三封，回信时间会依次往后错开，可能超出 2 小时（比如第 1 封 114 分钟、第 2 封 126 分、第 3 封 138 分）——顺序优先于区间上限，这是有意的。
另外，单封生成失败不会卡死队列：会退回待处理并延后重试，连续失败 3 次就标记失败放行给下一封。

## 已实现的接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/toy/getUserInfo` | 启动门禁，`status=2` 跳过登录/问卷 |
| POST | `/toy/signIn` | 账号初始化 |
| POST | `/toy/letter/send` | 寄信 `{content, material}`，带每日配额 |
| GET | `/toy/letter/list` | 列表 + `remainingToday` + 分页 |
| GET | `/toy/letter/detail` | 详情，读后自动标已读 |
| GET | `/toy/letter/unread_count` | 未读数（驱动桌面红点） |
| POST | `/toy/letter/resend` | 重寄（重新生成回信） |
| POST | `/toy/letter/share` | 返回假 shareId |
| * | 其余 20+ 端点 | 统一回 200 + `{}`，不再弹网络错误 |

## 排障

**先看两个日志**：

```
C:\CodeAI\Olivia_Lin\mockserver\data\requests.log      前端请求到没到（有没有 /toy/letter/*）
%APPDATA%\miHoYo\Olivia-steam\logs\Olivia.log          原生日志，含每个桥接调用和 CEF console
```

原生日志里搜 `toggleLetterEntry`、`mailWidget` 能直接看到入口状态，搜 `bridge_query` 能看到前端每一次桥调用。这是排查这类问题最快的地方。

| 现象 | 原因 |
|---|---|
| 曲库页显示「当前为离线模式，曲库暂不可用」 | 正常。曲目素材是逐首下载的预渲染视频，服务器没了就没了 |
| 桌面没有写信图标 | 看 `Olivia.log` 里 `getSettings` 返回的 `mailWidget` 是不是 true；不是就重启一次（原生在启动时读设置） |
| 请求打到 `olivia.local` 而不是 `127.0.0.1` | 补丁没生效，检查 `--apply` 是否在游戏完全退出后执行 |
| 弹「登录态校验失败」 | 说明 `offlineMode` 被关掉了，别关，保留离线态 |
| CORS 预检 204 但 GET 不通 | 带 credentials 的请求不能用 `Allow-Headers: *`，必须回显 `Access-Control-Request-Headers` |

## 做不到的

- **语音/视频回信**：`replyType` 原版有 `SPEECH`/`MIX_*`，需要服务端合成，做不到。现在固定 `TEXT`。
- **分享链接**：生成的 `shareId` 打不开线上页面。
- **曲库演奏**：每首曲子的 `videoUrl` 是独立下载的预渲染视频，只在点过「下载」时才落盘，服务器停了就再也拿不到。
- **实时对话**：走 `NutLiveChat.dll` 的 WebSocket + protobuf，加上云渲染，不成体系地做不出来。
