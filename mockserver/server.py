#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BSide: Olivia Lin 本地假服务端

用途：接管客户端的 /toy/* 业务接口，让停服后的「信箱 / 写信」重新可用。
设计原则：
  * 只用标准库，双击即跑，不装依赖
  * 信件落盘为 JSON，重启不丢
  * 回信默认可走本地模板；配置 LLM 后走真模型（OpenAI 兼容协议）
  * 对未实现的接口一律返回 200 + {}，避免前端弹"网络错误"

启动：
  py -3.12 server.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LETTER_FILE = os.path.join(DATA_DIR, "letters.json")
REQ_LOG = os.path.join(DATA_DIR, "requests.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
PERSONA_FILE = os.path.join(BASE_DIR, "persona.md")
# 曲目/封面等本地媒体：把这个目录当"云端"，客户端用 /media/<文件名> 取
MEDIA_DIR = os.path.join(BASE_DIR, "media")
DUMP_DIR = os.path.join(BASE_DIR, "data", "dump")
# 从原生桥捞出来的原版歌单（129 首，含乐评/波形/三时段视频地址）。
# 有了它，本地导入的曲子可以直接套用原版元数据，不用自己编歌名和简介。
SONGLIST_DUMP = os.path.join(DUMP_DIR, "songlist.txt")
# 启动时按 --port 填，供 /catalog 生成绝对媒体地址
PUBLIC_BASE = "http://127.0.0.1:8787"

# ---- 桌面壁纸播放（合并进本服务，不再需要单独进程）----
_WALLPAPER = None
_WP_LOCK = threading.Lock()


def _wallpaper():
    """懒加载壁纸播放器；mpv 缺失时返回 None，不影响写信功能。"""
    global _WALLPAPER
    if _WALLPAPER is not None:
        return _WALLPAPER
    with _WP_LOCK:
        if _WALLPAPER is None:
            try:
                if BASE_DIR not in sys.path:
                    sys.path.insert(0, BASE_DIR)
                import wallpaper as wp
                _WALLPAPER = wp.WallpaperPlayer()
                print("[wallpaper] 播放模块已就绪", flush=True)
            except Exception as e:
                print(f"[wallpaper] 不可用: {e}", flush=True)
                return None
    return _WALLPAPER


# 远程操作通道的指令队列（内存级，进程重启即清空）。
# 用途：窗口被别的程序盖住时，物理点击落不到页面上，改由页面自己派发 DOM 事件。
_CMD_QUEUE: list = []
_CMD_LOCK = threading.Lock()


def _probe_duration(path: str) -> float:
    """用 ffprobe 读时长；没有 ffprobe 就返回 0（前端只会显示 0:00，不影响播放）。"""
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=8,
        )
        return float((out.stdout or "0").strip() or 0)
    except Exception:
        return 0


# ffprobe 结果按 (路径, mtime) 缓存，避免每次拉歌单都重新探测
_duration_cache: dict = {}


def _media_size_duration(path: str) -> tuple:
    """返回 (字节数, 整秒时长)。

    duration 一定要给对：原生的 VideoTodViewItem.duration 转不成整数时会抛
    boost.json 的 out of range，播放器把时长当 0，于是刚开播就 ended。
    """
    try:
        size = os.path.getsize(path)
        mt = os.path.getmtime(path)
    except OSError:
        return 0, 0
    hit = _duration_cache.get(path)
    if hit and hit[0] == mt:
        return size, hit[1]
    dur = int(round(_probe_duration(path)))
    _duration_cache[path] = (mt, dur)
    return size, dur


# ---------- 原版歌单元数据 ----------
_official_cache: dict = {"mtime": 0.0, "by_key": {}}


def _load_official() -> dict:
    """读 data/dump/songlist.txt，返回 nameKey -> 曲目字典。

    文件是按 mtime 缓存的，替换 dump 后不用重启服务。
    """
    try:
        mt = os.path.getmtime(SONGLIST_DUMP)
    except OSError:
        return {}
    if _official_cache["mtime"] == mt:
        return _official_cache["by_key"]
    by_key: dict = {}
    try:
        with open(SONGLIST_DUMP, encoding="utf-8") as f:
            outer = json.load(f)
        inner = outer.get("data") if isinstance(outer, dict) else None
        payload = json.loads(inner) if isinstance(inner, str) else (inner or outer)
        for style in (payload.get("styles") or {}).values():
            for song in style.get("songs") or []:
                key = song.get("nameKey")
                if key:
                    by_key[key] = song
    except Exception as e:
        print(f"[catalog] 原版歌单读取失败：{e}", flush=True)
        return {}
    _official_cache["mtime"] = mt
    _official_cache["by_key"] = by_key
    print(f"[catalog] 已载入原版歌单 {len(by_key)} 首", flush=True)
    return by_key


# 文件名实名规则：<nameKey>_TOD<HHMM>_<view>[_L].mp4
_MEDIA_NAME_RE = re.compile(r"^(?P<key>.+?)_TOD(?P<tod>\d{4})_(?P<view>[A-Za-z]+?)(?:_L)?$")


def _tod_code(hhmm: str) -> str:
    """1200 -> TOD12，1730 -> TOD1730，2000 -> TOD20（与原版 tod 字段一致）。"""
    hh, mm = hhmm[:2], hhmm[2:]
    return "TOD" + hh + ("" if mm == "00" else mm)


def _scan_media() -> dict:
    """扫描 media/（含子目录），按 nameKey 归组各时段的视频。

    返回 {nameKey: {"tod": {todCode: 相对路径}, "other": [相对路径]}}
    """
    groups: dict = {}
    if not os.path.isdir(MEDIA_DIR):
        return groups
    for root, _dirs, files in os.walk(MEDIA_DIR):
        for fn in sorted(files):
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in (".mp4", ".webm", ".m4v"):
                continue
            rel = os.path.relpath(os.path.join(root, fn), MEDIA_DIR).replace("\\", "/")
            m = _MEDIA_NAME_RE.match(stem)
            if m:
                key = m.group("key")
                slot = groups.setdefault(key, {"tod": {}, "other": []})
                slot["tod"][_tod_code(m.group("tod"))] = rel
            else:
                slot = groups.setdefault(stem, {"tod": {}, "other": []})
                slot["other"].append(rel)
    return groups

# ---------------------------------------------------------------- 枚举（对齐客户端）
ST_PENDING, ST_AUDITING, ST_LLM, ST_REPLIED, ST_FAILED = 1, 2, 3, 4, 5
AUDIT_PENDING, AUDIT_PASSED, AUDIT_REJECTED = 1, 2, 3
RT_NONE, RT_TEXT, RT_SPEECH, RT_MIX_PLAY, RT_MIX_SVS = 0, 1, 2, 3, 4

# 客户端约定：0 = 成功，非 0 = 业务错误（error.message 会直接弹给用户）
ERR_BIZ = 100001

DEFAULT_CONFIG = {
    "port": 8787,
    "daily_letter_limit": 3,
    # 回信延迟：每封信寄出时在这个区间里随机抽一个送达时间（单位：分钟）。
    # 抽中的值会写进信件记录（deliverAt），服务重启也不会丢。
    # 想立刻验证效果，把 min/max 都改成 0 即可。
    "reply_delay_min_minutes": 30,
    "reply_delay_max_minutes": 120,
    # 重新投递（上一封失败了要重发）用这个更短的延迟
    "reply_resend_delay_minutes": 2,
    # 兼容旧配置：显式填了固定秒数就忽略上面的随机区间
    "reply_delay_seconds": 0,
    "display_name": "林离",
    # 人设文件（相对本目录）。改 persona.md 不用重启，下一封信就生效
    "persona_file": "persona.md",
    # 留空则使用内置模板回信；填上即走 OpenAI 兼容接口（DeepSeek 也可）
    "llm": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "timeout": 45,
        "temperature": 0.9,
        "max_tokens": 900,
        # 回信时带上前几封信做上下文，让她记得你说过什么。0 = 不带记忆
        "history_letters": 6,
    },
}

_lock = threading.Lock()

# 预检里浏览器要什么头就回显什么，兜底用这一串
ALLOW_HEADERS = (
    "content-type,authorization,x-uid,x-token,x-platform,"
    "x-rpc-device_fp,x-rpc-device_id,x-rpc-device_name,x-rpc-client_type,"
    "x-rpc-lang,x-rpc-app_version,x-rpc-channel,x-rpc-device_model"
)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception as e:  # 配置坏了不能拖垮服务
            print(f"[cfg] 读取 config.json 失败，用默认值：{e}")
    else:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    if os.environ.get("BSIDE_LLM_KEY"):
        cfg["llm"]["api_key"] = os.environ["BSIDE_LLM_KEY"]
    return cfg


CONFIG = load_config()


# ---------------------------------------------------------------- 存储
def _ensure_data() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(LETTER_FILE):
        _write_store({"seq": 0, "letters": []})


def _read_store() -> dict:
    _ensure_data()
    try:
        with open(LETTER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seq": 0, "letters": []}


def _write_store(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = LETTER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LETTER_FILE)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _sent_today(store: dict, uid: str) -> int:
    day = _today()
    return sum(
        1 for x in store["letters"]
        if x.get("uid") == uid and time.strftime("%Y-%m-%d", time.localtime(x["createdAt"])) == day
    )


def _summary(content: str) -> str:
    text = re.sub(r"\s+", " ", (content or "").strip())
    return text[:20] + ("..." if len(text) > 20 else "")


def _pick_reply_delay() -> int:
    """抽一个回信延迟（秒）。

    区间来自 config.json 的 reply_delay_min_minutes / max_minutes，默认 30–120 分钟。
    留 reply_delay_seconds>0 时走固定值，方便调人设时快速迭代。
    """
    fixed = int(CONFIG.get("reply_delay_seconds") or 0)
    if fixed > 0:
        return fixed
    lo = max(0.0, float(CONFIG.get("reply_delay_min_minutes", 30)))
    hi = max(lo, float(CONFIG.get("reply_delay_max_minutes", 120)))
    return int(round(random.uniform(lo, hi) * 60))


# ---------------------------------------------------------------- 回信引擎
# persona.md 缺失或读不出来时的兜底（内容比 persona.md 简略，但不会跑偏）
DEFAULT_PERSONA = (
    "你是《BSide: Olivia Lin》里的林离（Olivia Lin），音乐学院本科生，辅修心理学，"
    "弹钢琴，偏爱肖邦，也听流行。说话温柔、克制、有文气，喜欢用音乐和日常细节作比喻，"
    "不卖萌、不用感叹号堆情绪、不列要点、不写小标题。"
)

# 每次调用都附在 persona 后面，约束输出的形状
WRITING_RULE = """
---

现在请以林离的身份，给你面前的这个人写一封回信。

只输出信件正文，不要加引号，不要写「回信：」之类的任何前缀或后缀，不要任何解释。

篇幅严格跟着来信走：
- 对方只写一两句，你就回三五句（100 字上下）。不要因为对方写得少就展开长篇——短也是一种尊重。
- 常规倾诉，2–4 段。
- 情绪很重、或是第一封信，可以长一些。
"""


def load_persona() -> str:
    """每封信都重新读一次，改完 persona.md 立即生效。"""
    path = os.path.join(BASE_DIR, CONFIG.get("persona_file") or "persona.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text
        print(f"[persona] {os.path.basename(path)} 是空的，回退内置人设")
    except FileNotFoundError:
        print(f"[persona] 找不到 {path}，回退内置人设")
    except Exception as e:
        print(f"[persona] 读取失败（{e}），回退内置人设")
    return DEFAULT_PERSONA


def _history_messages(uid: str, exclude_id: int, limit: int) -> list[dict]:
    """把该用户最近几封已回复的信，转成多轮对话历史，让她记得你说过什么。

    只取 LLM 真正生成的回信：模板回信句式机械，混进上下文会把人设带偏。
    """
    if limit <= 0:
        return []
    with _lock:
        store = _read_store()
    items = [
        x for x in store["letters"]
        if x.get("uid") == uid
        and x["letterId"] != exclude_id
        and x["letterStatus"] == ST_REPLIED
        and x.get("replySource") == "llm"
        and (x.get("replyText") or "").strip()
    ]
    if not items:
        return []
    items.sort(key=lambda x: x["createdAt"])
    msgs: list[dict] = []
    for x in items[-limit:]:
        msgs.append({"role": "user", "content": x["content"]})
        msgs.append({"role": "assistant", "content": x["replyText"]})
    return msgs


def _build_messages(content: str, uid: str, letter_id: int) -> list[dict]:
    """拼出完整的 messages：人设 + 历史信件 + 这一封。"""
    limit = int((CONFIG.get("llm") or {}).get("history_letters", 0) or 0)
    hist = _history_messages(uid, letter_id, limit)
    msgs = [{"role": "system", "content": load_persona() + "\n" + WRITING_RULE}]
    msgs.extend(hist)
    if hist:
        msgs.append({"role": "user", "content": f"（新的来信）\n{content}"})
    else:
        msgs.append({"role": "user", "content": content})
    return msgs

OPENINGS = [
    "收到你的信了，我把它放在琴盖上，读完才舍得折起来。",
    "你的信我读了两遍。第二遍是慢慢读的，像把一段旋律放慢到能听清每个音。",
    "今天练完琴才拆你的信，指尖还热着，字迹看得很慢。",
    "信到的时候窗户正开着，风把纸角掀起来又放下，像在催我快点读。",
]

MIDDLES = [
    "你说{quote}。这句话让我停了一会儿。有些心情就是这样，说出来的时候轻，落下去的时候重。",
    "关于{quote}，我想起练琴时的一个习惯：难的段落不能硬冲，要拆成很小的句子，一遍一遍，直到手指先于脑子记住它。",
    "你提到的{quote}，我大概能懂。人在被生活推着走的时候，最难的是给自己留一小块不被安排的时间。",
    "读到{quote}，我想到肖邦的夜曲。它们不解决什么，只是陪你把一个晚上过完，这也够了。",
]

CLOSINGS = [
    "慢一点也没关系，我在这里。",
    "下次写信，可以只写一句，也可以什么都不写，寄一张空白的纸我也收。",
    "琴还在，灯也留着。等你的下一封。",
    "愿你今天有一段不被打扰的时间，哪怕只有十分钟。",
]


def _quote(content: str) -> str:
    """从来信里挑一个能自然嵌进句子的短语，避免截断在半个句子上。"""
    text = re.sub(r"\s+", "", content or "")
    clauses = [c for c in re.split(r"[。！？!?；;\n]+", text) if len(c) >= 6]
    candidates = []
    for c in clauses:
        for part in re.split(r"[，,、]", c):
            part = part.strip()
            if 6 <= len(part) <= 16:
                candidates.append(part)
    if candidates:
        return max(candidates, key=len)
    body = clauses[0] if clauses else text
    out = (body[:12] + "…") if len(body) > 12 else (body or "最近的日子")
    return out if len(out) >= 4 else "最近的日子"


def fallback_reply(content: str, seed: int) -> str:
    rnd = random.Random(seed)
    quote = _quote(content)
    parts = [rnd.choice(OPENINGS), rnd.choice(MIDDLES).format(quote=quote), rnd.choice(CLOSINGS)]
    return "\n\n".join(parts)


def llm_reply(content: str, uid: str = "10000", letter_id: int = -1) -> str | None:
    llm = CONFIG.get("llm") or {}
    key = (llm.get("api_key") or "").strip()
    if not key:
        return None
    base = llm["base_url"].rstrip("/")
    payload = {
        "model": llm["model"],
        "messages": _build_messages(content, uid, letter_id),
        "temperature": float(llm.get("temperature", 0.9)),
        "max_tokens": int(llm.get("max_tokens", 900)),
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=llm.get("timeout", 45)) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = (data["choices"][0]["message"]["content"] or "").strip()
        return _clean_reply(text) or None
    except Exception as e:
        print(f"[llm] 调用失败，回落模板：{e}")
        return None


def _clean_reply(text: str) -> str:
    """模型偶尔会自作多情加引号或前缀，剥掉。"""
    t = text.strip()
    t = re.sub(r"^(回信|信件正文|林离的回信)\s*[:：]\s*", "", t)
    if len(t) > 2 and t[0] in "\"“「" and t[-1] in "\"”」":
        t = t[1:-1].strip()
    # 若结尾自己加了署名，去掉
    t = re.sub(r"\n+\s*(——?\s*)?林离\s*$", "", t)
    t = re.sub(r"\n+\s*(——?\s*)?Olivia(\s*Lin)?\s*$", "", t, flags=re.I)
    return t.strip()



def _compose_reply(letter_id: int, content: str, uid: str = "10000") -> tuple[str, str]:
    """返回 (正文, 来源)。来源会写进信件记录，模板回信不进后续上下文。"""
    text = llm_reply(content, uid, letter_id)
    if text:
        return text, "llm"
    return fallback_reply(content, letter_id), "template"


def _process_letter(letter_id: int) -> None:
    """真正生成回信：把 PENDING 推进到 REPLIED。

    只由调度线程在**到达 deliverAt 之后**调用——延迟不在这里 sleep，
    因为一 sleep 就占着一个线程，而且服务一重启线程就没了、这封信永远不回。
    """
    with _lock:
        store = _read_store()
        letter = next((x for x in store["letters"] if x["letterId"] == letter_id), None)
        if not letter or letter.get("letterStatus") == ST_REPLIED:
            return
        letter["letterStatus"] = ST_LLM
        _write_store(store)
        content = letter["content"]
        uid = letter.get("uid", "10000")

    text, source = _compose_reply(letter_id, content, uid)

    with _lock:
        store = _read_store()
        letter = next((x for x in store["letters"] if x["letterId"] == letter_id), None)
        if not letter:
            return
        letter["replyText"] = text
        letter["replyType"] = RT_TEXT
        letter["letterStatus"] = ST_REPLIED
        letter["auditStatus"] = AUDIT_PASSED
        letter["repliedAt"] = int(time.time())
        letter["isRead"] = 0
        letter["replySource"] = source
        _write_store(store)
    print(f"[letter] #{letter_id} 已回信（{len(text)} 字，{source}）", flush=True)


# 正在生成的信件，防止调度器重复拾取同一封
_inflight: set = set()
_inflight_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


def _scheduler_tick() -> None:
    """把所有「已到点」的信件放进生成队列。

    这样设计的好处：
      · 服务中途重启，重启后第一次 tick 就会把逾期未回的信补上；
      · 延迟长到 2 小时也不需要挂着线程睡觉；
      · 信件的送达时间存在数据文件里，不依赖内存。

    顺序保证：同一个 uid 只放行**最早那一封还没回的**信。后写的信哪怕随机到
    更短的延迟、更早到点，也必须排在前面那封之后——她是一封一封读、一封一封回的。
    """
    now = int(time.time())
    with _lock:
        store = _read_store()
        pending = [
            x for x in store["letters"]
            if x.get("letterStatus") in (ST_PENDING, ST_AUDITING, ST_LLM)
        ]
        pending.sort(key=lambda x: int(x["letterId"]))
        oldest: dict = {}
        for x in pending:
            oldest.setdefault(x.get("uid"), x)   # 每个 uid 只留最早的一封
        due = [
            x["letterId"] for x in oldest.values()
            if int(x.get("deliverAt") or 0) <= now
        ]
    for lid in due:
        with _inflight_lock:
            if lid in _inflight:
                continue
            _inflight.add(lid)
        threading.Thread(target=_run_one, args=(lid,), daemon=True).start()


def _run_one(letter_id: int) -> None:
    try:
        _process_letter(letter_id)
    except Exception as e:  # 单封失败不能拖垮调度器
        print(f"[letter] #{letter_id} 回信失败：{e}", flush=True)
        # 关键：失败的信如果不处理，会一直占着「最早未回」的位置，
        # 后面的信永远排不上队。这里退回待处理并延后重试，连续失败就标记失败放行。
        try:
            with _lock:
                store = _read_store()
                letter = next((x for x in store["letters"] if x["letterId"] == letter_id), None)
                if letter:
                    tries = int(letter.get("replyTries") or 0) + 1
                    letter["replyTries"] = tries
                    if tries >= 3:
                        letter["letterStatus"] = ST_FAILED
                    else:
                        letter["letterStatus"] = ST_PENDING
                        letter["deliverAt"] = int(time.time()) + 60 * tries
                    _write_store(store)
        except Exception:
            pass
    finally:
        with _inflight_lock:
            _inflight.discard(letter_id)


def start_scheduler() -> None:
    """启动（幂等）后台调度线程：每 15 秒扫一次到点的信件。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def loop() -> None:
        while True:
            try:
                _scheduler_tick()
            except Exception as e:
                print(f"[sched] tick 失败：{e}", flush=True)
            time.sleep(15)

    threading.Thread(target=loop, daemon=True, name="letter-scheduler").start()
    pending = 0
    with _lock:
        store = _read_store()
        pending = sum(1 for x in store["letters"]
                      if x.get("letterStatus") in (ST_PENDING, ST_AUDITING, ST_LLM))
    print(f"[sched] 回信调度已启动（待回 {pending} 封，"
          f"延迟 {CONFIG.get('reply_delay_min_minutes', 30)}–"
          f"{CONFIG.get('reply_delay_max_minutes', 120)} 分钟随机）", flush=True)


# ---------------------------------------------------------------- HTTP
def _list_item(x: dict) -> dict:
    return {
        "letterId": x["letterId"],
        "isRead": x.get("isRead", 1),
        "letterStatus": x["letterStatus"],
        "auditStatus": x.get("auditStatus", AUDIT_PASSED),
        "replyType": x.get("replyType", RT_NONE),
        "summary": x["summary"],
        "createdAt": x["createdAt"],
        "repliedAt": x.get("repliedAt") or 0,
    }


def _detail(x: dict) -> dict:
    return {
        "letterId": x["letterId"],
        "content": x["content"],
        "material": x.get("material") or {"stampId": 0, "paperId": 0},
        "letterStatus": x["letterStatus"],
        "auditStatus": x.get("auditStatus", AUDIT_PENDING),
        "replyType": x.get("replyType", RT_NONE),
        "replyText": x.get("replyText", ""),
        "replyVideoUrl": x.get("replyVideoUrl", ""),
        "isRead": x.get("isRead", 1),
        "createdAt": x["createdAt"],
        "repliedAt": x.get("repliedAt") or 0,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "BSideLocal/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):
        if os.environ.get("BSIDE_QUIET") == "1":
            return
        line = "%s  %s" % (time.strftime("%H:%M:%S"), fmt % args)
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            pass
        # 同时落一份，后台运行时 stdout 会被缓冲，排障靠这个
        try:
            with open(REQ_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _cors(self):
        origin = self.headers.get("Origin") or "https://olivia.local"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        # 带 credentials 的请求不允许用 "*" 通配，必须把浏览器请求的头原样回显，
        # 否则预检（OPTIONS）会 204 通过、真正的 GET 却被浏览器拦掉。
        req_headers = self.headers.get("Access-Control-Request-Headers")
        self.send_header("Access-Control-Allow-Headers", req_headers or ALLOW_HEADERS)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin, Access-Control-Request-Headers")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data=None):
        """业务成功。客户端 axios 拦截器只认 body.code===0，然后把 body.data 交给上层。"""
        return self._json({"code": 0, "message": "ok", "data": data if data is not None else {}})

    def _err(self, message: str, code: int = ERR_BIZ):
        """业务失败。HTTP 仍给 200，让拦截器走 code 分支，前端弹的是 message 而不是"网络错误"。"""
        return self._json({"code": code, "message": message, "data": None})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _uid(self) -> str:
        return self.headers.get("x-uid") or "10000"

    # ---------- 调试 / 媒体 ----------
    def _save_dump(self, name: str, raw: bytes) -> None:
        os.makedirs(DUMP_DIR, exist_ok=True)
        p = os.path.join(DUMP_DIR, f"{name}.txt")
        with open(p, "wb") as f:
            f.write(raw)
        print(f"[dump] {name} <- {len(raw)} B  {p}", flush=True)
        return self._json({"ok": True, "bytes": len(raw)})

    # ---------- 本地曲库 ----------
    def _catalog(self) -> None:
        """把 media/ 目录扫成客户端认识的歌单结构。

        客户端原本从原生桥 getOfflineSongList 拿 songlist.dat（AES）解密后的 JSON，
        这里给出同构的 {data: "<json str>"}。

        两种来源：
          · 文件名形如 Solo_xxx_TOD1730_NI_L.mp4 且 nameKey 在原版歌单里 →
            整条套用原版元数据（曲名/演奏者/乐评/波形），只把地址换成本地文件；
          · 对不上的 → 当本地导入曲目，尽量从同名 .json 里取信息。
        播放走的是 <video src>，没有 DRM。
        """
        base = PUBLIC_BASE
        official = _load_official()
        groups = _scan_media()
        songs = []
        style_seen: dict = {}

        for key in sorted(groups):
            slot = groups[key]
            tod_map = slot["tod"]
            # 没解析出时段的（老式平铺文件）当作三个时段都用同一个
            flat = slot["other"]
            official_song = official.get(key)

            # 找同名封面：优先 media/<key> 目录里的，其次平铺的
            cover = ""
            for root, _dirs, files in os.walk(MEDIA_DIR):
                for fn in files:
                    stem, ext = os.path.splitext(fn)
                    if stem != key or ext.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), MEDIA_DIR).replace("\\", "/")
                    if not cover or (os.path.dirname(rel) == key):
                        cover = f"{base}/media/{rel}"
            if not flat:
                for cand in (".jpg", ".jpeg", ".png", ".webp"):
                    p = os.path.join(MEDIA_DIR, key, key + cand)
                    if os.path.isfile(p):
                        cover = f"{base}/media/{key}/{key}{cand}"
                        break

            # 组装 videoByTodView：本地有哪个时段就给哪个。
            # duration / size 必须带上——原生按它们判断媒体是否可用，
            # 缺了 duration 会 out of range，播放器把时长当 0，开播即 ended。
            tod_view = []
            for tod in ("TOD12", "TOD1730", "TOD20"):
                rel = tod_map.get(tod)
                if rel:
                    size, dur = _media_size_duration(os.path.join(MEDIA_DIR, rel.replace("/", os.sep)))
                    tod_view.append({
                        "url": f"{base}/media/{rel}",
                        "tod": tod,
                        "view": "NI",
                        "coverUrl": cover,
                        "duration": dur,
                        "size": size,
                    })
            if not tod_view and flat:
                _size, _dur = _media_size_duration(os.path.join(MEDIA_DIR, flat[0].replace("/", os.sep)))
                tod_view = [
                    {"url": f"{base}/media/{flat[0]}", "tod": t, "view": "NI",
                     "coverUrl": cover, "duration": _dur, "size": _size}
                    for t in ("TOD12", "TOD1730", "TOD20")
                ]
            if not tod_view:
                continue

            main_url = tod_view[0]["url"]
            main_file = os.path.join(
                MEDIA_DIR, tod_view[0]["url"][len(base) + len("/media/"):].replace("/", os.sep)
            ) if tod_view[0]["url"].startswith(base) else ""
            dur = tod_view[0].get("duration") or (
                int(_probe_duration(main_file)) if main_file and os.path.isfile(main_file) else 0
            )

            if official_song:
                song = dict(official_song)          # 整条沿用原版：乐评、波形、时长都在
                song["videoByTodView"] = tod_view
                song["videoUrl"] = main_url
                song["url"] = main_url
                song["audioUrl"] = ""
                if cover:
                    song["coverUrl"] = cover
                    song["iconUrl"] = cover
                if dur:
                    song["videoDuration"] = dur
                song["source"] = "official"
            else:
                meta = {}
                for rel in [tod_map.get(t) for t in tod_map] + flat:
                    if not rel:
                        continue
                    side = os.path.splitext(os.path.join(MEDIA_DIR, rel.replace("/", os.sep)))[0] + ".json"
                    if os.path.isfile(side):
                        try:
                            with open(side, encoding="utf-8") as f:
                                meta = json.load(f)
                            break
                        except Exception:
                            pass
                name = meta.get("name") or key
                style = meta.get("styleType") or "local"
                song = {
                    "id": key,
                    "name": name,
                    "nameKey": key,
                    "title": meta.get("title") or name,
                    "singer": meta.get("singer") or "",
                    "styleType": style,
                    "styleTypeDisplayName": meta.get("styleTypeDisplayName") or "本地导入",
                    "performanceType": "Solo",
                    "performanceTypeDisplayName": "独奏表演",
                    "performanceTypeDisplayShortName": "独奏",
                    "videoUrl": main_url,
                    "url": main_url,
                    "audioUrl": "",
                    "coverUrl": cover,
                    "iconUrl": cover,
                    "videoDuration": dur or int(meta.get("duration") or 0),
                    "audioDuration": dur or int(meta.get("duration") or 0),
                    "duration": dur or int(meta.get("duration") or 0),
                    "videoByTodView": tod_view,
                    "lyricSample": [],
                    "waveformData": meta.get("waveformData") or "",
                    "mood": meta.get("mood") or "",
                    "description": meta.get("description") or "",
                    "opinion": meta.get("opinion") or "",
                    "source": "local",
                }
            songs.append(song)
            st = song.get("styleType") or "local"
            if st not in style_seen:
                style_seen[st] = {
                    "type": st,
                    "displayName": song.get("styleTypeDisplayName") or st,
                    "count": 0,
                }
            style_seen[st]["count"] += 1

        modes = {}
        for s in songs:
            pt = s.get("performanceType") or "Solo"
            modes.setdefault(pt, {
                "type": pt,
                "displayName": s.get("performanceTypeDisplayName") or pt,
                "displayShortName": s.get("performanceTypeDisplayShortName") or pt,
            })
        payload = {
            "songs": songs,
            "musicStyles": list(style_seen.values()),
            "performanceModes": list(modes.values()),
        }
        print(f"[catalog] {len(songs)} 首（原版 {sum(1 for s in songs if s.get('source')=='official')}）", flush=True)
        return self._json({"data": json.dumps(payload, ensure_ascii=False)})

    def _serve_media(self, rel: str) -> None:
        """按 Range 返回本地媒体。CEF 的 <video> 拖动进度需要 206。"""
        rel = rel.split("?")[0]
        full = os.path.abspath(os.path.join(MEDIA_DIR, rel))
        if not full.startswith(os.path.abspath(MEDIA_DIR)) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        size = os.path.getsize(full)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        rng = self.headers.get("Range") or ""
        start, end = 0, size - 1
        partial = False
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m and (m.group(1) or m.group(2)):
            partial = True
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:  # bytes=-N 末尾 N 字节
                start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self._cors()
            self.end_headers()
            return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._cors()
        self.end_headers()
        with open(full, "rb") as f:
            f.seek(start)
            remain = length
            while remain > 0:
                chunk = f.read(min(256 * 1024, remain))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remain -= len(chunk)
        print(f"[media] {rel} {start}-{end}/{size}", flush=True)

    # ---------- 路由 ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        if path == "/health":
            return self._json({"ok": True, "letters": len(_read_store()["letters"])})

        # 前端调试上报（注入脚本用它把页面状态送回来，排障用）
        if path == "/debug":
            raw = q.get("d", "")
            try:
                obj = json.loads(raw)
                line = json.dumps(obj, ensure_ascii=False)
            except Exception:
                line = str(raw)
            print(f"[fe] {line[:900]}", flush=True)
            return self._json({"ok": True})

        # 本地媒体：曲目/封面都从这里取（支持 Range，视频才能拖动进度）
        if path.startswith("/media/"):
            return self._serve_media(path[len("/media/"):])

        # 本地曲库：把 media/ 目录扫成客户端能吃的歌单（替代已停服的 getOfflineSongList）
        if path == "/catalog":
            return self._catalog()

        # 桌面壁纸播放：点歌 → 视频铺到桌面背景（实现见 wallpaper.py）
        if path.startswith("/wallpaper/"):
            wp = _wallpaper()
            if wp is None:
                return self._json({"ok": False, "error": "壁纸播放不可用（缺 mpv 或未支持）"}, 503)
            act = path[len("/wallpaper/"):]
            if act == "status":
                return self._json({"ok": True, **wp.status()})
            if act == "list":
                import wallpaper as _wpmod
                cat = _wpmod.catalog()
                return self._json({"ok": True, "items": sorted(cat.keys()), "count": len(cat)})
            if act == "stop":
                wp.stop()
                print("[wallpaper] 停止，桌面恢复原生壁纸", flush=True)
                return self._json({"ok": True})
            if act == "play":
                label = q.get("file") or q.get("name") or ""
                try:
                    start = 0
                    try:
                        start = float(q.get("t") or 0)
                    except (TypeError, ValueError):
                        start = 0
                    if q.get("file"):
                        target = q["file"]
                        if not os.path.isfile(target):
                            return self._json({"ok": False, "error": "文件不存在"}, 404)
                        hwnd = wp.play(target, start=start)
                    elif q.get("name"):
                        hwnd = wp.play_by_name(q["name"], start=start)
                    else:
                        return self._json({"ok": False, "error": "缺少 name 或 file"}, 400)
                    print(f"[wallpaper] 播放 {label} hwnd={hwnd:#x}", flush=True)
                    return self._json({"ok": True, "hwnd": hwnd})
                except Exception as e:
                    print(f"[wallpaper] 播放失败 {label}: {e}", flush=True)
                    return self._json({"ok": False, "error": str(e)}, 500)
            return self._json({"ok": False, "error": "unknown wallpaper action"}, 404)

        # 远程操作通道：页面每 0.9s 拉一次，拿到指令后自己派发 DOM 事件。
        # 解决"窗口被遮挡 → 抢不到前台 → 物理点击打空"的排障困境。
        if path == "/probe/poll":
            cmd = None
            with _CMD_LOCK:
                if _CMD_QUEUE:
                    cmd = _CMD_QUEUE.pop(0)
            return self._json(cmd or {})

        route = path[len("/toy"):] if path.startswith("/toy") else path
        try:
            if route == "/letter/list":
                return self._letter_list(q)
            if route == "/letter/detail":
                return self._letter_detail(q)
            if route == "/letter/unread_count":
                return self._unread()
            if route == "/getUserInfo":
                return self._ok(self._user_info())
            if route == "/getMusicTypeInfo":
                return self._ok({
                    "musicStyles": [
                        {"type": "featured", "name": "精选"},
                        {"type": "popular", "name": "抒情"},
                        {"type": "rock", "name": "摇滚"},
                        {"type": "classical", "name": "古典"},
                        {"type": "jazz", "name": "爵士"},
                    ],
                    "performanceModes": [
                        {"type": "Solo", "name": "独奏"},
                        {"type": "PlaySing", "name": "弹唱"},
                        {"type": "Instrumental", "name": "伴奏"},
                    ],
                })
            if route == "/getPreferenceSurvey":
                return self._ok({"questions": []})
            if route == "/getSongStats":
                return self._ok({"playCount": 0, "performCount": 0})
            if route in ("/searchSongs", "/searchPlaylist", "/searchPerformances", "/searchUserSongs"):
                return self._ok({"list": [], "hasMore": False, "total": 0, "nextCursor": 0})
            if route == "/midi/listJobs":
                return self._ok({"list": [], "hasMore": False, "nextCursor": 0, "total": 0})
            if route == "/midi/batchGetResult":
                return self._ok({"list": []})
            # 未实现接口：给空 data，别让前端弹「网络错误」
            return self._ok({})
        except Exception as e:
            print(f"[err] GET {path}: {e}")
            return self._err(str(e))

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        route = path[len("/toy"):] if path.startswith("/toy") else path
        n = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(n) if n else b""
        try:
            # 调试落盘：注入脚本把桥接返回的原始数据 POST 过来，存成文件
            if route.startswith("/dump/"):
                name = (route[len("/dump/"):] or "dump").replace("/", "_")
                return self._save_dump(name, raw_body)
            # 下一条远程指令入队（curl -X POST /probe/cmd -d '{"cmd":"inv"}'）
            if route == "/probe/cmd":
                try:
                    item = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                except Exception:
                    item = {}
                with _CMD_LOCK:
                    _CMD_QUEUE.append(item)
                print(f"[probe] enqueue {item}", flush=True)
                return self._json({"ok": True, "queued": len(_CMD_QUEUE)})
            try:
                body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
            except Exception:
                body = {}
            if route == "/letter/send":
                return self._letter_send(body)
            if route == "/letter/resend":
                return self._letter_resend(body)
            if route == "/letter/share":
                return self._ok({"shareId": f"local-{body.get('letterId') or body.get('letter_id') or 0}"})
            if route == "/signIn":
                return self._sign_in(body)
            if route == "/editProfile":
                return self._ok(self._profile())
            if route == "/submitPreferenceSurvey":
                return self._ok({"status": 2})
            if route == "/createFeedback":
                return self._ok({"ok": True})
            if route == "/genObjectUploadUrl":
                return self._ok({"url": "", "key": "", "headers": {}, "exist": False})
            if route == "/generateShareToken":
                return self._ok({"shareToken": ""})
            return self._ok({})
        except Exception as e:
            print(f"[err] POST {path}: {e}")
            return self._err(str(e))

    # ---------- 业务 ----------
    def _profile(self):
        uid = self._uid()
        return {
            "uid": int(uid) if uid.isdigit() else 10000,
            "nickname": "旅行者",
            "gender": "unknown",
            "birthdate": 0,
        }

    def _user_info(self):
        # 启动链路读的是 /getUserInfo，status>=2 才会进首页，token 用于原生侧建会话
        # uid 可被 config.json 的 "uid" 覆盖：设为 "" 时原生/前端拿不到 uid，
        # 壁纸层的斜向 uid 水印就没有内容可画（置空 = 隐形水印）。
        uid = str(CONFIG.get("uid", "10000"))
        if uid == "":
            uid = self._uid() or ""
        return {
            "uid": int(uid) if str(uid).isdigit() else uid,
            "status": 2,                  # SURVEY_ANALYZED
            "modelGatewayToken": "offline",
            "userInfo": self._profile(),
        }

    def _sign_in(self, body):
        uid = self._uid()
        return self._ok({
            "uid": int(uid) if uid.isdigit() else 10000,
            "status": 2,                 # SURVEY_ANALYZED，跳过问卷与登录页
            "modelGatewayToken": "offline",
            "isNew": False,
            "userInfo": self._profile(),
        })

    def _letter_list(self, q):
        uid = self._uid()
        # 前端把 pageSize 经过 snake_case 转换后发成 page_size，两种都接
        raw_size = q.get("page_size") or q.get("pageSize") or 20
        raw_cursor = q.get("cursor") or 0
        page_size = max(1, min(50, int(raw_size)))
        cursor = int(raw_cursor)
        with _lock:
            store = _read_store()
        mine = [x for x in store["letters"] if x.get("uid") == uid]
        mine.sort(key=lambda x: x["createdAt"], reverse=True)
        page = mine[cursor:cursor + page_size]
        nxt = cursor + len(page)
        limit = CONFIG.get("daily_letter_limit", 3)
        return self._ok({
            "list": [_list_item(x) for x in page],
            "hasMore": nxt < len(mine),
            "nextCursor": nxt,
            "total": len(mine),
            "remainingToday": max(0, limit - _sent_today(store, uid)),
        })

    def _letter_detail(self, q):
        raw = q.get("letter_id") or q.get("letterId") or 0
        lid = int(raw)
        with _lock:
            store = _read_store()
            letter = next((x for x in store["letters"] if x["letterId"] == lid), None)
            if not letter:
                return self._err("信件不存在")
            if letter.get("isRead") == 0:
                letter["isRead"] = 1
                _write_store(store)
        return self._ok(_detail(letter))

    def _unread(self):
        uid = self._uid()
        with _lock:
            store = _read_store()
        cnt = sum(1 for x in store["letters"] if x.get("uid") == uid
                  and x.get("isRead") == 0 and x["letterStatus"] == ST_REPLIED)
        return self._ok({"unreadCount": cnt})

    def _letter_send(self, body):
        uid = self._uid()
        content = (body.get("content") or "").strip()
        if not content:
            return self._err("信不能是空的")
        with _lock:
            store = _read_store()
            limit = CONFIG.get("daily_letter_limit", 3)
            if _sent_today(store, uid) >= limit:
                return self._err("今天的信已经写完了，明天再来吧")
            store["seq"] = int(store.get("seq", 0)) + 1
            lid = store["seq"]
            now = int(time.time())
            delay = _pick_reply_delay()
            # 顺序：回信必须按写信顺序来。后面那封的送达时间不能早于前面那封，
            # 否则随机延迟会让「后写的先回」。这里以同 uid 已有信件的最后一个
            # 送达时间为地板，再留一个 5–15 分钟的间隔，避免一分钟内连回两封。
            prev = max(
                [int(x.get("deliverAt") or x.get("repliedAt") or 0)
                 for x in store["letters"] if x.get("uid") == uid] or [0]
            )
            gap = random.randint(5, 15) * 60
            deliver_at = max(now + delay, prev + gap)
            store["letters"].append({
                "letterId": lid,
                "uid": uid,
                "content": content,
                "material": body.get("material") or {"stampId": 0, "paperId": 0},
                "summary": _summary(content),
                "letterStatus": ST_PENDING,
                "auditStatus": AUDIT_PENDING,
                "replyType": RT_NONE,
                "replyText": "",
                "replyVideoUrl": "",
                "isRead": 1,
                "createdAt": now,
                # 送达时间：调度器到点才生成回信。存进记录里，重启也不丢。
                "deliverAt": deliver_at,
                "replyDelay": deliver_at - now,
                "repliedAt": 0,
            })
            _write_store(store)
        eta = time.strftime("%H:%M", time.localtime(deliver_at))
        wait = deliver_at - now
        print(f"[letter] #{lid} 已收到：{content[:24]}", flush=True)
        print(f"[letter] #{lid} 预计 {eta} 回信（+{wait // 60} 分 {wait % 60} 秒）", flush=True)
        return self._ok({"letterId": lid, "letterStatus": ST_PENDING})

    def _letter_resend(self, body):
        lid = int(body.get("letterId") or body.get("letter_id") or 0)
        with _lock:
            store = _read_store()
            letter = next((x for x in store["letters"] if x["letterId"] == lid), None)
            if not letter:
                return self._err("信件不存在")
            letter["letterStatus"] = ST_PENDING
            letter["replyText"] = ""
            letter["replyType"] = RT_NONE
            # 重发不用等两小时，走单独的短延迟；顺序仍由调度器把关
            delay = int(float(CONFIG.get("reply_resend_delay_minutes", 2)) * 60)
            letter["deliverAt"] = int(time.time()) + delay
            letter["replyDelay"] = delay
            letter["replyTries"] = 0
            _write_store(store)
        print(f"[letter] #{lid} 重新投递，+{delay // 60} 分后回信", flush=True)
        return self._ok({"letterId": lid, "letterStatus": ST_PENDING})


def main():
    ap = argparse.ArgumentParser(description="BSide: Olivia Lin 本地假服务")
    ap.add_argument("--port", type=int, default=CONFIG.get("port", 8787))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--reply", metavar="TEXT", help="不启动服务，直接让她回这一封（调人设用）")
    ap.add_argument("--reply-file", metavar="PATH", help="同上，从 txt 文件读来信用")
    ap.add_argument("--uid", default="10000", help="用哪个 uid 的信件历史（默认 10000）")
    ap.add_argument("--no-history", action="store_true", help="不带历史信件，模拟第一封信")
    ap.add_argument("--show-prompt", action="store_true", help="额外打印实际发给模型的 messages")
    args = ap.parse_args()

    if args.reply or args.reply_file:
        if args.reply_file:
            with open(args.reply_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
        else:
            content = args.reply.strip()
        if not content:
            print("来信是空的")
            return
        if args.no_history:
            CONFIG["llm"]["history_letters"] = 0

        if args.show_prompt:
            msgs = _build_messages(content, args.uid, -1)
            bar = "=" * 62
            print(bar)
            print(f"人设文件：{CONFIG.get('persona_file')}（{len(msgs[0]['content'])} 字符）")
            print(f"历史消息：{len(msgs) - 2} 条")
            print(bar)
            for m in msgs:
                print(f"--- {m['role']} ---")
                print(m["content"])
                print()
            print(bar)

        text, src = _compose_reply(-1, content, args.uid)
        label = ("LLM:" + CONFIG["llm"]["model"]) if src == "llm" else "内置模板（config.json 里没填 api_key）"
        print(f"[来源] {label}\n")
        print(text)
        return

    _ensure_data()
    global PUBLIC_BASE
    host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    PUBLIC_BASE = f"http://{host}:{args.port}"
    os.makedirs(MEDIA_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    llm = "on" if CONFIG["llm"].get("api_key") else "off(模板回信)"
    persona_path = os.path.join(BASE_DIR, CONFIG.get("persona_file") or "persona.md")
    print(f"BSide 本地假服务已启动  http://{args.host}:{args.port}")
    print(f"  信件存储：{LETTER_FILE}")
    print(f"  人设文件：{persona_path}"
          f"{'' if os.path.exists(persona_path) else '  <-- 不存在，将用内置人设'}")
    print(f"  LLM：{llm}    记忆：最近 {CONFIG['llm'].get('history_letters', 0)} 封")
    start_scheduler()
    print("  按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
