#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 feapp.dat 打一个"接管配置"补丁

做的事只有一件：在 feapp/index.html 里注入一段前置脚本，改写 getClientConfig 的返回值，
把 offlineMode 关掉、把 toyApiUrl 指到本地假服务。业务代码一行不改。

用法：
  只生成不发装（安全，安装目录零写入）：
    py -3.12 patch_feapp.py --api-base http://127.0.0.1:8787
  确认无误后再装（会先把原文件备份成 feapp.dat.bak.<时间戳>）：
    py -3.12 patch_feapp.py --api-base http://127.0.0.1:8787 --apply
  回滚：
    py -3.12 patch_feapp.py --restore
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
import time
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE_DIR, "dist")
DEFAULT_SRC = r"C:/Games/steam/steamapps/common/BSide Olivia Lin Test/0.0.9.627/resources/feapp.dat"

PATCH_HTML = r"""  <script>
  (function () {
    // 只做两件事：预置本地账号、给服务端一个到达信号（排障用）。
    // 真正的离线解除在 main.js 里（见 patch_main_js）—— window.cefViewQuery 是
    // QCefView 用 defineProperty 锁死的不可配置属性，劫持不了，只能在配置落进
    // store 的位置改。
    var BASE = "__API_BASE__";
    try {
      if (!localStorage.getItem("uid")) localStorage.setItem("uid", "10000");
      if (!localStorage.getItem("username")) localStorage.setItem("username", "10000");
    } catch (e) {}
    try { fetch(BASE + "/health?boot=1").catch(function () {}); } catch (e) {}

    // ---- 播放事件桥接：截获 app 注册的原生事件处理器，让本地播放能驱动 UI ----
    // 进度条/播放状态只认 ToyPianistClient 的 "webPlayerControl" 事件（原生播放器
    // 已死，事件永远不来）。这里包一层 addEventListener 把 handler 留底，
    // 之后本地播放器用 window.__olvEmit({event:"timeupdate"|"ended"|...}) 直接喂给 app。
    (function () {
      var iv = setInterval(function () {
        var t = window.ToyPianistClient;
        if (!t || t.__olvWrapped) { if (t) clearInterval(iv); return; }
        clearInterval(iv);
        try {
          t.__olvWrapped = 1;
          var hs = [];
          var oa = t.addEventListener, orr = t.removeEventListener;
          t.addEventListener = function (n, f) {
            if (n === "webPlayerControl" && typeof f === "function" && hs.indexOf(f) < 0) hs.push(f);
            return oa.call(t, n, f);
          };
          t.removeEventListener = function (n, f) {
            var i = hs.indexOf(f); if (i >= 0) hs.splice(i, 1);
            return orr.call(t, n, f);
          };
          window.__olvEmit = function (ev) {
            var n = 0;
            for (var i = 0; i < hs.length; i++) { try { hs[i](ev); n++; } catch (e) {} }
            return n;
          };
        } catch (e) {}
      }, 30);
      setTimeout(function () { clearInterval(iv); }, 15000);
    })();

    // ---- 直驱 pinia store（替代事件桥）----
    // 上面那层 addEventListener 包装实际上从未生效：QCefView 用 defineProperty
    // 把 ToyPianistClient.addEventListener 锁成了 writable:false /
    // configurable:false，对它赋值是静默失败（非严格模式不报错），hs 永远是空的。
    // 实测结论：timeupdate / ended 从来没送进过 app，进度条恒 0、播完不切歌。
    // 修法是绕过事件桥直接写 store（以下均已在运行中的页面活体验证过）：
    //   · playlist.songProgress 是裸 ref，可写 —— 驱动进度条；
    //   · playlist.handlePlayNext() 等价 app 收到 ended 后的自动切歌；
    //   · playlist.isPlaying 是 ao() 只读包装，只能读不能写 —— 用它判断
    //     handlePlayNext 有没有切上歌，没切上（列表播完）就补一个 stopCurrentSong；
    //   · ambientSound.setMuted(true/false) 控制待机背景音乐（见下）。
    function piniaStore(id) {
      try {
        var el = document.getElementById("app");
        var gp = el && el.__vue_app__.config.globalProperties;
        var pin = gp && gp.$pinia;
        return (pin && pin._s && pin._s.get(id)) || null;
      } catch (e) { return null; }
    }
    // ---- 待机背景音乐联动 ----
    // 壁纸视频（mpv）带自己的音轨，和 ambience 背景音乐叠在一起。视频开播时
    // 静音 ambience，停播时恢复原来的静音状态。syncToClient:false 只改本端，
    // 不回写设置存储（避免把"临时静音"持久化）。
    var ambSaved = null;
    function ambMute(on) {
      try {
        var amb = piniaStore("ambientSound");
        if (!amb || typeof amb.setMuted !== "function") return;
        if (on) {
          if (ambSaved === null) ambSaved = !!amb.isMuted;
          if (!amb.isMuted) amb.setMuted(true, { syncToClient: false });
        } else if (ambSaved !== null) {
          amb.setMuted(ambSaved, { syncToClient: false });
          ambSaved = null;
        }
      } catch (e) {}
    }
    function syncProgress(t) {
      try { var pl = piniaStore("playlist"); if (pl) pl.songProgress = t; } catch (e) {}
    }
    function onEnded() {
      try {
        var pl = piniaStore("playlist");
        if (!pl) { ambMute(false); wpStop(); return; }
        // 播单激活时由播单队列接管切歌（顺序/循环/随机）：
        // 直接 loadfile 下一首，mpv 不销毁，桌面画面无缝衔接；
        // ambMute 也保持开着（下一首 MV 继续出声）。
        if (olvQueueAdvance(pl)) return;
        ambMute(false);
        wpStop();
        pl.songProgress = pl.nowPlayingDuration || tk.dur || 0;
        pl.handlePlayNext && pl.handlePlayNext();
        // handlePlayNext 找不到下一首时什么都不做、isPlaying 仍是 true ——
        // 但那只是残留状态，等一个 tick 后没有新歌开播就主动收尾。
        setTimeout(function () {
          try {
            var p2 = piniaStore("playlist");
            if (p2 && !p2.isPlaying) return; // 已切歌在播
            // 切歌成功时 isPlaying 会短暂为 true；这里只在"确实没在播"时清场
            if (p2 && p2.currentNameKey && !p2.isPlaying) p2.stopCurrentSong && p2.stopCurrentSong();
          } catch (e) {}
        }, 800);
      } catch (e) {}
    }

    // ---- 只读探测：让应用自己把「解密后的离线歌单」交出来 ----
    // songlist.dat 是 AES 信封，但原生存着一份解好的；直接向桥接要一次结果，
    // POST 回本地服务落盘。这样不用逆向密钥也能看清曲目结构（字段/URL 形态）。
    // 只读一次，成功即停；失败按次数退避重试。
    var probeTries = 0;
    function probeSongList() {
      if (probeTries >= 5) return;
      probeTries++;
      try {
        window.cefViewQuery({
          request: JSON.stringify({ action: "getOfflineSongList", data: {} }),
          onSuccess: function (a) {
            probeTries = 99;
            var txt = String(a || "");
            report("songlist-ok", { len: txt.length, head: txt.slice(0, 160) });
            try { fetch(BASE + "/dump/songlist", { method: "POST", body: txt }); } catch (e) {}
          },
          onFailure: function (a, c) {
            report("songlist-fail", { err: String(a || c || "") });
          }
        });
      } catch (e) {
        report("songlist-throw", { err: String(e) });
      }
    }
    setTimeout(probeSongList, 7000);

    // ---- 写信入口兜底 + 信箱页返回 ----
    // 原生那条「桌面入口」在 Lite 模式下被门控住了（FloatingControlBar / LiteControlGizmo
    // 都停泊在 -32000，Web 侧的 toggleLetterEntry 只是埋点，动不了它）。但 Lite 模式下
    // /collection 路由挂的就是 MailBoxView，所以这里直接在页面里加一个悬浮按钮跳过去。
    //
    // 注意：MailBoxView 本身没有返回逻辑（它是给 PC 端带侧边导航的布局用的），
    // Lite 下进去就是死胡同。所以这里做成"上下文按钮"——
    //   /collection  → 显示「返回」，回到进来之前的页面（兜底 /studio 曲库）
    //   其他路径      → 显示「写信」，跳 /collection
    var LITE_ROOT = "/studio";
    var lastPath = "";

    // 把页面状态送回本地服务（排障用）：日志里出现 [fe] {...}
    function report(tag, extra) {
      try {
        var el = document.getElementById("app") || document.body;
        var info = {
          tag: tag,
          hash: location.hash,
          href: location.href,
          bodyKids: document.body ? document.body.children.length : -1,
          appKids: el ? el.children.length : -1,
          appHTML: el ? el.innerHTML.length : -1,
          vw: window.innerWidth,
          vh: window.innerHeight,
          text: ((document.body && document.body.innerText) || "").replace(/\s+/g, " ").slice(0, 120)
        };
        if (extra) { for (var key in extra) { info[key] = extra[key]; } }
        fetch(BASE + "/debug?d=" + encodeURIComponent(JSON.stringify(info)), { cache: "no-store" });
      } catch (e) {}
    }

    // 关键：拿真正的 vue-router 实例来导航。
    // 之前是 history.pushState 伪造历史项 + 手动派发 popstate，路由表看着切了，
    // 但 router 内部状态（currentRoute.matched、滚动位置）没被正确维护，
    // 进一次再出一次就把视图搞成空白。Vue 3 会把 app 实例挂在容器元素的 __vue_app__ 上。
    function getRouter() {
      try {
        var el = document.getElementById("app");
        var app = el && el.__vue_app__;
        var gp = app && app.config && app.config.globalProperties;
        return (gp && gp.$router) || null;
      } catch (e) { return null; }
    }
    // 这个应用是 hash 路由：https://olivia.local/feapp/index.html#/studio
    // location.pathname 永远是 /feapp/index.html，真实的"当前页"在 hash 里。
    // 千万不能用 history.pushState("/collection") 去伪造——那会把基础路径改坏，
    // 相对资源全部解析失败，整页变黑（踩过这个坑）。
    function curRoutePath() {
      var h = location.hash || "";
      if (h.indexOf("#/") === 0) { return h.slice(1); }
      return h === "#" ? "/" : "";
    }
    function navTo(path) {
      var r = getRouter();
      if (r && typeof r.push === "function") {
        var p = r.push(path);
        if (p && p.catch) { p.catch(function (e) { report("push-fail", { to: path, err: String(e) }); }); }
        report("push", { to: path });
        return;
      }
      // 兜底：直接改 hash，由 vue-router 的 hashchange 监听完成导航
      report("hash-nav", { to: path });
      try { location.hash = "#" + path; } catch (e) { report("hash-fail", { to: path, err: String(e) }); }
    }
    function openMailbox() {
      try { sessionStorage.setItem("olivia_prev_path", curRoutePath() || LITE_ROOT); } catch (e) {}
      navTo("/collection");
    }
    function backFromMailbox() {
      var p = LITE_ROOT;
      try { p = sessionStorage.getItem("olivia_prev_path") || LITE_ROOT; } catch (e) {}
      // 防止存进去的就是信箱自己（连点两次写信）
      if (!p || p.indexOf("/collection") === 0) p = LITE_ROOT;
      navTo(p);
    }
    // ---- 视觉：跟原生控件同一套语言 ----
    // 颜色直接取自应用自身：主按钮米色 #DCD4CA / 文字 #28292F（就是曲库底部那颗
    // 「写信」的配色），页头圆形控件背景 #1E1E20 / 图标 #FFFBF0。
    var STYLE_ID = "olivia-nav-style";
    function injectStyle() {
      if (document.getElementById(STYLE_ID)) return;
      var s = document.createElement("style");
      s.id = STYLE_ID;
      s.textContent = [
        // 兜底：即使 main.js 那两处补丁没生效，也不显示 UID 水印
        ".watermark-overlay{display:none!important}",
        ".olv-btn{position:fixed;z-index:2147483000;display:inline-flex;align-items:center;",
        "  font-family:inherit;cursor:pointer;border:0;outline:0;background:none;",
        "  user-select:none;-webkit-tap-highlight-color:transparent;",
        "  animation:olvIn .26s cubic-bezier(.22,.9,.3,1) both}",
        "@keyframes olvIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}",
        // 曲库：米色胶囊。放在「测试版」标签左侧的空白处，不与任何原生控件重叠
        "#olivia-write-entry{right:148px;top:60px;height:34px;padding:0 18px 0 15px;gap:8px;",
        "  border-radius:999px;background:#DCD4CA;color:#28292F;",
        "  font-size:13px;font-weight:500;letter-spacing:.09em;line-height:1;",
        "  box-shadow:0 6px 18px rgba(0,0,0,.42),inset 0 1px 0 rgba(255,255,255,.55);",
        "  transition:background .18s,transform .18s,box-shadow .18s}",
        "#olivia-write-entry:hover{background:#EBE4DA;transform:translateY(-1px);",
        "  box-shadow:0 10px 24px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.7)}",
        "#olivia-write-entry:active{transform:scale(.985)}",
        "#olivia-write-entry svg{flex:0 0 auto;opacity:.82}",
        // 信箱：28px 圆形图标按钮，同样落在「测试版」左侧，不遮挡原生控件
        "#olivia-back-entry{right:148px;top:63px;width:28px;height:28px;padding:0;",
        "  justify-content:center;border-radius:50%;background:#1E1E20;color:#FFFBF0;",
        "  border:1px solid rgba(255,255,255,.10);",
        "  transition:background .18s,border-color .18s,transform .18s}",
        "#olivia-back-entry:hover{background:#2B2B2F;border-color:rgba(255,255,255,.24)}",
        "#olivia-back-entry:active{transform:scale(.92)}"
      ].join("");
      (document.head || document.documentElement).appendChild(s);
    }
    var ICON_MAIL = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" '
      + 'stroke="currentColor" stroke-width="1.35" stroke-linecap="round" '
      + 'stroke-linejoin="round"><rect x="1.9" y="3.6" width="12.2" height="8.8" rx="1.7"/>'
      + '<path d="M2.5 4.5 8 8.7l5.5-4.2"/></svg>';
    var ICON_BACK = '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" '
      + 'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" '
      + 'stroke-linejoin="round"><path d="M9.9 3.3 5.1 8l4.8 4.7"/></svg>';
    function makeBtn(id, label, title, html) {
      var b = document.createElement("button");
      b.id = id;
      b.type = "button";
      b.className = "olv-btn";
      b.title = title;
      b.setAttribute("aria-label", title);
      b.innerHTML = html;
      return b;
    }
    function mountNav() {
      try {
        if (!document.body) return;
        injectStyle();
        var cur = curRoutePath();
        if (cur !== lastPath) {
          lastPath = cur;
          report("route", { route: cur });
          setTimeout(function () { report("route+1.5s", { route: curRoutePath() }); }, 1500);
        }
        var inMail = cur.indexOf("/collection") === 0;
        var w = document.getElementById("olivia-write-entry");
        var k = document.getElementById("olivia-back-entry");
        if (inMail) {
          if (w) w.parentNode && w.parentNode.removeChild(w);
          if (!k) {
            k = makeBtn("olivia-back-entry", "返回", "返回曲库", ICON_BACK);
            k.onclick = backFromMailbox;
            document.body.appendChild(k);
          }
        } else {
          if (k) k.parentNode && k.parentNode.removeChild(k);
          if (!w) {
            w = makeBtn("olivia-write-entry", "写信", "打开信箱", ICON_MAIL + "<span>写信</span>");
            w.onclick = openMailbox;
            document.body.appendChild(w);
          }
        }
      } catch (e) {}
    }
    // ---- 远程操作通道（排障用）----
    // 应用常驻桌面层，实际使用时窗口经常被浏览器等程序整个盖住；SetForegroundWindow
    // 会被前台锁拒绝，PostMessage 又进不了 Chromium 的输入管线，物理点击根本落不到页面上。
    // 这里反过来做：页面自己轮询本地服务，收到指令后派发**真实的 DOM 事件**。
    // 不依赖焦点、不依赖 Z 序，还能顺带把 DOM 清点一遍。
    function evOpt(x, y) {
      return {
        bubbles: true, cancelable: true, composed: true, view: window,
        clientX: x, clientY: y, screenX: x, screenY: y,
        button: 0, buttons: 1, detail: 1,
        pointerId: 1, pointerType: "mouse", isPrimary: true, width: 1, height: 1
      };
    }
    function fire(el, x, y) {
      var o = evOpt(x, y);
      ["pointerover", "pointerenter", "mouseover", "mouseenter",
       "pointermove", "mousemove", "pointerdown", "mousedown",
       "pointerup", "mouseup", "click"].forEach(function (t) {
        var C = (t.indexOf("pointer") === 0 && window.PointerEvent) ? window.PointerEvent : MouseEvent;
        try { el.dispatchEvent(new C(t, o)); } catch (e) {}
      });
    }
    function hitAt(x, y) {
      var el = document.elementFromPoint(x, y);
      if (!el) return "no-element";
      fire(el, x, y);
      var r = el.getBoundingClientRect();
      return el.tagName.toLowerCase() + (el.className && typeof el.className === "string" ? "." + el.className.split(" ")[0] : "")
        + " rect=" + Math.round(r.left) + "," + Math.round(r.top) + "," + Math.round(r.width) + "x" + Math.round(r.height);
    }
    // 清点页面上所有可点元素，带位置，方便从外部精确定位
    function inventory() {
      var sel = "button,a,[role=button],svg,i,span";
      var list = [];
      var all = document.querySelectorAll(sel);
      for (var i = 0; i < all.length; i++) {
        var e = all[i], r = e.getBoundingClientRect();
        if (r.width < 6 || r.height < 6) continue;
        var t = (e.innerText || e.getAttribute("aria-label") || e.title || "").replace(/\s+/g, " ").slice(0, 18);
        list.push({
          t: t, tag: e.tagName.toLowerCase(),
          c: (typeof e.className === "string" ? e.className.split(" ")[0] : ""),
          x: Math.round(r.left), y: Math.round(r.top),
          w: Math.round(r.width), h: Math.round(r.height)
        });
        if (list.length >= 70) break;
      }
      return list;
    }
    // ---- 本地曲目播放器 ----
    // 停服后原生不再创建播放视图：LiteUIController 收到 sendWebPlayerControlCmd
    // 只打印一行 JSON 就静默返回，桌面层永远停在 ambience 场景（日志证实收到命令后
    // 无任何后续动作）。但曲目资源本身是明文 H.264/AAC，CEF 直接能解——实测
    // readyState=4、时长 187s、currentTime 正常推进、按 Range 拉流无阻碍。
    // 所以这里自己拿一个 <video> 播，并把 main.js 里的 Ct() 挂过来一起触发。
    var PLYR = "olivia-layer";
    function pickUrl(song) {
      var list = (song && song.videoByTodView) || [];
      var i;
      if (!list.length) return (song && song.videoUrl) || "";
      // 按时段选画面：<17:30 → TOD12，17:30~20:00 → TOD1730，之后 → TOD20
      var d = new Date();
      var h = d.getHours() + d.getMinutes() / 60;
      var want = h < 17.5 ? "TOD12" : (h < 20 ? "TOD1730" : "TOD20");
      for (i = 0; i < list.length; i++) { if (list[i].tod === want && list[i].url) return list[i].url; }
      for (i = 0; i < list.length; i++) { if (list[i].url) return list[i].url; }
      return (song && song.videoUrl) || "";
    }
    function closePlayer() {
      var w = document.getElementById(PLYR);
      if (!w) return;
      var v = w.querySelector("video");
      if (v) { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {} }
      w.parentNode && w.parentNode.removeChild(w);
      // 页内播放被手动关掉时，驱动器和待机音乐联动也要一起收尾
      tkStop();
      ambMute(false);
    }
    function cefPlay(url) {
      try {
        var wrap = document.getElementById(PLYR);
        if (!wrap) {
          wrap = document.createElement("div");
          wrap.id = PLYR;
          // 全屏覆盖整个窗口，16:9 素材用 contain 留黑边，不裁画面
          wrap.style.cssText = "position:fixed;left:0;top:0;width:100vw;height:100vh;"
            + "background:#000;z-index:2147483646;display:flex;align-items:center;justify-content:center";
          var vd = document.createElement("video");
          vd.setAttribute("playsinline", "");
          vd.controls = true;
          vd.style.cssText = "width:100%;height:100%;object-fit:contain;background:#000";
          var xb = document.createElement("button");
          xb.type = "button";
          xb.title = "关闭";
          xb.innerHTML = "&#x2715;";
          xb.style.cssText = "position:absolute;right:16px;top:14px;width:34px;height:34px;"
            + "border-radius:50%;background:rgba(30,30,32,.74);color:#FFFBF0;"
            + "border:1px solid rgba(255,255,255,.18);font-size:15px;line-height:1;"
            + "cursor:pointer;z-index:2147483647";
          xb.onclick = function (ev) { if (ev) ev.stopPropagation(); closePlayer(); };
          wrap.appendChild(vd);
          wrap.appendChild(xb);
          document.body.appendChild(wrap);
        }
        var v = wrap.querySelector("video");
        if (v.getAttribute("src") !== url) { v.src = url; }
        var p = v.play();
        if (p && p.then) {
          p.then(function () { ambMute(true); }).catch(function () {});
        }
      } catch (err) {}
    }
    // 本地播放驱动器：进度条和播放状态只认 "webPlayerControl" 事件，
    // 这里用定时器模拟 timeupdate，播完发 ended（app 自己会走下一首/单曲循环逻辑）。
    var tk = { iv: 0, t: 0, dur: 0, key: "" };
    function emitEvt(ev, cur) {
      if (!window.__olvEmit) return;
      var payload = ev === "timeupdate" ? { event: "timeupdate", currentTime: cur } : { event: ev };
      try { window.__olvEmit(payload); } catch (e) {}
    }
    function tkStop() { if (tk.iv) { clearInterval(tk.iv); tk.iv = 0; } }
    function tkStart(from, dur) {
      tkStop();
      tk.t = Math.max(0, Number(from) || 0); tk.dur = Number(dur) || 0;
      tk.iv = setInterval(function () {
        tk.t += 0.5;
        // 直驱进度条（事件桥被 QCefView 锁死，见 syncProgress 上方注释）
        syncProgress(tk.t);
        if (tk.dur && tk.t >= tk.dur) { tkStop(); onEnded(); return; }
        emitEvt("timeupdate", tk.t);
      }, 500);
    }
    function wpStop() {
      try { var rq = fetch(BASE + "/wallpaper/stop", { cache: "no-store" }); if (rq && rq.catch) rq.catch(function () {}); } catch (e) {}
    }
    function wpPlay(key, t) {
      try {
        return fetch(BASE + "/wallpaper/play?name=" + encodeURIComponent(key) + "&t=" + (t || 0), { cache: "no-store" })
          .then(function (r) { return r.json(); });
      } catch (x) { return Promise.reject(x); }
    }
    function pickMeta(song) {
      var list = (song && song.videoByTodView) || [];
      var d = new Date();
      var h = d.getHours() + d.getMinutes() / 60;
      var want = h < 17.5 ? "TOD12" : (h < 20 ? "TOD1730" : "TOD20");
      var i, item = null;
      for (i = 0; i < list.length; i++) { if (list[i].tod === want && list[i].url) { item = list[i]; break; } }
      if (!item) for (i = 0; i < list.length; i++) { if (list[i].url) { item = list[i]; break; } }
      if (!item && song && song.videoUrl) item = { url: song.videoUrl, duration: song.duration };
      var dur = 0;
      if (item) dur = parseFloat(item.duration) || parseFloat(song && song.duration) || 0;
      return { url: (item && item.url) || (song && song.videoUrl) || "", dur: dur };
    }
    // 优先把画面交给桌面壁纸播放器（wallpaper.py，合并进主服务 8787）：
    // 视频铺到桌面背景，游戏窗口不被遮挡，效果等同 Wallpaper Engine。
    // 如果那个服务没开，自动退回本页内全屏播放。两条路都由 tk 驱动 UI 状态。
    function olvPlayer(e) {
      try {
        var cmd = e && e.cmd, song = e && e.song;
        if (cmd === "stop" || cmd === "close") { tkStop(); ambMute(false); wpStop(); closePlayer(); return; }
        if (cmd === "timeupdate" && e.position != null) {
          // 用户拖进度条：app 已自己更新进度，这里对齐本地播放器并让壁纸跳到同一位置
          tk.t = Number(e.position) || 0;
          if (tk.key) { var r2 = wpPlay(tk.key, tk.t); if (r2 && r2.catch) r2.catch(function () {}); }
          return;
        }
        if (cmd === "setPlayMode" || cmd === "setVolume" || cmd === "popupWidget") return;
        if (!song) return;
        // 区分播单发起的播放与曲库直接点播：后者取消播单接管
        if (window.__olvFromQueue) { window.__olvFromQueue = 0; }
        else { queue.active = false; }
        var meta = pickMeta(song);
        if (!meta.url) return;
        var mm = /\/media\/([^\/]+)\//.exec(meta.url);
        var key = mm ? mm[1] : "";
        tk.key = key;
        var fallback = function () { cefPlay(meta.url); };
        tkStart(0, meta.dur);
        wpPlay(key, 0).then(function (j) {
          if (!j || !j.ok) { fallback(); return; }
          // 壁纸视频开播：压掉待机背景音乐，避免两路声音叠在一起
          ambMute(true);
        }).catch(fallback);
      } catch (err) {}
    }
    window.__olvPlayer = olvPlayer;

    // ================= 播单 =================
    // 自定义歌单：多播单 / localStorage 持久化 / 顺序·循环·随机三种模式。
    // 入口挂在曲库左侧风格栏（与「轻音乐」同级）。播单激活时接管 onEnded：
    // 按模式取下一首 playSonglistItem，顺序模式播完自动停止。
    var PL_KEY = "olv_playlists_v1";
    var PL_MODE_LABEL = { seq: "顺序", loop: "循环", rand: "随机" };
    var pls = plsLoad();
    var plSelId = null;        // 面板当前选中的播单 id
    var plAdding = false;      // 面板是否处于「添加曲目」视图
    var plSearch = "";         // 添加视图的过滤词
    var queue = { id: null, pos: -1, active: false };

    function plsLoad() {
      try {
        var v = JSON.parse(localStorage.getItem(PL_KEY) || "[]");
        return Array.isArray(v) ? v : [];
      } catch (e) { return []; }
    }
    function plsSave() {
      try { localStorage.setItem(PL_KEY, JSON.stringify(pls)); } catch (e) {}
    }
    function plById(id) {
      for (var i = 0; i < pls.length; i++) { if (pls[i].id === id) return pls[i]; }
      return null;
    }
    function plUid() {
      return "p" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    }
    function songByKey(key) {
      var cat = piniaStore("offlineCatalog");
      var list = (cat && cat.songs) || [];
      for (var i = 0; i < list.length; i++) { if (list[i].nameKey === key) return list[i]; }
      return null;
    }

    // ---- 播放队列 ----
    // onEnded 里优先调用；返回 true 表示播单已接管（已切下一首或主动收尾）
    function olvQueueAdvance(pl) {
      if (!queue.active) return false;
      var s = qPick();
      if (!s) {
        queue.active = false;
        try { if (pl) pl.stopCurrentSong && pl.stopCurrentSong(); } catch (e) {}
        return true;
      }
      try {
        window.__olvFromQueue = 1;
        pl.playSonglistItem(s);
      } catch (e) { queue.active = false; }
      return true;
    }
    function qPick() {
      var p = plById(queue.id);
      if (!p || !p.items.length) return null;
      var n = p.items.length;
      if (p.mode === "rand") {
        if (n === 1) { queue.pos = 0; return songByKey(p.items[0]); }
        var nx = queue.pos;
        while (nx === queue.pos) { nx = Math.floor(Math.random() * n); }
        queue.pos = nx;
        return songByKey(p.items[nx]);
      }
      var np = queue.pos + 1;
      if (np >= n) {
        if (p.mode !== "loop") { return null; }   // 顺序模式播完 → 停
        np = 0;
      }
      queue.pos = np;
      return songByKey(p.items[np]);
    }
    function qStart(p, fromIdx) {
      if (!p || !p.items.length) { return; }
      var idx = Math.max(0, Math.min(fromIdx || 0, p.items.length - 1));
      var s = songByKey(p.items[idx]);
      if (!s) { return; }
      queue.id = p.id; queue.pos = idx; queue.active = true;
      var pl = piniaStore("playlist");
      if (pl) {
        try { window.__olvFromQueue = 1; pl.playSonglistItem(s); }
        catch (e) { queue.active = false; }
      }
      closePlPanel();
    }

    // ---- 面板 UI（纯自建 DOM，挂在 body 上，不进 Vue 管理的子树）----
    var PL_STYLE_ID = "olv-pl-style";
    function elx(tag, cls, text) {
      var e = document.createElement(tag);
      if (cls) { e.className = cls; }
      if (text != null) { e.textContent = text; }
      return e;
    }
    function plInjectStyle() {
      if (document.getElementById(PL_STYLE_ID)) { return; }
      var s = document.createElement("style");
      s.id = PL_STYLE_ID;
      s.textContent = [
        "#olv-pl-mask{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:2147483000;",
        "  display:flex;align-items:center;justify-content:center}",
        "#olv-pl-panel{width:760px;max-width:92vw;height:540px;max-height:86vh;background:#1E1E20;",
        "  border-radius:14px;border:1px solid rgba(255,255,255,.10);color:#FFFBF0;",
        "  display:flex;flex-direction:column;overflow:hidden;font-size:13px;",
        "  box-shadow:0 24px 64px rgba(0,0,0,.6)}",
        ".olv-pl-head{display:flex;align-items:center;gap:10px;padding:14px 16px 10px}",
        ".olv-pl-title{font-size:15px;font-weight:600;letter-spacing:.06em;flex:1}",
        ".olv-pl-btn{cursor:pointer;border:1px solid rgba(255,255,255,.14);",
        "  background:rgba(255,255,255,.06);color:#FFFBF0;border-radius:8px;",
        "  padding:5px 12px;font-size:12px;line-height:1.4;user-select:none;",
        "  transition:background .15s,border-color .15s}",
        ".olv-pl-btn:hover{background:rgba(255,255,255,.12);border-color:rgba(255,255,255,.28)}",
        ".olv-pl-btn.primary{background:#DCD4CA;color:#28292F;border-color:#DCD4CA;font-weight:500}",
        ".olv-pl-btn.primary:hover{background:#EBE4DA}",
        ".olv-pl-body{flex:1;display:flex;min-height:0;border-top:1px solid rgba(255,255,255,.08)}",
        ".olv-pl-side{width:200px;flex:0 0 auto;border-right:1px solid rgba(255,255,255,.08);",
        "  overflow-y:auto;padding:8px}",
        ".olv-pl-main{flex:1;display:flex;flex-direction:column;min-width:0}",
        ".olv-pl-item{display:flex;align-items:center;gap:8px;padding:8px 10px;border-radius:8px;",
        "  cursor:pointer;color:#DDD;white-space:nowrap;overflow:hidden;user-select:none}",
        ".olv-pl-item:hover{background:rgba(255,255,255,.07)}",
        ".olv-pl-item.sel{background:rgba(220,212,202,.16);color:#FFFBF0}",
        ".olv-pl-item .nm{flex:1;overflow:hidden;text-overflow:ellipsis}",
        ".olv-pl-item .op{flex:0 0 auto;opacity:.55;font-size:12px;padding:0 4px;border-radius:4px;cursor:pointer}",
        ".olv-pl-item .op:hover{opacity:1;background:rgba(255,255,255,.14)}",
        ".olv-pl-tools{display:flex;gap:8px;align-items:center;padding:10px 14px;",
        "  border-bottom:1px solid rgba(255,255,255,.08);flex-wrap:wrap}",
        ".olv-pl-list{flex:1;overflow-y:auto;padding:6px 10px}",
        ".olv-pl-row{display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:8px;color:#DDD}",
        ".olv-pl-row:hover{background:rgba(255,255,255,.07)}",
        ".olv-pl-row .idx{width:20px;text-align:right;opacity:.5;flex:0 0 auto;font-size:12px}",
        ".olv-pl-row .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
        ".olv-pl-row .op{flex:0 0 auto;cursor:pointer;opacity:.5;padding:1px 6px;border-radius:4px;font-size:12px;user-select:none}",
        ".olv-pl-row .op:hover{opacity:1;background:rgba(255,255,255,.14)}",
        ".olv-pl-row.playing .nm{color:#DCD4CA;font-weight:600}",
        ".olv-pl-search{flex:1;min-width:140px;background:rgba(255,255,255,.08);",
        "  border:1px solid rgba(255,255,255,.14);border-radius:8px;color:#FFFBF0;",
        "  padding:6px 10px;font-size:12px;outline:none}",
        ".olv-pl-empty{opacity:.45;padding:22px 10px;text-align:center}",
        ".olv-pl-in{background:rgba(255,255,255,.1);border:0;border-bottom:1px solid #DCD4CA;",
        "  color:#FFFBF0;font-size:13px;padding:2px 4px;outline:none;width:150px}"
      ].join("");
      (document.head || document.documentElement).appendChild(s);
    }
    function openPlPanel() {
      plInjectStyle();
      var mask = document.getElementById("olv-pl-mask");
      if (!mask) {
        mask = elx("div");
        mask.id = "olv-pl-mask";
        var p = elx("div");
        p.id = "olv-pl-panel";
        mask.appendChild(p);
        mask.addEventListener("click", function (ev) {
          if (ev.target === mask) { closePlPanel(); }
        });
        document.body.appendChild(mask);
      }
      plAdding = false;
      renderPlPanel();
    }
    function closePlPanel() {
      var m = document.getElementById("olv-pl-mask");
      if (m && m.parentNode) { m.parentNode.removeChild(m); }
    }
    function renderPlPanel() {
      var p = document.getElementById("olv-pl-panel");
      if (!p) { return; }
      p.innerHTML = "";
      if (!plSelId && pls.length) { plSelId = pls[0].id; }

      // 头部
      var head = elx("div", "olv-pl-head");
      head.appendChild(elx("div", "olv-pl-title", "我的播单"));
      var nb = elx("div", "olv-pl-btn", "＋ 新建播单");
      nb.onclick = function () {
        var np = { id: plUid(), name: "播单 " + (pls.length + 1), items: [], mode: "seq" };
        pls.push(np); plsSave(); plSelId = np.id; plAdding = false; renderPlPanel();
      };
      head.appendChild(nb);
      var xb = elx("div", "olv-pl-btn", "✕");
      xb.onclick = closePlPanel;
      head.appendChild(xb);
      p.appendChild(head);

      var body = elx("div", "olv-pl-body");
      // 左列：播单列表
      var side = elx("div", "olv-pl-side");
      if (!pls.length) {
        side.appendChild(elx("div", "olv-pl-empty", "还没有播单\n点右上角「＋ 新建播单」"));
      }
      for (var i = 0; i < pls.length; i++) {
        (function (pl) {
          var it = elx("div", "olv-pl-item" + (pl.id === plSelId ? " sel" : ""));
          it.appendChild(elx("div", "nm", pl.name + " (" + pl.items.length + ")"));
          var ren = elx("div", "op", "✎");
          ren.title = "重命名";
          ren.onclick = function (ev) {
            ev.stopPropagation();
            var inp = document.createElement("input");
            inp.className = "olv-pl-in";
            inp.value = pl.name;
            var nmEl = it.querySelector(".nm");
            nmEl.innerHTML = "";
            nmEl.appendChild(inp);
            inp.focus();
            inp.select();
            function done() {
              var v = inp.value.trim();
              if (v) { pl.name = v; plsSave(); }
              renderPlPanel();
            }
            inp.addEventListener("keydown", function (e2) {
              if (e2.key === "Enter") { done(); }
              if (e2.key === "Escape") { renderPlPanel(); }
              e2.stopPropagation();
            });
            inp.addEventListener("blur", done);
            inp.addEventListener("click", function (e2) { e2.stopPropagation(); });
          };
          it.appendChild(ren);
          var del = elx("div", "op", "✕");
          del.title = "删除播单";
          del.onclick = function (ev) {
            ev.stopPropagation();
            for (var k = pls.length - 1; k >= 0; k--) { if (pls[k].id === pl.id) { pls.splice(k, 1); } }
            if (queue.id === pl.id) { queue.active = false; queue.id = null; }
            if (plSelId === pl.id) { plSelId = pls.length ? pls[0].id : null; }
            plsSave(); renderPlPanel();
          };
          it.appendChild(del);
          it.onclick = function () { plSelId = pl.id; plAdding = false; renderPlPanel(); };
          side.appendChild(it);
        })(pls[i]);
      }
      body.appendChild(side);

      // 右列：选中播单详情
      var main = elx("div", "olv-pl-main");
      var cur = plById(plSelId);
      if (!cur) {
        main.appendChild(elx("div", "olv-pl-empty", "选择左侧播单，或新建一个"));
      } else {
        var tools = elx("div", "olv-pl-tools");
        if (plAdding) {
          var back = elx("div", "olv-pl-btn", "‹ 返回");
          back.onclick = function () { plAdding = false; renderPlPanel(); };
          tools.appendChild(back);
          var si = document.createElement("input");
          si.className = "olv-pl-search";
          si.placeholder = "搜索曲目名…";
          si.value = plSearch;
          si.oninput = function () { plSearch = si.value; renderPlListOnly(); };
          tools.appendChild(si);
          var nAvail = 0, ci;
          for (ci = 0; ci < cur.items.length; ci++) { if (songByKey(cur.items[ci])) { nAvail++; } }
          tools.appendChild(elx("div", "olv-pl-title", nAvail + " 首在播单中"));
        } else {
          var pb = elx("div", "olv-pl-btn primary", "▶ 播放全部");
          pb.onclick = function () { qStart(cur, 0); };
          tools.appendChild(pb);
          var mb = elx("div", "olv-pl-btn", "模式：" + (PL_MODE_LABEL[cur.mode] || cur.mode));
          mb.onclick = function () {
            cur.mode = cur.mode === "seq" ? "loop" : (cur.mode === "loop" ? "rand" : "seq");
            plsSave(); renderPlPanel();
          };
          tools.appendChild(mb);
          var ab = elx("div", "olv-pl-btn", "＋ 添加曲目");
          ab.onclick = function () { plAdding = true; renderPlPanel(); };
          tools.appendChild(ab);
          tools.appendChild(elx("div", "olv-pl-title", cur.name + " · " + cur.items.length + " 首"));
        }
        main.appendChild(tools);
        var list = elx("div", "olv-pl-list");
        list.id = "olv-pl-list";
        if (plAdding) { renderAddList(list, cur); }
        else { renderPlList(list, cur); }
        main.appendChild(list);
      }
      body.appendChild(main);
      p.appendChild(body);
    }
    // 添加视图的搜索：只重绘列表不重建输入框（避免丢焦点）
    function renderPlListOnly() {
      var list = document.getElementById("olv-pl-list");
      var cur = plById(plSelId);
      if (!list || !cur) { return; }
      list.innerHTML = "";
      renderAddList(list, cur);
    }
    function renderAddList(list, cur) {
      var cat = piniaStore("offlineCatalog");
      var songs = (cat && cat.songs) || [];
      var kw = (plSearch || "").toLowerCase();
      var shown = 0;
      for (var i = 0; i < songs.length; i++) {
        (function (s) {
          var nm = s.name || s.nameKey || "";
          if (kw && nm.toLowerCase().indexOf(kw) < 0) { return; }
          shown++;
          var row = elx("div", "olv-pl-row");
          row.appendChild(elx("div", "idx", ""));
          row.appendChild(elx("div", "nm", nm));
          var pos = cur.items.indexOf(s.nameKey);
          var op;
          if (pos >= 0) {
            op = elx("div", "op", "✓ 已加入");
            op.title = "点击移出播单";
            op.onclick = function () {
              cur.items.splice(pos, 1); plsSave(); renderPlListOnly();
            };
          } else {
            op = elx("div", "op", "＋ 加入");
            op.onclick = function () {
              cur.items.push(s.nameKey); plsSave(); renderPlListOnly();
            };
          }
          row.appendChild(op);
          list.appendChild(row);
        })(songs[i]);
      }
      if (!shown) { list.appendChild(elx("div", "olv-pl-empty", "没有匹配的曲目")); }
    }
    function renderPlList(list, cur) {
      if (!cur.items.length) {
        list.appendChild(elx("div", "olv-pl-empty", "播单还是空的\n点上方「＋ 添加曲目」从曲库挑选"));
        return;
      }
      for (var i = 0; i < cur.items.length; i++) {
        (function (idx) {
          var key = cur.items[idx];
          var s = songByKey(key);
          var row = elx("div", "olv-pl-row");
          var isCur = queue.active && queue.id === cur.id && queue.pos === idx;
          if (isCur) { row.className += " playing"; }
          row.appendChild(elx("div", "idx", String(idx + 1)));
          row.appendChild(elx("div", "nm", (s && s.name) || key + "（曲库中未找到）"));
          var play = elx("div", "op", "▶");
          play.title = "从这首开始播放";
          play.onclick = function () { qStart(cur, idx); };
          row.appendChild(play);
          if (idx > 0) {
            var up = elx("div", "op", "↑");
            up.title = "上移";
            up.onclick = function () {
              var t = cur.items[idx - 1]; cur.items[idx - 1] = cur.items[idx]; cur.items[idx] = t;
              if (queue.id === cur.id) {
                if (queue.pos === idx) { queue.pos = idx - 1; }
                else if (queue.pos === idx - 1) { queue.pos = idx; }
              }
              plsSave(); renderPlPanel();
            };
            row.appendChild(up);
          }
          if (idx < cur.items.length - 1) {
            var dn = elx("div", "op", "↓");
            dn.title = "下移";
            dn.onclick = function () {
              var t = cur.items[idx + 1]; cur.items[idx + 1] = cur.items[idx]; cur.items[idx] = t;
              if (queue.id === cur.id) {
                if (queue.pos === idx) { queue.pos = idx + 1; }
                else if (queue.pos === idx + 1) { queue.pos = idx; }
              }
              plsSave(); renderPlPanel();
            };
            row.appendChild(dn);
          }
          var rm = elx("div", "op", "✕");
          rm.title = "移出播单";
          rm.onclick = function () {
            cur.items.splice(idx, 1);
            if (queue.id === cur.id) {
              if (queue.pos === idx) { queue.active = false; queue.pos = -1; }
              else if (queue.pos > idx) { queue.pos--; }
            }
            plsSave(); renderPlPanel();
          };
          row.appendChild(rm);
          list.appendChild(row);
        })(i);
      }
    }

    // ---- 曲库风格栏入口：与「轻音乐」同级的「我的播单」----
    // Vue 列表会随筛选重建，所以用定时器检查，被删掉就重插。
    var PL_ENTRY_ID = "olv-pl-entry";
    function mountPlEntry() {
      try {
        var menu = null;
        var items = document.querySelectorAll(".tp-el-menu-item");
        for (var i = 0; i < items.length; i++) {
          var t = (items[i].innerText || "").trim();
          if (t === "轻音乐" || t === "流行" || t === "全部") { menu = items[i].parentNode; break; }
        }
        if (!menu) { return; }
        var exist = document.getElementById(PL_ENTRY_ID);
        if (exist) {
          if (exist.parentNode === menu) { return; }
          if (exist.parentNode) { exist.parentNode.removeChild(exist); }
        }
        var li = elx("li", "tp-el-menu-item tp-menu-item text-title-m h-fit h-");
        li.id = PL_ENTRY_ID;
        li.appendChild(elx("div", "h-5.5 mb-2 font-medium", "我的播单"));
        li.addEventListener("click", function (ev) {
          ev.stopPropagation();
          ev.preventDefault();
          openPlPanel();
        });
        var ref = null;
        for (var j = 0; j < menu.children.length; j++) {
          if ((menu.children[j].innerText || "").trim() === "轻音乐") { ref = menu.children[j]; break; }
        }
        if (ref && ref.nextSibling) { menu.insertBefore(li, ref.nextSibling); }
        else { menu.appendChild(li); }
      } catch (e) {}
    }

    function pollCmd() {
      fetch(BASE + "/probe/poll", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          if (!j || !j.cmd) return;
          if (j.cmd === "click") {
            report("cmd-click", { at: j.x + "," + j.y, hit: hitAt(j.x, j.y) });
          } else if (j.cmd === "nav") {
            navTo(j.path);
          } else if (j.cmd === "inv") {
            report("cmd-inv", { items: inventory() });
          } else if (j.cmd === "eval") {
            var out;
            try { out = String((new Function("return (" + j.js + ")"))()); }
            catch (e) { out = "ERR " + e; }
            report("cmd-eval", { out: out.slice(0, 600) });
          } else if (j.cmd === "player") {
            // 排障用：直接让本页播放指定 song（结构同 sendWebPlayerControlCmd 的 data）
            olvPlayer(j.data);
            report("cmd-player", { url: (j.data && pickUrl(j.data.song)) || "", ok: true });
          } else if (j.cmd === "player-close") {
            closePlayer();
            report("cmd-player", { closed: true });
          }
        })
        .catch(function () {});
    }
    setInterval(pollCmd, 900);
    setTimeout(pollCmd, 2500);

    setTimeout(function () { report("boot"); }, 1500);
    setInterval(mountNav, 1200);
    setInterval(mountPlEntry, 1200);
    setTimeout(mountPlEntry, 3000);
  })();
  </script>
"""


