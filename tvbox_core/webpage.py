# -*- coding: utf-8 -*-
"""管理后台页面(单文件 HTML/CSS/JS, 无前端依赖)。

顶部 Tab 分区: 总览(构建控制+日志) / 添加源 / 监测报告 / 源与名单。
只放 PAGE 常量, 逻辑都在 server.py 的 API 里。

页面底部带版本号(本文件 mtime, 进程启动时固化): 改了页面没重启进程时,
用户可凭版本号判断当前看到的是不是最新页面。
"""
import datetime
import os

# ---------------------------------------------------------------- 页面
# 版本号取本文件修改时间, 模块加载时(即进程启动时)固化
_PAGE_VER = datetime.datetime.fromtimestamp(
    os.path.getmtime(__file__)).strftime('%m-%d %H:%M')
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📺</text></svg>">
<title>TVBox 源管理</title>
<style>
*{box-sizing:border-box}
body{margin:0;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft Yahei",sans-serif;background:#f0f2f5;color:#1f2329;min-height:100vh}
.wrap{max-width:880px;margin:0 auto;padding:0 18px 60px}

/* 顶部渐变横幅 */
.hero{background:linear-gradient(135deg,#1a73e8 0%,#6c4cf1 100%);color:#fff;padding:26px 24px 22px;border-radius:0 0 18px 18px;margin:0 -18px 18px;box-shadow:0 4px 18px rgba(26,115,232,.25)}
.hero h1{font-size:22px;margin:0 0 6px;font-weight:700;letter-spacing:.3px}
.hero .sub{color:rgba(255,255,255,.88);font-size:13px;margin:0}
.hstate{display:inline-block;width:8px;height:8px;border-radius:50%;background:rgba(255,255,255,.4);margin-left:6px;vertical-align:middle}
.hstate.ok{background:#22c55e;box-shadow:0 0 6px #22c55e}
.hstate.err{background:#ef4444;box-shadow:0 0 6px #ef4444}
.hstate.busy{background:#f59e0b;animation:pulse 1s infinite}

/* Tab 导航(吸顶, 窄屏可横滑) */
.tabs{position:sticky;top:0;z-index:20;display:flex;gap:8px;padding:12px 0 10px;margin-bottom:4px;overflow-x:auto;background:linear-gradient(#f0f2f5 85%,rgba(240,242,245,.6));scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{white-space:nowrap;flex:0 0 auto;background:#fff;color:#5a6472;border:1px solid #e9ebee;border-radius:999px;padding:8px 18px;font-size:14px;font-weight:500;cursor:pointer;margin:0;box-shadow:none;transition:color .15s,border-color .15s,background .15s,box-shadow .15s}
.tab:hover{color:#1a73e8;border-color:#c7d7f5}
.tab.active{background:linear-gradient(135deg,#1a73e8,#6c4cf1);color:#fff;border-color:transparent;box-shadow:0 2px 8px rgba(26,115,232,.25)}
.tab .dot{display:none;width:7px;height:7px;border-radius:50%;background:#f59e0b;margin-left:6px;vertical-align:middle}
.tab.busy .dot{display:inline-block;animation:pulse 1s infinite}
.tab.flash{animation:tabflash 1.2s ease 2}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes tabflash{0%,100%{box-shadow:0 0 0 0 rgba(34,197,94,0)}50%{box-shadow:0 0 0 5px rgba(34,197,94,.4)}}
.tbadge{display:inline-block;min-width:18px;padding:0 6px;border-radius:999px;background:#fee2e2;color:#dc2626;font-size:11px;line-height:18px;margin-left:6px}
.tbadge.warn{background:#fef3c7;color:#b45309}
.tab.active .tbadge{background:rgba(255,255,255,.25);color:#fff}

/* 面板切换 */
.panel{display:none}
.panel.active{display:block;animation:fadein .25s ease}
@keyframes fadein{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}

.card{background:#fff;border:1px solid #e9ebee;border-radius:14px;padding:20px;margin-bottom:18px;box-shadow:0 1px 3px rgba(16,24,40,.05)}
.card h2{font-size:16px;margin:0 0 14px;font-weight:600;color:#1f2329;display:flex;align-items:center}
.card h2::before{content:"";display:inline-block;width:4px;height:16px;background:linear-gradient(180deg,#1a73e8,#6c4cf1);border-radius:2px;margin-right:8px}

label{display:block;font-size:13px;color:#5a6472;margin:12px 0 5px}
input[type=text],input[type=password],textarea{width:100%;padding:10px 12px;border:1px solid #d7dbe0;border-radius:8px;font-size:14px;font-family:inherit;background:#fff;transition:border-color .15s,box-shadow .15s}
input[type=text]:focus,input[type=password]:focus,textarea:focus{outline:none;border-color:#1a73e8;box-shadow:0 0 0 3px rgba(26,115,232,.12)}
textarea{min-height:150px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;line-height:1.5}
input[type=file]{width:100%;padding:9px 10px;font-size:13px;color:#5a6472;border:1px dashed #c9ced6;border-radius:8px;background:#fafbfc}
input[type=file]::file-selector-button{background:#1a73e8;color:#fff;border:0;border-radius:6px;padding:6px 14px;margin-right:10px;font-size:13px;cursor:pointer}

button{background:#1a73e8;color:#fff;border:0;border-radius:8px;padding:9px 18px;font-size:14px;font-weight:500;cursor:pointer;margin:12px 8px 0 0;transition:background .15s,transform .05s,filter .15s;box-shadow:0 1px 2px rgba(16,24,40,.1)}
button:hover{background:#1765cc}
button:active{transform:translateY(1px)}
button.pri{background:linear-gradient(135deg,#1a73e8,#6c4cf1);box-shadow:0 2px 8px rgba(26,115,232,.25)}
button.pri:hover{filter:brightness(1.07);background:linear-gradient(135deg,#1a73e8,#6c4cf1)}
button.sec{background:#fff;color:#3c4453;border:1px solid #d7dbe0;box-shadow:none}
button.sec:hover{background:#f5f7fa;border-color:#c3c9d1}
button:disabled{opacity:.5;cursor:not-allowed}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.muted{color:#8a93a1;font-size:12.5px}
.ok{color:#16a34a}.err{color:#dc2626}.warn{color:#d97706}

/* 状态数据块 */
#stat{background:#f7f8fa;border:1px solid #edf0f3;border-radius:10px;padding:14px;color:#3c4453;font-size:14px}
.chips{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.chip{background:#fff;border:1px solid #e9ebee;border-radius:10px;padding:9px 16px;min-width:92px;text-align:center;box-shadow:0 1px 2px rgba(16,24,40,.04)}
.chip b{display:block;font-size:21px;color:#1a73e8;line-height:1.25;font-weight:700}
.chip b.warn{color:#d97706}
.chip .lbl{font-size:12px;color:#8a93a1}
.meta{color:#5a6472;font-size:13px;line-height:1.9;margin-top:6px}

#msg,#bmsg,#pmsg{margin-top:10px;font-size:13.5px;white-space:pre-wrap}
#fileMsg,#urlMsg,#rawMsg{margin-top:10px;font-size:13.5px;white-space:pre-wrap}

.bar{height:6px;background:#e9ebee;border-radius:4px;overflow:hidden;margin-top:12px}
.bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#1a73e8,#6c4cf1);transition:width .4s;border-radius:4px}

table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #f0f2f5}
th{color:#8a93a1;font-weight:600;font-size:12px;letter-spacing:.3px}
tr:hover td{background:#f8fafc}
code{background:#eef1f5;padding:2px 6px;border-radius:5px;font-size:12px;color:#c03b6b}
#list{max-height:244px;overflow:auto;border:1px solid #f0f2f5;border-radius:8px}
#list table th{position:sticky;top:0;background:#fff;z-index:1;box-shadow:0 1px 0 #f0f2f5}
/* 明细卡片: 默认只露约 5 行, 超出内部滚动(表头吸顶) */
.scroll5{max-height:244px;overflow:auto;border:1px solid #f0f2f5;border-radius:8px}
.scroll5 table th{position:sticky;top:0;background:#fff;z-index:1;box-shadow:0 1px 0 #f0f2f5}

/* 终端风日志 */
#log{max-height:300px;overflow:auto;white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12px;background:#0f1720;color:#cbd5e1;border-radius:10px;padding:14px;line-height:1.6}

/* 长内容省略号截断 + 悬停/点按显示完整内容(类 Element Plus show-overflow-tooltip) */
td.ellcell{max-width:300px}
.ell{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.ell.blk{flex:1 1 100%}
#tip{position:fixed;z-index:9999;display:none;max-width:min(480px,92vw);background:#1f2329;color:#fff;
     font-size:12px;line-height:1.6;padding:6px 10px;border-radius:6px;pointer-events:none;
     box-shadow:0 4px 16px rgba(0,0,0,.3);word-break:break-all;white-space:pre-wrap}

/* 页面底部版本号(进程启动时固化, 用于判断是否重启生效) */
.pver{max-width:880px;margin:0 auto;padding:2px 18px 26px;text-align:center;color:#b6bdc8;font-size:11px}
/* 通知状态里的 chatid 可能较长, 窄屏允许任意处折行 */
#notifyStat{overflow-wrap:anywhere}

/* ============ 手机端适配(<=640px) ============ */
@media (max-width:640px){
  .wrap{padding:0 12px 40px}
  .hero{margin:0 -12px 14px;padding:20px 16px 16px;border-radius:0 0 14px 14px}
  .hero h1{font-size:19px}
  .tabs{padding:10px 0 8px}
  .tab{padding:7px 14px;font-size:13px}
  .card{padding:14px;margin-bottom:14px;border-radius:12px}
  .card h2{font-size:15px}
  #stat{padding:12px}
  .chip{flex:1 1 44%;min-width:0;padding:8px 10px}
  .chip b{font-size:18px}
  /* 主操作按钮两列排 */
  #panel-overview .row button{flex:1 1 44%;margin-right:0}
  /* 添加源按钮通栏 */
  #panel-add .row button{width:100%;margin-right:0}
  textarea{min-height:120px}
  .bl-search{max-width:none!important;width:100%}
  /* 表格 -> 卡片竖排: 列名做前缀, 行变卡片(表/tbody/行/单元格都转 block, 避免匿名表格盒) */
  table.resp{display:block;width:100%}
  table.resp tbody{display:block;width:100%}
  table.resp thead{display:none}
  table.resp tr.hr{display:none}
  table.resp tr{display:block;border:1px solid #eef0f3;border-radius:10px;padding:4px 10px;margin-bottom:8px;background:#fff}
  table.resp tr:last-child{margin-bottom:0}
  table.resp tr:hover td{background:transparent}
  table.resp td{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
               border:none;border-bottom:1px dashed #f0f2f5;padding:7px 0;font-size:13.5px;
               overflow-wrap:anywhere;text-align:right}
  table.resp tr td:last-child{border-bottom:none}
  table.resp td::before{content:attr(data-l);color:#8a93a1;font-size:12px;font-weight:600;
                        flex:0 0 auto;text-align:left}
  table.resp td button{margin:0}
  table.resp .cell{display:block;overflow-wrap:anywhere}
  /* 省略号单元格: 手机端不限制列宽, 内容弹性收缩 */
  table.resp td.ellcell{max-width:none}
  table.resp td .ell{flex:1 1 auto;min-width:0}
  /* 兜底: 手机端不出现横向滚动条(真有内容超宽就裁剪, 完整内容靠 tooltip 查看) */
  #list,.scroll5{overflow-x:hidden}
}
</style></head><body>

<div class="wrap" id="main">
  <div class="hero">
    <h1>TVBox 源管理</h1>
    <div class="sub">添加源后点「重新构建」, 构建完成 TVBox 才会生效<span class="hstate" id="hstate"></span></div>
  </div>

  <nav class="tabs">
    <button class="tab active" data-tab="overview" onclick="switchTab('overview')">总览<span class="dot"></span></button>
    <button class="tab" data-tab="add" onclick="switchTab('add')">添加源</button>
    <button class="tab" data-tab="monitor" onclick="switchTab('monitor')">监测报告<span class="tbadge warn" id="tabMonCnt"></span></button>
    <button class="tab" data-tab="manage" onclick="switchTab('manage')">源与名单<span class="tbadge" id="tabBlCnt"></span></button>
  </nav>

  <!-- ============ 总览: 构建控制 + 日志 ============ -->
  <section class="panel active" id="panel-overview">
    <div class="card">
      <h2>状态</h2>
      <div id="stat" class="muted">加载中…</div>
      <div class="bar"><i id="pb"></i></div>
      <div class="row">
        <button class="pri" onclick="rebuild(false)">重新构建</button>
        <button class="sec" onclick="rebuild(true)">构建并推送</button>
        <button class="sec" onclick="doPush()">单独推送</button>
        <button class="sec" onclick="loadStatus()">刷新</button>
      </div>
      <div id="bmsg"></div>
      <div id="pmsg" class="muted" style="margin-top:8px"></div>
    </div>
    <div class="card">
      <h2>企业微信通知</h2>
      <div id="notifyStat" class="muted">加载中…</div>
      <div class="row"><button class="sec" onclick="testNotify()">发送测试消息</button></div>
      <div id="nmsg"></div>
    </div>
    <div class="card">
      <h2>构建日志</h2>
      <div id="log" class="muted" style="max-height:300px;overflow:auto;white-space:pre-wrap;font-family:ui-monospace,Consolas,monospace;font-size:12px"></div>
    </div>
  </section>

  <!-- ============ 添加源 ============ -->
  <section class="panel" id="panel-add">
    <div class="card">
      <h2>方式一：上传 JSON 文件</h2>
      <input type="file" id="file" accept=".json,application/json">
      <input type="text" id="fname" placeholder="保存文件名(可选, 如 mysource.json)">
      <div class="row"><button onclick="uploadFile()">上传并保存</button></div>
      <div id="fileMsg"></div>
      <div class="muted" style="margin-top:6px">同名文件会自动备份到 sources_backup/</div>
    </div>
    <div class="card">
      <h2>方式二：用远程 URL 抓取</h2>
      <input type="text" id="url" placeholder="https://example.com/tvbox.json">
      <input type="text" id="urlname" placeholder="保存文件名(可选)">
      <div class="row"><button onclick="addUrl()">抓取并保存</button></div>
      <div id="urlMsg"></div>
      <div class="muted" style="margin-top:6px">
        提示：cnb.cool 要用 <code>/-/git/raw/</code> 这种真实文件地址, <code>/-/blob/</code> 返回的是网页
      </div>
    </div>
    <div class="card">
      <h2>方式三：直接粘贴内容</h2>
      <textarea id="raw" placeholder="把 TVBox 源 JSON 内容粘到这里"></textarea>
      <input type="text" id="rawname" placeholder="保存文件名(可选)">
      <div class="row"><button onclick="addRaw()">保存</button></div>
      <div id="rawMsg"></div>
    </div>
  </section>

  <!-- ============ 监测报告 ============ -->
  <section class="panel" id="panel-monitor">
    <div class="card">
      <h2>不稳定源 ⚠（近期连续失败后恢复）</h2>
      <div id="unstable" class="scroll5"><span class="muted">加载中…</span></div>
      <div class="muted" style="margin-top:6px">"时好时坏"的源：本次探测恢复了，但此前已连续失败 3~4 次。产物里 name 带 <code>[⚠]</code>，客户端会忽略该后缀。</div>
    </div>
    <div class="card">
      <h2>deep 失效源（返回 200 但内容无效）</h2>
      <div id="invalid" class="scroll5"><span class="muted">加载中…</span></div>
      <div class="muted" style="margin-top:6px">浅层探测判活、deep 内容验证判失效的源（空 list / 错误页 / 登录页）。如果里面有你能正常访问的源，可能是误判。</div>
    </div>
    <div class="card">
      <h2>源文件贡献度</h2>
      <div id="srcstat" class="scroll5"><span class="muted">加载中…</span></div>
      <div class="muted" style="margin-top:6px">每个源文件贡献多少存活/失效站点（失效最多在前）。失效远大于存活的文件可以考虑清理。<span id="srccnt"></span></div>
    </div>
    <div class="card">
      <h2>远程源健康 <span class="muted" id="fdCnt"></span></h2>
      <div id="fetchdead" class="scroll5"><span class="muted">加载中…</span></div>
      <div class="muted" style="margin-top:6px">dc.txt 里连续拉取失败已达阈值的远程源: 后续构建不再抓取(仍用本地缓存), 建议尽快从 dc.txt 移除, 清理依据见 report.md。</div>
    </div>
  </section>

  <!-- ============ 源与名单 ============ -->
  <section class="panel" id="panel-manage">
    <div class="card">
      <h2>当前源文件 <span class="muted" id="cnt"></span></h2>
      <div id="list" class="muted">加载中…</div>
    </div>
    <div class="card">
      <h2>失败黑名单 <span class="muted" id="blcnt"></span></h2>
      <div class="row" style="margin:0 0 10px;align-items:center">
        <button class="sec" onclick="releaseAllBl()">一键全部放出</button>
        <span class="muted" style="font-size:12px">已达阈值的源默认 <span id="blRetryDays">7</span> 天后自动重探, 不想等就全部放出, 下次构建立即重新探测</span>
      </div>
      <div id="bllist" class="muted">加载中…</div>
      <div id="blmsg"></div>
      <div class="muted" style="margin-top:6px">同一 URL 连续失败达阈值会自动加入; 点「释放」可从黑名单移除, 下次构建恢复。</div>
    </div>
    <div class="card">
      <h2>源搜索与禁用</h2>
      <div class="row" style="margin:0 0 8px;align-items:center">
        <input type="text" id="dsearch" placeholder="按 name/api/ext 搜索当前产物中的源" style="flex:1;min-width:200px;margin:0" onkeydown="if(event.key==='Enter')searchDisable()">
        <button onclick="searchDisable()" style="margin:0 0 0 8px">搜索</button>
      </div>
      <div id="dresult" class="muted"></div>
      <div class="muted" style="margin:10px 0 4px">已禁用清单 <span id="dcnt"></span></div>
      <div id="dlist" class="muted">加载中…</div>
      <div class="muted" style="margin-top:6px">禁用后下次构建自动跳过该源; 点「启用」可恢复。</div>
    </div>
  </section>
</div>

<script>
const $=id=>document.getElementById(id);
let curTab='overview', _wasRunning=false;

function switchTab(name){
  curTab=name;
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.getAttribute('data-tab')===name));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active', p.id==='panel-'+name));
  try{ localStorage.setItem('tvbox_tab', name); }catch(e){}
  // 同步 URL hash: 非总览写成 #tab(可分享/刷新保持), 总览清掉;
  // replaceState 不触发 hashchange、不滚动、不污染历史
  try{
    history.replaceState(null,'', name==='overview'
      ? location.pathname+location.search : '#'+name);
  }catch(e){}
  if(name==='overview'){
    document.title='TVBox 源管理';
    const ov=document.querySelector('.tab[data-tab="overview"]');
    if(ov) ov.classList.remove('flash');
  }
  window.scrollTo(0,0);
}

async function boot(){
  let last='overview';
  try{ last=localStorage.getItem('tvbox_tab')||'overview'; }catch(e){}
  // URL hash 优先(深链接, 如 #monitor 直达监测报告)
  const h=(location.hash||'').replace('#','');
  if(['overview','add','monitor','manage'].includes(h)) last=h;
  switchTab(last);
  loadStatus(); loadList(); loadBlacklist(); loadDisableList(); loadReport();
  loadNotify();
}

function msg(el,text,cls){ const e=$(el); if(!e) return; e.innerHTML='<span class="'+(cls||'')+'">'+text+'</span>'; }

function renderLists(d){
  const L=d.lists||{};
  const u=L.unstable||[];
  $('unstable').innerHTML = u.length
    ? '<table class="resp"><tr class="hr"><th>站点</th><th>连续失败</th></tr>'+
      u.map(x=>'<tr><td data-l="站点" class="ellcell"><span class="ell" data-tip="'+esc(x.name||'')+'">'+esc(x.name||'')+'</span></td><td data-l="连续失败"><span class="warn">⚠ '+esc(x.fail_count)+' 次</span></td></tr>').join('')+'</table>'
    : '<span class="muted">无(没有"时好时坏"的源)</span>';
  const iv=L.invalid_content||[];
  $('invalid').innerHTML = iv.length
    ? '<table class="resp"><tr class="hr"><th>站点</th><th>url</th></tr>'+
      iv.map(x=>'<tr><td data-l="站点" class="ellcell"><span class="ell" data-tip="'+esc(x.name||'')+'">'+esc(x.name||'')+'</span></td><td data-l="url" class="ellcell"><span class="ell" data-tip="'+esc(String(x.url||''))+'"><code>'+esc(String(x.url||''))+'</code></span></td></tr>').join('')+'</table>'
    : '<span class="muted">无(没有"200 但内容无效"的源)</span>';
  // 远程源健康(dc.txt 已达拉取阈值的条目)
  const fd=L.fetch_dead||[];
  const fcEl=$('fdCnt');
  if(fcEl) fcEl.textContent = fd.length ? '('+(L.fetch_dead_count||fd.length)+')' : '';
  $('fetchdead').innerHTML = fd.length
    ? '<table class="resp"><tr class="hr"><th>远程源</th><th>失败次数</th><th>最后失败</th></tr>'+
      fd.map(x=>'<tr><td data-l="远程源" class="ellcell"><span class="ell" data-tip="'+esc(x.name||'')+'">'+esc(x.name||'')+'</span>'+
        '<span class="ell blk muted" style="font-size:11px" data-tip="'+esc(String(x.url||''))+'"><code>'+esc(String(x.url||''))+'</code></span></td>'+
        '<td data-l="失败次数"><span class="err">'+esc(x.fail_count)+'</span></td>'+
        '<td data-l="最后失败">'+esc(x.last_fail||'')+'</td></tr>').join('')+'</table>'
    : '<span class="muted">无(没有达到拉取阈值的远程源)</span>';
  // 监测 Tab 角标: 不稳定 + deep 失效 + 远程源待清理 总数
  const n=u.length+iv.length+(L.fetch_dead_count||0);
  const badge=$('tabMonCnt');
  if(badge){ badge.textContent = n? String(n) : ''; badge.style.display = n? '' : 'none'; }
}

async function loadReport(){
  const r=await api('/api/report');
  if(!r.ok||!r.j){ $('srcstat').innerHTML='<span class="muted">(暂无报告, 构建后生成)</span>'; return; }
  const rows=r.j.sources||[];
  if(!rows.length){ $('srcstat').innerHTML='<span class="muted">(无数据, 下次构建后生成)</span>'; return; }
  let h='<table class="resp"><tr class="hr"><th>源文件</th><th>点播</th><th>存活</th><th>失效</th><th>其中拉黑</th><th>客户端依赖</th><th>直播</th><th>解析</th></tr>';
  rows.forEach(x=>{
    h+='<tr><td data-l="源文件" class="ellcell"><span class="ell" data-tip="'+esc(x.src)+'"><code>'+esc(x.src)+'</code></span></td><td data-l="点播">'+x.sites+'</td>'+
       '<td data-l="存活"><span class="ok">'+x.sites_alive+'</span></td>'+
       '<td data-l="失效"><span class="'+(x.sites_dead?'err':'muted')+'">'+x.sites_dead+'</span></td>'+
       '<td data-l="其中拉黑">'+x.sites_blacklisted+'</td><td data-l="客户端依赖">'+x.sites_client+'</td>'+
       '<td data-l="直播">'+x.lives+'</td><td data-l="解析">'+x.parses+'</td></tr>';
  });
  $('srcstat').innerHTML=h+'</table>';
  $('srccnt').textContent='（共 '+rows.length+' 个源文件, 滚动查看全部）';
}

async function loadStatus(){
  const r=await api('/api/status');
  if(!r.ok)return;
  const d=r.j;
  const s=d.summary||{};
  renderLists(d);
  let durTxt='';
  if(d.duration!==null&&d.duration!==undefined){
    durTxt = d.running
      ? '<div class="meta">本次构建已用: <b>'+d.duration.toFixed(1)+'</b> 秒</div>'
      : '<div class="meta">上次构建耗时: <b>'+d.duration.toFixed(1)+'</b> 秒</div>';
  }
  const chip=(lbl,val,cls)=>'<div class="chip"><b class="'+(cls||'')+'">'+val+'</b><span class="lbl">'+lbl+'</span></div>';
  $('stat').innerHTML =
    '<div class="chips">'+
      chip('点播源', d.counts.sites||0)+
      chip('直播源', d.counts.lives||0)+
      chip('解析源', d.counts.parses||0)+
      (d.summary? chip('不稳定 ⚠', s.sites_unstable||0, s.sites_unstable?'warn':'') : '')+
    '</div>'+
    (d.summary
      ? '<div class="meta">上次构建: 存活 <span class="ok">'+s.sites_alive+'</span> / 失效 <span class="err">'+s.sites_dead+
        '</span> · 恢复 <span class="ok">'+s.recovered_vs_last_run+'</span> · 源文件 '+d.source_count+' 个</div>'
      : '<div class="meta">尚无构建报告</div>') + durTxt;
  // hero 状态灯 + 总览 Tab 构建中脉冲
  const hs=$('hstate');
  if(hs){
    hs.className='hstate'+(d.running?' busy'
      : (d.last_rc===0?' ok':((d.last_rc===null||d.last_rc===undefined)?'':' err')));
  }
  const ov=document.querySelector('.tab[data-tab="overview"]');
  if(!d.running){
    $('pb').style.width='0%';
    $('pb').style.background='#16a34a';
  }
  if(d.running){
    _wasRunning=true;
    if(ov) ov.classList.add('busy');
    const nm={fetch:'远程拉取',merge:'合并去重',static:'静态字段探测',probe:'存活探测',output:'生成产物'};
    const stg=d.stage? (nm[d.stage]||d.stage):'准备中';
    const pct=d.percent||0;
    $('pb').style.width=pct+'%';
    $('pb').style.background='#d97706';
    $('bmsg').innerHTML='<span class="warn">构建进行中：'+stg+
      (d.total? ('  '+d.done+'/'+d.total+'  '+pct+'%'):'')+'</span>';
    setTimeout(loadStatus,1000);
  }else{
    if(ov) ov.classList.remove('busy');
    if(_wasRunning){   // 构建刚结束: 刷新明细, 不在总览则闪 Tab + 改标题提醒
      _wasRunning=false;
      if(curTab!=='overview'&&ov){ ov.classList.add('flash'); setTimeout(()=>ov.classList.remove('flash'),2600); }
      document.title='✅ 构建完成 - TVBox 源管理';
      loadReport();
    }
    $('bmsg').innerHTML = d.last_rc!==null&&d.last_rc!==undefined
      ? (d.last_rc===0? '<span class="ok">上次构建成功</span>'
                      : '<span class="err">上次构建失败(rc='+d.last_rc+'), 可能触发了安全阀</span>') : '';
    if(d.log) $('log').textContent=d.log.slice(-4000);
  }
  if(d.push_running){
    $('pmsg').innerHTML='<span class="warn">推送进行中…</span>';
    setTimeout(loadStatus,1500);
  }else if(d.push_rc!==null&&d.push_rc!==undefined){
    $('pmsg').innerHTML = d.push_rc===0
      ? '<span class="ok">推送成功</span>'
      : '<span class="err">推送失败(rc='+d.push_rc+')</span>';
    if(d.push_log) $('log').textContent=d.push_log.slice(-4000);
  }else{
    $('pmsg').innerHTML='';
  }
}

async function api(path, body){
  const opt={headers:{}};
  if(body!==undefined){opt.method='POST';opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body);}
  const r=await fetch(path,opt);
  let j={};try{j=await r.json();}catch(e){}
  return {ok:r.ok, j};
}

async function loadList(){
  const r=await api('/api/sources');
  if(!r.ok)return;
  const a=r.j.sources||[];
  $('cnt').textContent='('+a.length+')';
  if(!a.length){$('list').textContent='(无)';return;}
  let h='<table class="resp"><tr class="hr"><th>文件名</th><th>大小</th><th>修改时间</th><th>黑名单</th></tr>';
  a.forEach(s=>{
    let bl='<span class="muted">-</span>';
    if(s.blocked){bl='<span class="err">已拉黑 '+s.blocked+'</span> <button class="sec bl-relfile" data-name="'+esc(s.name)+'">取消拉黑</button>';}
    else if(s.pending){bl='<span class="warn">累计中 '+s.pending+'</span>';}
    h+='<tr><td data-l="文件" class="ellcell"><span class="ell" data-tip="'+esc(s.name)+'"><code>'+esc(s.name)+'</code></span></td><td data-l="大小">'+(s.size/1024).toFixed(1)+' KB</td><td data-l="修改时间">'+s.mtime+'</td><td data-l="黑名单">'+bl+'</td></tr>';
  });
  $('list').innerHTML=h+'</table>';
  $('list').querySelectorAll('.bl-relfile').forEach(b=>{
    b.onclick=()=>releaseFile(b.getAttribute('data-name'));
  });
}

async function releaseFile(name){
  if(!confirm('从黑名单中释放「'+name+'」涉及的所有源?')) return;
  const r=await api('/api/blacklist/release_file',{name:name});
  if(r.ok&&r.j.ok){ loadList(); loadBlacklist(); }
  else { alert((r.j&&r.j.message)||'释放失败'); }
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

// 黑名单"自动重探"倒计时单元格: null=未达阈值不显示, 0=已到期下次构建重探
function retryCell(s){
  const d=s.retry_in_days;
  if(d===null||d===undefined) return '<span class="muted">-</span>';
  if(d<=0) return '<span class="ok">下次构建</span>';
  return '<span class="warn">'+d+' 天</span>';
}

// ---- 企业微信通知: 绑定状态 + 测试发送 ----
async function loadNotify(){
  const el=$('notifyStat'); if(!el) return;
  const r=await api('/api/notify/status');
  if(!r.ok||!r.j){ el.innerHTML='<span class="muted">(无法读取通知状态)</span>'; return; }
  const d=r.j;
  if(!d.enabled){
    el.innerHTML='通知已在配置中禁用(<code>notify_config.json</code> 的 <code>enabled=false</code>)。';
  }else if(d.bound){
    el.innerHTML='已绑定: <code>'+esc(d.chatid)+'</code> ('+(d.chat_type===2?'群聊':'单聊')+
      '), 构建结果会推送到该会话。';
  }else{
    el.innerHTML='<span class="warn">尚未绑定会话</span> — 在服务器上运行 '+
      '<code>python build.py --notify-capture</code>, 然后去企业微信给机器人发任意消息完成绑定。';
  }
}
async function testNotify(){
  msg('nmsg','发送中…');
  const r=await api('/api/notify/test',{});
  if(r.ok&&r.j.ok) msg('nmsg','✓ 发送成功, 请到企业微信查看','ok');
  else msg('nmsg','✗ '+esc((r.j&&r.j.message)||'发送失败'),'err');
}

async function loadBlacklist(){
  const r=await api('/api/blacklist');
  if(!r.ok)return;
  const th=r.j.threshold;
  const a=(r.j.items||[]).map(x=>({...x,_type:'源存活',_source:'probe'}));
  const fb=(r.j.fetch_items||[]).map(x=>({...x,_type:'远程拉取',_source:'fetch'}));
  $('blcnt').textContent='(源 '+a.length+' · 拉取 '+fb.length+', 阈值 '+th+' 次)';
  blCounts.probe=a.length; blCounts.fetch=fb.length;
  const rd=$('blRetryDays'); if(rd) rd.textContent=String(r.j.retry_days||7);
  const badge=$('tabBlCnt');
  if(badge){ const n=a.length+fb.length; badge.textContent=n?String(n):''; badge.style.display=n?'':'none'; }
  renderBl('bllist', [...a,...fb], th);
}

// 一键全部放出
const blCounts={probe:0,fetch:0};
async function releaseAllBl(){
  const n=blCounts.probe+blCounts.fetch;
  if(!n){ msg('blmsg','黑名单本来就是空的','muted'); return; }
  if(!confirm('确定放出全部 '+n+' 个黑名单源?\n下次构建会重新探测它们(仍可能再次失败)。')) return;
  const r=await api('/api/blacklist/release_all',{source:'all'});
  if(r.ok&&r.j.ok){ msg('blmsg','✓ 已放出 '+r.j.released+' 个源, 下次构建重新探测','ok'); loadBlacklist(); }
  else { msg('blmsg','✗ '+esc((r.j&&r.j.message)||'放出失败'),'err'); }
}

const blState={};

function renderBl(id, arr, th){
  if(!blState[id]) blState[id]={page:1,q:'',size:5};
  const st=blState[id];
  st.arr=arr; st.th=th;
  drawBl(id);
}

function drawBl(id){
  const st=blState[id], el=$(id);
  const arr=st.arr, th=st.th;
  const q=st.q.trim().toLowerCase();
  const filtered = q ? arr.filter(s=>{
    const nm=(s.name||s.url||'').toLowerCase();
    const u=String(s.url||'').toLowerCase();
    const tp=(s._type||'').toLowerCase();
    return nm.includes(q)||u.includes(q)||tp.includes(q);
  }) : arr;
  const total=filtered.length;
  const pages=Math.max(1, Math.ceil(total/st.size));
  if(st.page>pages) st.page=pages;
  if(st.page<1) st.page=1;
  const start=(st.page-1)*st.size;
  const slice=filtered.slice(start, start+st.size);
  let h='<div class="row" style="margin:0 0 8px;align-items:center;flex-wrap:wrap">'+
    '<input type="text" class="bl-search" placeholder="按名称/URL/类型 搜索" value="'+esc(st.q)+'" style="max-width:240px;padding:6px 10px;font-size:13px;margin:0">'+
    '<span class="muted" style="margin:0 4px 0 10px">每页</span>'+
    '<select class="bl-size" style="padding:5px 8px;font-size:13px;border:1px solid #d7dbe0;border-radius:6px;margin:0">'+
      [5,10,20].map(n=>'<option value="'+n+'"'+(st.size==n?' selected':'')+'>'+n+'</option>').join('')+
    '</select>'+
    '<span class="muted" style="margin-left:10px">共 '+total+' 条</span></div>';
  if(!total){ el.innerHTML=h+'<span class="muted">(无)</span>'; bindBlEvents(id); return; }
  h+='<table class="resp"><tr class="hr"><th>类型</th><th>源</th><th>失败次数</th><th>最后失败</th><th>自动重探</th><th></th></tr>';
  slice.forEach(s=>{
    const fc=s.fail_count>=th? '<span class="err">'+s.fail_count+'</span>' : s.fail_count;
    const tp=s._type==='源存活'? '<span class="warn">'+esc(s._type)+'</span>' : '<span class="muted">'+esc(s._type)+'</span>';
    h+='<tr><td data-l="类型">'+tp+'</td><td data-l="源" class="ellcell">'+
       '<span class="ell" data-tip="'+esc(s.name||s.url)+'">'+esc(s.name||s.url)+'</span>'+
       '<span class="ell blk muted" style="font-size:11px" data-tip="'+esc(String(s.url))+'">'+esc(String(s.url))+'</span></td>'+
       '<td data-l="失败次数">'+fc+'</td><td data-l="最后失败">'+esc(s.last_fail||'')+'</td>'+
       '<td data-l="自动重探">'+retryCell(s)+'</td>'+
       '<td data-l="操作"><button class="sec bl-rel" data-url="'+esc(s.url)+'" data-src="'+esc(s._source)+'">释放</button></td></tr>';
  });
  h+='</table>';
  if(pages>1){
    h+='<div class="muted" style="margin-top:8px;font-size:12px;display:flex;align-items:center;gap:8px">'+
       '<button class="sec bl-prev"'+(st.page<=1?' disabled':'')+'>上一页</button>'+
       '<span>第 '+st.page+' / '+pages+' 页</span>'+
       '<button class="sec bl-next"'+(st.page>=pages?' disabled':'')+'>下一页</button></div>';
  }
  el.innerHTML=h;
  bindBlEvents(id);
}

function bindBlEvents(id){
  const el=$(id), st=blState[id];
  const sEl=el.querySelector('.bl-search');
  if(sEl) sEl.oninput=()=>{ st.q=sEl.value; st.page=1; drawBl(id); };
  const zEl=el.querySelector('.bl-size');
  if(zEl) zEl.onchange=()=>{ st.size=parseInt(zEl.value)||5; st.page=1; drawBl(id); };
  const pEl=el.querySelector('.bl-prev');
  if(pEl) pEl.onclick=()=>{ st.page--; drawBl(id); };
  const nEl=el.querySelector('.bl-next');
  if(nEl) nEl.onclick=()=>{ st.page++; drawBl(id); };
  el.querySelectorAll('.bl-rel').forEach(b=>{
    b.onclick=()=>releaseBl(b.getAttribute('data-url'), b.getAttribute('data-src'));
  });
}

async function releaseBl(url, source){
  const r=await api('/api/blacklist/release',{url:url, source:source});
  if(r.ok&&r.j.ok){ loadBlacklist(); }
  else { alert(r.j.message||'释放失败'); }
}

async function searchDisable(){
  const q=$('dsearch').value.trim();
  if(!q){$('dresult').innerHTML='<span class="err">请输入关键词</span>';return;}
  const r=await api('/api/disable/search?q='+encodeURIComponent(q));
  if(!r.ok)return;
  const a=r.j.items||[];
  if(!a.length){$('dresult').innerHTML='<span class="muted">无匹配</span>';return;}
  let h='<span class="muted">匹配 '+a.length+' 条</span>';
  h+='<table class="resp" style="margin-top:8px"><tr class="hr"><th>name</th><th>api</th><th></th></tr>';
  a.slice(0,50).forEach(s=>{
    h+='<tr><td data-l="name" class="ellcell"><span class="ell" data-tip="'+esc(s.name)+'">'+esc(s.name)+'</span>'+
       '<span class="ell blk muted" style="font-size:11px" data-tip="key: '+esc(s.key)+'">key: '+esc(s.key)+'</span></td>'+
       '<td data-l="api" class="ellcell"><span class="ell muted" style="font-size:11px" data-tip="'+esc(String(s.api))+'">'+esc(String(s.api))+'</span></td>'+
       '<td data-l="操作"><button class="sec d-add" data-key="'+esc(s.key)+'" data-name="'+esc(s.name)+'" data-api="'+esc(s.api)+'" data-ext="'+esc(s.ext||'')+'">禁用</button></td></tr>';
  });
  h+='</table>';
  if(a.length>50) h+='<div class="muted" style="margin-top:6px">仅显示前 50 条, 请细化关键词</div>';
  $('dresult').innerHTML=h;
  $('dresult').querySelectorAll('.d-add').forEach(b=>{
    b.onclick=()=>addDisable(b.getAttribute('data-key'),b.getAttribute('data-name'),b.getAttribute('data-api'),b.getAttribute('data-ext'));
  });
}

async function loadDisableList(){
  const r=await api('/api/disable/list');
  if(!r.ok)return;
  const a=r.j.items||[];
  $('dcnt').textContent='('+a.length+')';
  if(!a.length){$('dlist').textContent='(无)';return;}
  let h='<table class="resp"><tr class="hr"><th>name</th><th>api</th><th>添加时间</th><th></th></tr>';
  a.forEach(s=>{
    h+='<tr><td data-l="name" class="ellcell"><span class="ell" data-tip="'+esc(s.name||'')+'">'+esc(s.name||'')+'</span></td>'+
       '<td data-l="api" class="ellcell"><span class="ell muted" style="font-size:11px" data-tip="'+esc(String(s.api||''))+'">'+esc(String(s.api||''))+'</span></td>'+
       '<td data-l="添加时间">'+esc(s.added||'')+'</td>'+
       '<td data-l="操作"><button class="sec d-rel" data-key="'+esc(s.key||'')+'" data-api="'+esc(s.api||'')+'">启用</button></td></tr>';
  });
  h+='</table>';
  $('dlist').innerHTML=h;
  $('dlist').querySelectorAll('.d-rel').forEach(b=>{
    b.onclick=()=>removeDisable(b.getAttribute('data-key'),b.getAttribute('data-api'));
  });
}

async function addDisable(key,name,apiUrl,ext){
  if(!confirm('禁用「'+name+'」? 下次构建将跳过该源。')) return;
  try {
    const r=await api('/api/disable/add',{key:key,name:name,api:apiUrl,ext:ext});
    if(r.ok){ loadDisableList(); $('dresult').innerHTML='<span class="ok">'+esc(r.j.message||'已禁用')+'</span>'; }
    else alert((r.j&&r.j.message)||'禁用失败');
  } catch(e) {
    console.error('addDisable error', e);
    alert('禁用出错: '+e.message);
  }
}

async function removeDisable(key,apiUrl){
  const r=await api('/api/disable/remove',{key:key,api:apiUrl});
  if(r.ok){ loadDisableList(); }
  else alert((r.j&&r.j.message)||'启用失败');
}

async function uploadFile(){
  const f=$('file').files[0];
  if(!f){msg('fileMsg','请先选择文件','err');return;}
  if(f.size>8*1024*1024){msg('fileMsg','文件超过 8MB','err');return;}
  const name=($('fname').value.trim()||f.name);
  const txt=await f.text();
  const r=await api('/api/add',{type:'content',filename:name,content:txt});
  handle(r,'fileMsg');
}
async function addUrl(){
  const u=$('url').value.trim();
  if(!u){msg('urlMsg','请填写 URL','err');return;}
  const name=$('urlname').value.trim();
  msg('urlMsg','抓取中…');
  const r=await api('/api/add',{type:'url',url:u,filename:name});
  handle(r,'urlMsg');
}
async function addRaw(){
  const c=$('raw').value;
  if(!c.trim()){msg('rawMsg','请粘贴内容','err');return;}
  const r=await api('/api/add',{type:'content',filename:$('rawname').value.trim(),content:c});
  handle(r,'rawMsg');
}
function handle(r,el){
  if(r.ok&&r.j.ok){ msg(el,'✓ '+esc(r.j.message)+(r.j.counts?('  ('+r.j.counts+')'):''),'ok'); loadList(); }
  else { msg(el,'✗ '+esc(r.j.message||'失败'),'err'); }
}

async function rebuild(push){
  const r=await api('/api/rebuild',{push:!!push});
  if(r.ok&&r.j.started){ loadStatus(); }
  else msg('bmsg','✗ '+(r.j.message||'无法启动构建'),'err');
}

async function doPush(){
  const r=await api('/api/push',{});
  if(r.ok&&r.j.started){ $('pmsg').innerHTML='<span class="warn">推送已开始…</span>'; setTimeout(loadStatus,1500); }
  else $('pmsg').innerHTML='<span class="err">✗ '+(r.j.message||'无法启动推送')+'</span>';
}

// ---- 长内容悬停/点按 tooltip(data-tip; 类 Element Plus show-overflow-tooltip) ----
let _tipEl=null, _tipOn=false;
function _tipShow(text,x,y){
  if(!text)return;
  if(!_tipEl){_tipEl=document.createElement('div');_tipEl.id='tip';document.body.appendChild(_tipEl);}
  _tipEl.textContent=text;_tipEl.style.display='block';_tipOn=true;_tipMove(x,y);
}
function _tipMove(x,y){
  if(!_tipEl)return;
  const r=_tipEl.getBoundingClientRect();
  let left=Math.min(x+14,innerWidth-r.width-8), top=y-r.height-10;
  if(top<8)top=y+20;                       // 上方不够就翻到下方
  _tipEl.style.left=Math.max(8,left)+'px';_tipEl.style.top=top+'px';
}
function _tipHide(){_tipOn=false;if(_tipEl)_tipEl.style.display='none';}
document.addEventListener('mouseover',e=>{
  const t=e.target.closest&&e.target.closest('[data-tip]');
  if(t)_tipShow(t.getAttribute('data-tip'),e.clientX,e.clientY);
});
document.addEventListener('mousemove',e=>{ if(_tipOn)_tipMove(e.clientX,e.clientY); });
document.addEventListener('mouseout',e=>{
  const to=e.relatedTarget;
  if(to&&to.closest&&to.closest('[data-tip]'))return;   // 移到另一个 data-tip 上, 不收起
  if(e.target.closest&&e.target.closest('[data-tip]'))_tipHide();
});
document.addEventListener('scroll',_tipHide,true);      // 容器滚动时收起(捕获阶段)
document.addEventListener('click',e=>{                  // 触屏: 点内容显示, 点别处收起
  const t=e.target.closest&&e.target.closest('[data-tip]');
  if(t)_tipShow(t.getAttribute('data-tip'),e.clientX||innerWidth/2,e.clientY||innerHeight/2);
  else if(_tipOn)_tipHide();
});

window.addEventListener('DOMContentLoaded',()=>{ boot(); });
// 浏览器 前进/后退 或手动改 hash: 跟随切换(replaceState 同步的不会触发这里)
window.addEventListener('hashchange',()=>{
  const h=(location.hash||'').replace('#','');
  if(['overview','add','monitor','manage'].includes(h)&&h!==curTab) switchTab(h);
});
</script>
<div class="pver">TVBox 源管理 · 页面版本 __PAGE_VER__（改页面后需重启 manage.py 进程才会更新）</div>
</body></html>""".replace('__PAGE_VER__', _PAGE_VER)