def injected_index(html: str, api_base: str) -> str:
    script = PATCH_HTML.replace("__API_BASE__", api_base)
    if "<head>" in html:
        return html.replace("<head>", "<head>\n" + script, 1)
    return script + html


# ---------------------------------------------------------------------------
# main.js 业务层补丁
#
# 为什么必须改这里：window.cefViewQuery 是 QCefView 用 Object.defineProperty
# 定义的不可配置属性（configurable:false），既不能赋值也不能 redefine，
# 所以"劫持桥接返回值"这条路走不通，只能在业务代码里改。
#
# 为什么不能直接关掉 offlineMode：实测关掉之后应用会要求真实登录
# （Steam 票据 → 原生 showThirdPartyQuickLoginPanel），服务器已停，必然弹
# "登录态校验失败，请重试"。所以策略是——保留离线态（不登录、照常进主页），
# 只把写信这条链路单独放行。
MAIN_REPLACEMENTS = [
    (
        "配置落进 store 时把接口地址指向本地服务",
        '$=J=>{d.value=J,d.value.appConf.toyApiUrl&&jl(',
        '$=J=>{J.appConf=J.appConf||{};J.appConf.toyApiUrl="__API_BASE__";'
        'd.value=J,d.value.appConf.toyApiUrl&&jl(',
    ),
    (
        "信箱功能总开关（原来写死关闭）",
        'N3=!1,Ss=!1,wa=({onComplete',
        'N3=!0,Ss=!1,wa=({onComplete',
    ),
    (
        "离线态不再锁定桌面入口",
        'e.isOfflineMode&&(l.value.mailWidget!==!1&&(l.value.mailWidget=!1),'
        'l.value.musicWidget!==!1&&(l.value.musicWidget=!1))',
        'l.value.mailWidget=!0,l.value.musicWidget=!0',
    ),
    (
        "设置初始化时强制打开两个入口（原生里被持久化成 false 了）",
        'l.value={...l.value,...p}};let c=',
        'c={...p},l.value={...l.value,...p,mailWidget:!0,musicWidget:!0}};let c=',
    ),
    (
        "离线态不再短路请求拦截器",
        'if(t.isOfflineMode)throw new Ol(e)',
        'if(!1)throw new Ol(e)',
    ),
    (
        "写信按钮不再隐藏",
        '"hide-write":o(p)||!o(N3)',
        '"hide-write":!1',
    ),
    (
        "信箱列表不再因离线跳过加载",
        'He(()=>{p.value||d.fetchMailList(!0)})',
        'He(()=>{d.fetchMailList(!0)})',
    ),
    (
        "离线会话建立后，显式把两个桌面入口推给原生并打开",
        'if(await h(M,Cn,!1)){await A.load();return}',
        'if(await h(M,Cn,!1)){await Promise.resolve()'
        '.then(()=>Fm({mailWidget:!0,musicWidget:!0})).catch(()=>null);'
        'await Promise.allSettled(['
        'Promise.resolve().then(()=>Z.toggleLetterEntry({new_status:!0})),'
        'Promise.resolve().then(()=>Z.toggleMusicEntry({new_status:!0}))'
        ']);await A.load();return}',
    ),
    (
        "离线态也启动新信轮询（红点靠它）",
        's.isOfflineMode||(s.appMode===Se.PRO?Lt().proRestoreFromApi():'
        's.appMode===Se.LITE&&(Lt().liteStartPoll(),uo().startPolling()))',
        's.appMode===Se.PRO?Lt().proRestoreFromApi():s.appMode===Se.LITE&&'
        '(s.isOfflineMode?uo().startPolling():(Lt().liteStartPoll(),uo().startPolling()))',
    ),
    (
        # WatermarkOverlay 用 canvas 把 uid 以 5% 白、-30° 铺满整个界面。
        # 本地假账号的 uid 是 10000，这层水印没有任何意义，直接让它的 backgroundImage
        # 恒为 "none"（不再画 canvas，也不再多一份 toDataURL 开销）。
        "去掉斜向 UID 水印（canvas 铺满那层）",
        'if(!t.uid)return"none";const i=document.createElement("canvas")',
        'if(!0)return"none";const i=document.createElement("canvas")',
    ),
    (
        # 右下角那个 "UID: 10000" 是 UserInfo 组件画的（fixed bottom-0 right-0 z-[60]）。
        # 整个组件的根节点换成一个空注释节点，DOM 里连这个 div 都不再出现。
        "去掉右下角 UID 文本",
        '(r(),_("div",n0,[n("div",a0,[o(t)?(r(),_("span",l0,"UID: "+v(o(t)),1)):Y("",!0)])]))',
        'Y("",!0)',
    ),
    (
        # 离线歌单原本只能来自原生（songlist.dat 的 AES 信封）。这里改成优先向本地
        # 假服务要 /catalog（= media 目录扫描结果），拿不到再退回原生。
        # 播放器本身就是 <video src=videoUrl>，所以本地 mp4 能直接播。
        "离线曲库改为本地 /catalog（可导入外部曲目）",
        'Xm=()=>We({action:"getOfflineSongList",data:{}})',
        'Xm=()=>fetch("__API_BASE__/catalog",{cache:"no-store"})'
        '.then(r=>r.json()).catch(()=>We({action:"getOfflineSongList",data:{}}))',
    ),
    (
        # 曲库列表在 Lite 下还会再叠一层"是否已下载"的过滤（下载管理器只认自己经手过
        # 的 id）。本地导入的曲子没走过那条下载链路，会被滤成空列表 → 显示"暂不可用"。
        "曲库不再要求「已下载」才显示",
        'Ce=j(()=>w.value?oe.getSongsByStyle(R.value).filter(q=>f.isDownloaded(q.id))'
        ':Q.value?te.value:N.value)',
        'Ce=j(()=>w.value?oe.getSongsByStyle(R.value):Q.value?te.value:N.value)',
    ),
    (
        "曲库左侧风格栏不再要求「有已下载曲目」",
        'D.value=oe.musicStyles.filter(me=>oe.getSongsByStyle(me.type).some(Be=>f.isDownloaded(Be.id)))',
        'D.value=oe.musicStyles.filter(me=>oe.getSongsByStyle(me.type).length>0)',
    ),
    (
        # 离线态会把整行的操作全部隐藏（hide-actions），包括悬停才出现的那颗播放键。
        # 曲库既然能列出本地曲目，就没必要再隐藏操作。
        "曲库行内操作（播放键）不再因离线隐藏",
        '"hide-actions":o(w)',
        '"hide-actions":!1',
    ),
    (
        # 下载管理器里没有记录的曲目，一律当作「已在本地」。
        # 本地导入的曲子本来就在硬盘上（由假服务直接提供），不该再要求"下载"。
        # 这同时修好两处：列表里显示的是播放键而不是下载键；Lite 的播放前置校验。
        "没有下载记录的曲目视为已就绪",
        ',u=$=>{const L=e.get($);return(L==null?void 0:L.state)==="completed"}',
        ',u=$=>{const L=e.get($);return!L||L.state==="completed"}',
    ),
    (
        # 行内那颗按钮在 idle/failed/cancelled 时会变成「下载」，点了只会建下载任务。
        # 本地曲库的曲子本来就在盘上，这里恒按「可播放」处理，按钮才会是播放键。
        "曲库行内按钮恒为播放（不再走下载）",
        'u=j(()=>a.downloadState==="idle"||a.downloadState==="failed"||a.downloadState==="cancelled")',
        'u=j(()=>!1)',
    ),
    (
        # 播放命令只走本地播放器，不再转发原生。
        # 原生播放器已死，sendWebPlayerControlCmd 发过去唯一的"效果"是
        # LiteUIController 立即隐藏原生壁纸窗口（露出 Windows 系统壁纸），
        # 而我们的 mpv 要 0.3~0.5s 后才 attach 上来 —— 用户看到的就是
        # 「待机房间场景 → 闪一下系统壁纸 → 进入 MV」。不转发后，
        # 房间场景一直显示到 mpv 就绪，视觉上无缝衔接。
        # 停服前原生命令也只是打一行 JSON 日志，无其他消费方，可安全去掉。
        "播放命令只走本地播放器（不再转发原生，避免壁纸窗口被提前隐藏）",
        'Ct=e=>We({action:"sendWebPlayerControlCmd",data:e})',
        'Ct=e=>{try{window.__olvPlayer&&window.__olvPlayer(e)}catch(olvErr){}'
        'return Promise.resolve({})}',
    ),
]


def patch_main_js(js: str, api_base: str) -> str:
    if 'J.appConf.toyApiUrl="' in js:
        return js  # 已经打过
    out = js
    for desc, old, new in MAIN_REPLACEMENTS:
        cnt = out.count(old)
        if cnt == 0:
            raise SystemExit(
                f"main.js 锚点缺失：{desc}\n原文：{old[:80]}\n"
                "可能是前端版本变了，请重新从 feapp.dat 解包确认。"
            )
        if cnt > 1:
            raise SystemExit(f"main.js 锚点不唯一：{desc}（{cnt} 处），拒绝修改")
        out = out.replace(old, new.replace("__API_BASE__", api_base))
    return out


def build(src: str, out_dir: str, api_base: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "feapp.dat")
    with zipfile.ZipFile(src, "r") as zin:
        names = zin.namelist()
        if "index.html" not in names:
            raise SystemExit("feapp.dat 结构异常：根目录没有 index.html")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info.filename)
                if info.filename == "index.html":
                    data = injected_index(data.decode("utf-8"), api_base).encode("utf-8")
                elif re.match(r"^assets/main-[\w-]+\.js$", info.filename):
                    js = data.decode("utf-8")
                    patched_js = patch_main_js(js, api_base)
                    if patched_js is not js:
                        print(f"  已改业务代码：{info.filename}（offlineMode + toyApiUrl）")
                    data = patched_js.encode("utf-8")
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zout.writestr(zi, data)
    print(f"已生成补丁包：{out}  ({os.path.getsize(out) / 1048576:.1f} MB, {len(names)} 个条目)")
    return out


def apply_patch(src: str, patched: str) -> None:
    if not os.path.exists(src):
        raise SystemExit(f"找不到原文件：{src}")
    # 只在第一次备份，保留最早那份才是真正的原版；重复 apply 不再叠加备份
    cands = sorted(glob.glob(f"{src}.bak.*"))
    if cands:
        print(f"已有原始备份：{cands[0]}（跳过重复备份）")
    else:
        bak = f"{src}.bak.{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(src, bak)
        print(f"已备份原文件：{bak}")
    shutil.copy2(patched, src)
    print(f"已写入：{src}")
    print("提示：请确认应用已完全退出再启动，否则文件可能被占用。")


def restore(src: str) -> None:
    cands = sorted(glob.glob(f"{src}.bak.*"))
    if not cands:
        raise SystemExit("没找到备份文件，无法回滚")
    # 最早的那份是原始文件（后续 apply 不再生成新备份）
    latest = cands[0]
    shutil.copy2(latest, src)
    print(f"已回滚：{latest} -> {src}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="原 feapp.dat 路径")
    ap.add_argument("--out", default=DIST, help="补丁包输出目录")
    ap.add_argument("--api-base", default="http://127.0.0.1:8787")
    ap.add_argument("--apply", action="store_true", help="直接把补丁写回安装目录（会先备份）")
    ap.add_argument("--restore", action="store_true", help="用最近一次备份回滚")
    args = ap.parse_args()

    if args.restore:
        restore(args.src)
        return

    if not os.path.exists(args.src):
        raise SystemExit(f"找不到 {args.src}，用 --src 指定")

    # 始终以原始备份为构建源，否则重复 apply 会把补丁叠上去（注入两遍脚本）
    origin = args.src
    cands = sorted(glob.glob(f"{args.src}.bak.*"))
    if cands:
        origin = cands[0]
        print(f"以原始备份为源：{origin}")

    patched = build(origin, args.out, args.api_base)
    if args.apply:
        apply_patch(args.src, patched)
    else:
        print("未写入安装目录。确认无误后加 --apply 再执行一次即可。")


if __name__ == "__main__":
    main()
