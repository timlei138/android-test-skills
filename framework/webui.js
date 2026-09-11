// webui.js —— 从 webui.html 拆出的前端逻辑
// 由 _split_assets.py 一次性拆分生成；之后直接改本文件即可。
// 依赖：/codemirror.bundle.js 必须先于本文件加载（暴露 window.CodeMirrorYaml，历史命名）。

const $ = s => document.querySelector(s);
let cases = [], filter = '', kbCurrent = null, kbFiles = [], currentCaseId = null;
// 统一请求封装：错误提示必须能看懂。
// 1) 后端返回的 {"error": "..."} 只取 error 字段，不再把整坨 JSON 甩给用户；
// 2) 连接被掐断时 fetch 只会抛一句 "Failed to fetch"，换成指向明确的提示。
const api = async (p, o) => {
  let r;
  try {
    r = await fetch(p, o);
  } catch (e) {
    throw new Error('无法连接本地服务（Web UI 进程可能已退出或卡死），请刷新页面；仍不行就重启服务');
  }
  if (!r.ok) {
    const t = await r.text().catch(() => '');
    let msg = '';
    try { const d = JSON.parse(t); msg = d.error || d.message || ''; } catch (_) {}
    throw new Error(msg || t.slice(0, 150) || ('HTTP ' + r.status));
  }
  return r.json();
};

function show(name) {
  ['dashboard','records','scripts','knowledge','vision'].forEach(v => {
    const el = $('#view-'+v);
    if (el) el.style.display = name===v ? '' : 'none';
    const nav = $('#nav-'+v);
    if (nav) nav.className = name===v ? 'active' : '';
  });
  if (name==='dashboard') loadDashboard();
  if (name==='scripts') loadScripts();
  if (name==='knowledge' && !kbFiles.length) loadKnowledgeList();
  if (name==='vision') loadVision();
}
function toggleTheme() {
  const body = document.body, root = document.documentElement;
  const isLight = body.classList.contains('light');
  body.classList.toggle('light', !isLight); body.classList.toggle('dark', isLight);
  root.classList.toggle('light', !isLight); root.classList.toggle('dark', isLight);
  document.querySelectorAll('.theme-toggle').forEach(b => b.textContent = isLight ? '☀️ 亮色' : '🌙 暗色');
  localStorage.setItem('theme', isLight ? 'dark' : 'light');
  // 同步 CodeMirror 配色
  if (typeof kbSyncTheme === 'function') kbSyncTheme();
}
(function initTheme(){
  const saved = localStorage.getItem('theme');
  if (saved === 'dark') {
    document.body.classList.remove('light'); document.body.classList.add('dark');
    document.documentElement.classList.remove('light'); document.documentElement.classList.add('dark');
    document.querySelectorAll('.theme-toggle').forEach(b => b.textContent = '☀️ 亮色');
  }
})();

function badge(r) { return '<span class="badge b-'+(r||'UNKNOWN')+'">'+(r||'UNKNOWN')+'</span>'; }
function filename(p) { if(!p)return''; const i=p.replace(/\\/g,'/').lastIndexOf('/'); return i>=0?p.slice(i+1):p; }
function imgTag(path,title) { return '<div class="shot" title="'+(title||filename(path))+'" data-lb-path="'+encodeURIComponent(path)+'"><img src="/api/file?path='+encodeURIComponent(path)+'" loading="lazy" alt=""><div class="shot-title">'+(title||filename(path))+'</div></div>'; }
function fmtDuration(s) {
  if (s===null||s===undefined||s==='') return '-';
  if (s<1) return Math.round(s*1000)+'ms';
  if (s<60) return s.toFixed(1)+'s';
  const m=Math.floor(s/60), rs=Math.round(s%60); return m+'m '+rs+'s';
}
function category(name) {
  if (!name) return '-';
  const n = name.replace(/\.py$/, '');
  const idx = n.indexOf('_');
  return idx > 0 ? n.slice(0, idx) : n;
}
function escapeHtml(s) { return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c])); }
// started_at / finished_at 形如 "2026-09-03T16:42:44"（或已含空格），列表只显示到分钟
function fmtDateTime(s) {
  if (!s) return '-';
  const t = String(s).replace('T', ' ').trim();
  return t.length >= 16 ? t.slice(0, 16) : t;
}

// ── 等待框：删除记录/脚本等耗时操作期间阻塞交互，结束即消失 ──
function showBusy(text) {
  const b = $('#busy'), t = $('#busy-text');
  if (!b) return;
  if (t) t.textContent = text || '处理中…';
  b.style.display = 'grid';
}
function hideBusy() { const b = $('#busy'); if (b) b.style.display = 'none'; }

async function loadCases() {
  try {
    cases = await api('/api/cases');
    updateStats(); renderCases();
  }
  catch(e) { $('#case-list').innerHTML = '<tr><td colspan="8" class="empty">加载失败: '+e.message+'</td></tr>'; }
}
function updateStats() {
  const pass = cases.filter(c => c.status==='PASS').length;
  const fail = cases.filter(c => c.status==='FAIL').length;
  $('#stat-total').textContent = cases.length;
  $('#stat-pass').textContent = pass;
  $('#stat-fail').textContent = fail;
}
function setFilter(f) {
  filter = f;
  ['all','PASS','FAIL'].forEach(k => {
    const el = $('#flt-'+k);
    if (el) el.className = 'filter-btn' + ((f||'all')===k ? ' active' : '');
  });
  renderCases();
}
function renderCases() {
  const q = ($('#case-search').value||'').toLowerCase();
  const list = cases.filter(c =>
    (!filter || c.status === filter) &&
    (!q || c.name.toLowerCase().includes(q) || (c.user_input||'').toLowerCase().includes(q)));
  const el = $('#case-list');
  const empty = $('#empty-state');
  if (!list.length) { el.innerHTML = ''; empty.style.display = ''; return; }
  empty.style.display = 'none';
  el.innerHTML = list.map(c => {
    const dur = fmtDuration(c.duration_seconds);
    const input = escapeHtml(c.user_input || '-');
    const sel = selectedCaseIds.has(c.id);
    return '<tr class="'+(sel?'sel-row':'')+'" onclick="openCase('+c.id+')">' +
      '<td onclick="event.stopPropagation()"><input type="checkbox" data-case-id="'+c.id+'" ' +
        (sel?'checked':'') + ' onchange="toggleCaseSel('+c.id+', this.checked)"></td>' +
      '<td class="mono">#'+c.id+'</td>' +
      '<td><b>'+escapeHtml(c.name)+'</b></td>' +
      '<td>'+category(c.name)+'</td>' +
      '<td>'+badge(c.status)+'</td>' +
      '<td class="mono" style="font-size:11px;color:var(--text-3)">'+fmtDateTime(c.started_at)+'</td>' +
      '<td class="mono">'+dur+'</td>' +
      '<td><div class="truncate" title="'+input+'">'+input+'</div></td>' +
      '<td><button class="del-btn" onclick="event.stopPropagation();deleteCase('+c.id+')">删除</button></td>' +
    '</tr>';
  }).join('');
  updateSelCount();
}
// ── 多选删除 ──
const selectedCaseIds = new Set();

function toggleSelectAll(on) {
  const q = ($('#case-search').value||'').toLowerCase();
  cases.filter(c =>
    (!filter || c.status === filter) &&
    (!q || c.name.toLowerCase().includes(q) || (c.user_input||'').toLowerCase().includes(q))
  ).forEach(c => { on ? selectedCaseIds.add(c.id) : selectedCaseIds.delete(c.id); });
  // 两个全选框状态同步
  ['sel-all','sel-all-head'].forEach(id => { const el = $('#'+id); if (el) el.checked = on; });
  renderCases();
}

function updateSelCount() {
  const btn = $('#btn-batch-del'), cnt = $('#sel-count');
  if (btn) btn.style.display = selectedCaseIds.size ? '' : 'none';
  if (cnt) cnt.textContent = selectedCaseIds.size;
}

function toggleCaseSel(id, on) {
  on ? selectedCaseIds.add(id) : selectedCaseIds.delete(id);
  updateSelCount();
  // 行高亮
  const row = document.querySelector('input[data-case-id="'+id+'"]');
  if (row) row.closest('tr').classList.toggle('sel-row', on);
}

async function deleteSelectedCases() {
  const ids = [...selectedCaseIds];
  if (!ids.length) return;
  if (!confirm('确认删除所选 '+ids.length+' 条记录？\n对应的报告和截图将一并从磁盘删除，不可恢复。')) return;
  // 删库+清磁盘文件+刷新列表都要时间，等待框期间禁止再点（finally 保证一定消失）
  showBusy('正在删除 '+ids.length+' 条记录…');
  let r = null, err = null;
  try {
    r = await api('/api/cases/delete-batch', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ids})
    });
    selectedCaseIds.clear();
    updateSelCount();
    const sa = $('#sel-all'), sah = $('#sel-all-head');
    if (sa) sa.checked = false;
    if (sah) sah.checked = false;
    await loadCases();
  } catch(e) { err = e; }
  finally { hideBusy(); }
  if (err) { alert('批量删除失败: '+err.message); return; }
  alert('已删除 '+r.deleted.length+' 条记录，清理磁盘文件 '+r.removed_files+' 个' +
        (r.missing.length ? '（'+r.missing.length+' 条已不存在）' : ''));
}

// 报告文件丢了/内容对不上时，从数据库记录重新渲染一份。
// 步骤、断言、证据路径都在库里，报告只是这些数据的 Markdown 视图。
async function rebuildReport(id) {
  if (!id) return;
  if (!confirm('用数据库里的记录重新生成这份报告？\n\n'
    + '（报告若还被其它记录共用，会自动另存一份，不会覆盖别人的）')) return;
  showBusy('正在重建报告…');
  let err = null;
  try {
    await api('/api/cases/' + id + '/rebuild-report', { method: 'POST' });
  } catch (e) { err = e; }
  hideBusy();
  if (err) { alert('重建报告失败: ' + err.message); return; }
  await openCase(id);     // 刷新详情里的报告路径
  await loadCases();      // 列表里也可能显示报告状态
}

async function deleteCase(id) {
  if (!confirm('确认删除这条测试记录？\n对应的报告和截图将一并从磁盘删除，不可恢复。')) return;
  showBusy('正在删除记录…');
  let err = null;
  try {
    await api('/api/cases/'+id, {method:'DELETE'});
    selectedCaseIds.delete(id);
    updateSelCount();
    cases = cases.filter(c => c.id !== id);
    updateStats(); renderCases();
    if (currentCaseId === id) closeDetail();
  } catch(e) { err = e; }
  finally { hideBusy(); }
  if (err) alert('删除失败: '+err.message);
}

function renderActions(actions) {
  if (!actions || !actions.length) return '';
  const rows = actions.map(a => {
    const icon = {tap:'👆', input:'⌨️', clear:'🧹', screenshot:'📷', pm_clear:'🗑️', grant_permission:'🔐', start_app:'🚀', press:'🔘', swipe:'↔️', long_tap:'⏱️', set_text:'📝'}[a.action] || '•';
    return '<div class="action-item">' +
      '<span class="action-kind">'+icon+' '+a.action+'</span>' +
      '<span class="action-detail">'+(a.detail||'')+'</span>' +
      '<span class="action-ms">'+(a.duration_ms?a.duration_ms+'ms':'')+'</span>' +
    '</div>';
  }).join('');
  return '<div class="action-log"><div class="action-log-label">操作时间线</div><div class="action-list">'+rows+'</div></div>';
}

async function openCase(id) {
  currentCaseId = id;
  const c = await api('/api/cases/'+id);
  $('#detail-title').innerHTML = badge(c.status) + ' ' + escapeHtml(c.name);
  let html = '';

  if (c.user_input) {
    html += '<div class="user-input-card"><div class="label">📝 用户原始输入 / Case 输入</div><div class="body">'+escapeHtml(c.user_input)+'</div></div>';
  }

  html += '<div class="detail-meta">';
  html += '<div class="row"><span class="label">执行结果</span><span class="value">'+badge(c.status)+' '+(c.summary||'')+'</span></div>';
  if (c.duration_seconds!==null && c.duration_seconds!==undefined) html += '<div class="row"><span class="label">总耗时</span><span class="value mono" style="color:var(--accent);font-weight:700">'+fmtDuration(c.duration_seconds)+'</span></div>';
  if (c.started_at) html += '<div class="row"><span class="label">执行时间</span><span class="value mono">'+c.started_at.replace('T',' ')+'</span></div>';
  if (c.finished_at) html += '<div class="row"><span class="label">结束时间</span><span class="value mono">'+c.finished_at.replace('T',' ')+'</span></div>';
  if (c.device) html += '<div class="row"><span class="label">设备</span><span class="value mono">'+escapeHtml(c.device)+'</span></div>';
  if (c.package) html += '<div class="row"><span class="label">被测 App</span><span class="value mono">'+escapeHtml(c.package)+'</span></div>';
  if (c.script_path) html += '<div class="row"><span class="label">脚本路径</span><span class="value mono">'+escapeHtml(c.script_path)+'</span></div>';
  if (c.report_path) html += '<div class="row"><span class="label">报告</span><span class="value mono ev-link" data-ev="'+encodeURIComponent(c.report_path)+'">'+escapeHtml(c.report_path)+'</span></div>';
  html += '</div>';

  // ── 失败摘要：FAIL/BLOCKED 结果顶部汇总，不用翻到底部找失败原因 ──
  const fails = [];
  for (const s of c.steps) {
    for (const r of (s.results||[])) {
      if (r.result === 'FAIL' || r.result === 'BLOCKED') {
        fails.push({step: s.name, result: r.result, detail: r.detail});
      }
    }
  }
  if (fails.length) {
    html += '<div class="fail-summary">';
    html += '<div class="fail-summary-title">❌ 失败摘要（'+fails.length+' 条）</div>';
    for (const f of fails) {
      html += '<div class="fail-summary-item">'+badge(f.result)+' <span class="fail-step">'+escapeHtml(f.step)+'</span> — '+escapeHtml(f.detail)+'</div>';
    }
    html += '</div>';
  }

  // 收集所有截图路径（用于 lightbox 左右导航）
  const allEvs = [];
  for (const s of c.steps) {
    for (const r of (s.results||[])) {
      if (r.evidence) allEvs.push(encodeURIComponent(r.evidence));
    }
    const hasOps = s.actions && s.actions.length > 0;
    const resultEvs = new Set((s.results||[]).map(r => r.evidence).filter(Boolean));
    const opEvs = hasOps ? (s.evidences||[]).filter(ev => !resultEvs.has(ev)) : [];
    for (const ev of opEvs) allEvs.push(encodeURIComponent(ev));
  }

  html += '<div class="timeline">';
  for (const s of c.steps) {
    html += '<div class="step"><div class="step-dot"></div><div class="step-header"><div class="step-name">'+escapeHtml(s.name)+'</div></div>';
    html += renderActions(s.actions);
    for (const r of s.results) {
      html += '<div class="res"><div class="res-head">'+badge(r.result)+'</div>';
      html += '<div class="res-detail">'+escapeHtml(r.detail)+'</div>';
      if (r.state) html += '<div class="res-state">状态 '+JSON.stringify(r.state)+'</div>';
      if (r.evidence) html += '<div class="gallery" style="margin-top:8px;">'+imgTag(r.evidence, '验证点截图')+'</div>';
      html += '</div>';
    }
    // 操作截图：仅当该步骤确实执行过操作（tap/input/clear 等）才展示。
    // 纯验证步骤（无 actions）只有 step() 拍的"步骤开始"图，与验证点截图画面重复，不展示。
    const hasOps = s.actions && s.actions.length > 0;
    const resultEvs = new Set((s.results||[]).map(r => r.evidence).filter(Boolean));
    const opEvs = hasOps ? (s.evidences||[]).filter(ev => !resultEvs.has(ev)) : [];
    if (opEvs.length) {
      html += '<div class="gallery-label">操作截图</div><div class="gallery">'+opEvs.map(ev => imgTag(ev, '操作截图')).join('')+'</div>';
    }
    html += '</div>';
  }
  html += '</div>';

  // ── 历史时间线（同 script_path 的近 20 次执行）──
  if (c.script_path) {
    try {
      const hist = await api('/api/cases/' + id + '/history');
      if (hist && hist.length > 1) {
        html += '<div class="detail-meta" style="margin-top:12px">';
        html += '<div class="row"><span class="label">📊 历史执行（近 '+hist.length+' 次）</span></div>';
        html += '<div class="hist-timeline">';
        const statusColor = {PASS:'var(--pass)',WARN:'var(--warn)',FAIL:'var(--fail)',BLOCKED:'var(--blocked)',ERROR:'var(--fail)'};
        for (const h of hist) {
          const col = statusColor[h.final_status] || 'var(--text-3)';
          const dur = h.duration_seconds ? h.duration_seconds.toFixed(1)+'s' : '';
          html += '<div class="hist-block" style="background:'+col+'" title="#'+h.id+' '+h.final_status+' '+dur+' '+(h.started_at||'')+'"></div>';
        }
        html += '</div></div>';
      }
    } catch(e) { /* 历史加载失败不阻断详情展示 */ }
  }

  $('#detail-body').innerHTML = html;
  $('#detail-overlay').classList.add('open');
  $('#detail-panel').classList.add('open');
}
function closeDetail() { currentCaseId=null; $('#detail-overlay').classList.remove('open'); $('#detail-panel').classList.remove('open'); setTimeout(()=>$('#detail-body').innerHTML='',250); }

// ── Lightbox 导航：左右箭头 + 键盘方向键切换截图 ──
let lbImages = [], lbIndex = 0;
function openLightbox(pathEnc, allImages, idx) {
  // allImages: 当前 case 的所有截图路径数组（已 encodeURIComponent）
  // idx: 当前点击图片在 allImages 中的下标
  if (allImages && allImages.length > 1) {
    lbImages = allImages; lbIndex = idx || 0;
  } else {
    lbImages = [pathEnc]; lbIndex = 0;
  }
  lbShow();
  $('#lightbox').classList.add('open');
}
function lbShow() {
  $('#lightbox-img').src = '/api/file?path=' + lbImages[lbIndex];
  const counter = $('#lb-counter');
  if (counter) counter.textContent = (lbIndex+1) + ' / ' + lbImages.length;
  // 单张时隐藏导航
  const prev = $('#lb-prev'), next = $('#lb-next');
  const show_nav = lbImages.length > 1;
  if (prev) prev.style.display = show_nav ? '' : 'none';
  if (next) next.style.display = show_nav ? '' : 'none';
  if (counter) counter.style.display = show_nav ? '' : 'none';
}
function lbPrev() { if (lbImages.length<=1) return; lbIndex = (lbIndex-1+lbImages.length) % lbImages.length; lbShow(); }
function lbNext() { if (lbImages.length<=1) return; lbIndex = (lbIndex+1) % lbImages.length; lbShow(); }
function closeLightbox() { $('#lightbox').classList.remove('open'); $('#lightbox-img').src=''; lbImages=[]; lbIndex=0; }
document.addEventListener('keydown', function(e) {
  if (!$('#lightbox').classList.contains('open')) return;
  if (e.key === 'ArrowLeft') { e.preventDefault(); lbPrev(); }
  else if (e.key === 'ArrowRight') { e.preventDefault(); lbNext(); }
  else if (e.key === 'Escape') closeLightbox();
});
document.addEventListener('click', function(e) {
  var t=e.target.closest?e.target.closest('.ev-link'):null;
  if(t&&t.dataset.ev) window.open('/api/file?path='+t.dataset.ev,'_blank');
  // Lightbox：点击 .shot 截图时打开，传入全量图片列表与当前下标
  var shot = e.target.closest?e.target.closest('.shot'):null;
  if (shot && shot.dataset.lbPath) {
    const shots = [...document.querySelectorAll('.shot[data-lb-path]')];
    const paths = shots.map(s => s.dataset.lbPath);
    const idx = shots.indexOf(shot);
    openLightbox(shot.dataset.lbPath, paths, idx);
  }
});

// ── Case 用例管理 ──


// ── Case 用例管理 ──
let scripts = [], currentScriptName = null, scriptEditing = false;

async function loadScripts() {
  try {
    scripts = await api('/api/scripts');
    const cnt = $('#script-count');
    if (cnt) cnt.textContent = scripts.length;
    renderScripts();
  } catch(e) {
    $('#script-list').innerHTML = '<tr><td colspan="6" class="empty">加载失败: '+e.message+'</td></tr>';
  }
}
function fmtTime(ts) {
  if (!ts) return '-';
  const d = new Date(ts*1000), p = n => String(n).padStart(2,'0');
  return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
function firstLine(s) {
  const l = (s||'').split('\n').map(x=>x.trim()).filter(Boolean)[0] || '';
  return l.length > 70 ? l.slice(0,70)+'…' : l;
}
function renderScripts() {
  const q = ($('#script-search') ? $('#script-search').value : '').toLowerCase();
  const list = scripts.filter(s => !q || s.name.toLowerCase().includes(q) ||
                                   (s.description||'').toLowerCase().includes(q));
  const el = $('#script-list'), empty = $('#script-empty');
  if (!el) return;
  if (!list.length) { el.innerHTML = ''; if (empty) empty.style.display=''; return; }
  if (empty) empty.style.display = 'none';
  el.innerHTML = list.map(s => {
    const desc = escapeHtml(s.description || '');
    // 共享模块（_ 开头，如 _flow.py）：可被多个用例 import，不是可执行用例。
    // 列表里照常展示以便查看/编辑，但不给删除入口（后端也拒绝删除）。
    const tag = s.shared
      ? ' <span class="badge b-SHARED" title="共享模块：被同目录用例 import，不是可执行用例，不可删除">共享</span>'
      : '';
    const delBtn = s.shared ? '' :
      '<button class="del-btn" onclick="event.stopPropagation();deleteScript(\''+encodeURIComponent(s.name)+'\')">删除</button>';
    // 整行可点击查看（与历史记录列表一致），删除按钮需阻止冒泡
    return '<tr class="row-click" onclick="openScript(\''+encodeURIComponent(s.name)+'\')" title="点击查看/编辑">' +
      '<td><b>'+escapeHtml(s.name)+'</b>'+tag+'</td>' +
      '<td><div class="truncate" title="'+desc+'">'+escapeHtml(firstLine(s.description))+'</div></td>' +
      '<td class="mono" style="text-align:center">'+(s.steps||0)+'</td>' +
      '<td class="mono" style="font-size:11px;color:var(--text-3)">'+fmtTime(s.mtime)+'</td>' +
      // 注意：flex 必须放在 td 内部的 div 上。直接给 <td> 加 display:flex 会让它
      // 脱离表格布局，浏览器补一个匿名单元格，底边框就会跑到按钮下方、且宽度
      // 不再是这一列 —— 表现出来就是"删除按钮下面多了一条线"。
      '<td>' +
        '<div style="display:flex;gap:6px">' + delBtn + '</div>' +
      '</td>' +
    '</tr>';
  }).join('');
}
async function openScript(nameEnc) {
  currentScriptName = decodeURIComponent(nameEnc);
  const r = await fetch('/api/scripts/'+encodeURIComponent(currentScriptName));
  if (!r.ok) { alert('读取失败'); return; }
  $('#script-editor').value = await r.text();
  const meta = scripts.find(x => x.name === currentScriptName) || {};
  let mh = '<div class="row"><span class="label">文件名</span><span class="value mono">'+escapeHtml(currentScriptName)+'</span></div>';
  mh += '<div class="row"><span class="label">路径</span><span class="value mono">'+escapeHtml(meta.path||'')+'</span></div>';
  mh += '<div class="row"><span class="label">步骤数</span><span class="value mono">'+(meta.steps||0)+'</span></div>';
  // 共享模块没有 USER_INPUT 常量，描述通常为空 —— 说明它是什么，避免看起来
  // 像"一个没有描述的用例"，也顺带交代为什么不给删除入口。
  if (meta.shared) {
    mh += '<div class="row"><span class="label">类型</span><span class="value">' +
          '<span class="badge b-SHARED">共享模块</span> 供同目录用例 import，' +
          '不是可执行用例，不可删除</span></div>';
  }
  if (meta.description) mh += '<div class="row"><span class="label">描述</span><span class="value">'+escapeHtml(meta.description)+'</span></div>';
  $('#script-meta').innerHTML = mh;
  $('#script-title').textContent = currentScriptName;
  $('#script-saved').textContent = '';
  $('#script-overlay').classList.add('open');
  $('#script-panel').classList.add('open');
}
function closeScript() {
  currentScriptName = null;
  $('#script-overlay').classList.remove('open');
  $('#script-panel').classList.remove('open');
}
async function saveScript() {
  if (!currentScriptName) return;
  try {
    await api('/api/scripts/'+encodeURIComponent(currentScriptName), {
      method:'PUT', headers:{'Content-Type':'text/plain'}, body: $('#script-editor').value });
    const t = $('#script-saved'); t.textContent = '✓ 已保存';
    setTimeout(()=>t.textContent='', 2000);
    await loadScripts();
  } catch(e) { alert('保存失败: '+e.message); }
}
async function deleteScript(nameEnc) {
  const name = decodeURIComponent(nameEnc);
  if (!confirm('确认删除用例脚本「'+name+'」？\n（仅删除脚本文件，不影响历史测试记录与截图）')) return;
  showBusy('正在删除用例脚本…');
  let err = null;
  try {
    await api('/api/scripts/'+encodeURIComponent(name), {method:'DELETE'});
    scripts = scripts.filter(s => s.name !== name);
    renderScripts();
    if (currentScriptName === name) closeScript();
  } catch(e) { err = e; }
  finally { hideBusy(); }
  if (err) alert('删除失败: '+err.message);
}
function newScript() {
  const name = prompt('新建用例（按包名分目录，如 com.zui.calendar/175.py）:');
  if (!name) return;
  const full = name.endsWith('.py') ? name : name+'.py';
  const base = full.split('/').pop().replace(/\.py$/,'');
  const tmpl = '#!/usr/bin/env python3\n'
    + '"""'+base+' 用例"""\n'
    + 'import os\nimport sys\nimport time\n\n'
    + '_HERE = os.path.dirname(os.path.abspath(__file__))\n'
    + 'sys.path.insert(0, _HERE)  # 同目录 _flow.py（本 App 可复用流程）\n'
    + 'sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(_HERE)), "framework"))\n'
    + 'from test_framework import TestCase\n\n'
    + 'USER_INPUT = """'+base+'\n前提：\n操作步骤：\n预期结果："""\n\n'
    + 'PKG = os.path.basename(_HERE)  # 目录名即包名\n\n\n'
    + 'def run():\n'
    + '    t = TestCase("'+base+'")\n'
    + '    t.step("Step1")\n'
    + '    # TODO: 补充操作步骤与断言\n'
    + '    t.record("PASS", "示例断言")\n'
    + '    return t.finish()\n\n\n'
    + 'if __name__ == "__main__":\n'
    + '    run()\n';
  api('/api/scripts/'+encodeURIComponent(full), {
    method:'PUT', headers:{'Content-Type':'text/plain'}, body: tmpl })
    .then(() => loadScripts())
    .then(() => openScript(encodeURIComponent(full)))
    .catch(e => alert('创建失败: '+e.message));
}

// ── 知识库 ──
let kbFreshness = null;  // card-freshness 缓存
async function loadKnowledgeList() {
  try {
    kbFiles = await api('/api/knowledge');
    // 拉取卡片新鲜度（容错：API 不可用时不影响列表展示）
    try { kbFreshness = await api('/api/card-freshness?days=30'); } catch(_) { kbFreshness = null; }
    const el = $('#kb-files'); el.innerHTML = '';
    kbFiles.forEach(item => {
      const name = item.name, prot = item.protected;
      const row = document.createElement('div');
      row.className = 'kb-row';
      const b = document.createElement('button');
      b.className = 'kb-item' + (name===kbCurrent?' active':'');
      b.onclick = () => loadKnowledge(name);
      const nm = document.createElement('span');
      nm.className = 'kb-name'; nm.textContent = name;
      b.appendChild(nm);
      if (prot) {
        const tag = document.createElement('span');
        tag.className = 'kb-tag'; tag.textContent = '内置';
        tag.title = '内置卡：作为样式来源/兜底卡，不可删除或修改';
        b.appendChild(tag);
      } else {
        const del = document.createElement('span');
        del.className = 'kb-del'; del.textContent = '🗑';
        del.title = '删除该知识卡';
        del.onclick = (e) => { e.stopPropagation(); deleteKnowledge(name); };
        b.appendChild(del);
      }
      // 知识卡新鲜度徽章（匹配 App 卡包名）
      if (kbFreshness) {
        const pkg = name.replace(/\.md$/, '');
        const fresh = kbFreshness.find(r => r.package === pkg);
        if (fresh) {
          const badge = document.createElement('span');
          badge.className = 'kb-tag';
          if (fresh.stale) {
            badge.textContent = '⚠ 未验证';
            badge.title = '该卡 30 天内无 PASS 记录，建议跑一遍用例验证';
            badge.style.color = '#c66';
          } else {
            const d = fresh.last_pass_at ? fresh.last_pass_at.slice(0, 10) : '?';
            badge.textContent = '✓ ' + d;
            badge.title = '最近验证时间: ' + (fresh.last_pass_at || '无');
          }
          b.appendChild(badge);
        }
      }
      row.appendChild(b);
      el.appendChild(row);
    });
    if (kbFiles.length && !kbCurrent) loadKnowledge(kbFiles[0].name);
    else if (!kbFiles.length) { kbSetDoc(''); }
  } catch(e) { $('#kb-files').innerHTML='<div class="empty">'+e.message+'</div>'; }
}
function isProtectedKb(name) { const f = kbFiles.find(x=>x.name===name); return !!(f && f.protected); }
// 只读（不可编辑），与"仅禁删"区分开
function isReadonlyKb(name) { const f = kbFiles.find(x=>x.name===name); return !!(f && f.readonly); }
async function loadKnowledge(name) {
  kbCurrent=name;
  document.querySelectorAll('#kb-files .kb-item').forEach(b=>{
    b.classList.toggle('active', b.querySelector('.kb-name').textContent===name);
  });
  const r = await fetch('/api/knowledge/'+encodeURIComponent(name));
  kbSetDoc(await r.text());
  // 两种保护分开处理：
  //   readonly (_template) → 不可编辑、不可保存，但可复制其内容作参考
  //   nodelete (_system)   → 不可删除，但允许编辑补充（通用兜底经验需持续积累）
  const ro = isReadonlyKb(name);
  kbSetReadOnly(ro);
  $('#kb-save').disabled = ro;
  $('#kb-del').disabled = isProtectedKb(name);
  $('#kb-del').style.display = isProtectedKb(name) ? 'none' : '';
  const h = $('#kb-editor-wrap') || $('#kb-editor');
  if (h) h.classList.toggle('ro', ro);
  const tip = $('#kb-protected-tip');
  if (tip) {
    if (ro) { tip.textContent = '样式模板为只读：它是新建知识卡的样式来源，不可修改或删除。'; tip.style.display = ''; }
    else if (isProtectedKb(name)) { tip.textContent = '系统卡不可删除，但可以编辑补充内容（通用兜底经验）。'; tip.style.display = ''; }
    else { tip.style.display = 'none'; }
  }
  clearKbError();
  updateKbEmpty();
}
// ── CodeMirror 编辑器（知识卡为 MD：bundle 仅当通用高亮编辑器用，
//    yaml 语言模式对纯文本无害；不再有服务端 safe_load 硬校验） ──
let kbView = null;          // CodeMirror 实例
let kbReady = false;        // bundle 是否加载成功
let kbDocDirty = false;

// 初始化编辑器（幂等）
function initKbEditor() {
  if (kbReady || typeof window.CodeMirrorYaml === 'undefined') return kbReady;
  const host = $('#kb-editor');
  if (!host) return false;
  try {
    kbView = window.CodeMirrorYaml.create(host, '', {
      dark: document.documentElement.classList.contains('dark'),
      onChange: () => { kbDocDirty = true; updateKbEmpty(); }
    });
    kbReady = true;
    // 用 CodeMirror 时隐藏降级 textarea
    const raw = $('#kb-editor-raw');
    if (raw) raw.style.display = 'none';
    return true;
  } catch (e) {
    // bundle 不可用时降级为普通 textarea
    const raw = $('#kb-editor-raw');
    if (raw) {
      raw.style.display = '';
      raw.addEventListener('input', updateKbEmpty);
    }
    return false;
  }
}

function kbGetDoc() {
  if (kbReady && kbView) return kbView.state.doc.toString();
  const raw = $('#kb-editor-raw');
  return raw ? raw.value : '';
}
function kbSetDoc(text) {
  const t = text == null ? '' : String(text);
  if (kbReady && kbView) {
    kbView.dispatch({changes: {from: 0, to: kbView.state.doc.length, insert: t}});
  }
  const raw = $('#kb-editor-raw');
  if (raw) raw.value = t;
  kbDocDirty = false;
  updateKbEmpty();
}
function kbSetReadOnly(ro) {
  if (kbReady && kbView) {
    kbView.contentDOM.setAttribute('contenteditable', ro ? 'false' : 'true');
  }
  const raw = $('#kb-editor-raw');
  if (raw) raw.readOnly = !!ro;
}
// 主题切换时同步编辑器配色
function kbSyncTheme() {
  if (kbReady && kbView && typeof kbView.setTheme === 'function') {
    kbView.setTheme(document.documentElement.classList.contains('dark'));
  }
}

// ── 编辑时静态检查 ──
// 知识卡已是 MD（2026-09）：任何文本都合法，YAML 重复键/缩进问题不复存在。
// 保留函数壳（多处调用），现在只做一件事：空文档时清掉错误提示。
function kbLint() {
  const text = kbGetDoc();
  if (!text.trim()) { clearKbError(); return []; }
  return [];
}
// 保留定时器壳，MD 无需 lint；只负责清提示
let kbLintTimer = null;
function kbScheduleLint() {
  if (kbLintTimer) clearTimeout(kbLintTimer);
  kbLintTimer = setTimeout(() => {
    clearKbError();
  }, 600);
}

function updateKbEmpty() {
  const e = $('#kb-empty');
  if (e) e.style.display = kbGetDoc() ? 'none' : 'grid';
  kbScheduleLint();
}
function clearKbError() {
  const e = $('#kb-error'); if (e) { e.style.display='none'; e.textContent=''; }
}
function showKbError(msg) {
  const e = $('#kb-error'); if (!e) return;
  e.textContent = '⚠ ' + msg; e.style.display = '';
}

async function saveKnowledge() {
  if (!kbCurrent) { alert('先选择或新建一个知识卡'); return; }
  // 只读卡（_template）禁保存；_system 仅禁删、允许保存补充
  if (isReadonlyKb(kbCurrent)) { showKbError('样式模板只读不可修改（它是新建知识卡的样式来源）'); return; }
  clearKbError();
  const t = $('#kb-saved');
  try {
    await api('/api/knowledge/'+encodeURIComponent(kbCurrent), {
      method:'PUT', headers:{'Content-Type':'text/plain'}, body: kbGetDoc()
    });
    kbDocDirty = false;
    t.textContent = '✓ 已保存 ' + kbCurrent;
    setTimeout(()=>t.textContent='', 2500);
  } catch (e) {
    // 服务端只拦空文件（MD 无语法校验），此处展示原始报错
    showKbError(e.message);
    t.textContent = '';
  }
}

async function deleteKnowledge(name) {
  if (isProtectedKb(name)) {
    showKbError(isReadonlyKb(name) ? '样式模板不可删除' : '系统卡不可删除，但可以编辑补充内容');
    return;
  }
  if (!confirm('确定删除知识卡「'+name+'」？此操作不可恢复。')) return;
  try {
    await api('/api/knowledge/'+encodeURIComponent(name), { method:'DELETE' });
    if (kbCurrent === name) { kbCurrent = null; kbSetDoc(''); }
    await loadKnowledgeList();
    clearKbError();
  } catch(e) { showKbError(e.message); }
}

// 新建知识卡：先选类型（App 卡 / 场景卡），两者的 schema 不同
async function newKnowledge() {
  const kind = await pickKbKind();
  if (!kind) return;                       // 用户取消
  if (kind === 'scenario') return newScenario();
  return newAppCard();
}

// 用下拉（而非 confirm）选类型：confirm 只有确定/取消两个按钮，
// 第三个类型加不进去，且按钮文案写不下说明
function pickKbKind() {
  return new Promise(resolve => {
    const wrap = document.createElement('div');
    wrap.className = 'kb-kind';
    wrap.innerHTML =
      '<div class="kb-kind-mask"></div>' +
      '<div class="kb-kind-box">' +
        '<div class="kb-kind-title">新建哪种知识卡？</div>' +
        '<button class="kb-kind-opt" data-v="app">' +
          '<b>App 卡</b><span>按包名记录某个 App 的操作经验（界面结构 / 高效操作 / 已知坑）</span></button>' +
        '<button class="kb-kind-opt" data-v="scenario">' +
          '<b>场景卡</b><span>跨 App 的操作场景（无限工作台 / 分屏…），放在 scenarios/ 下</span></button>' +
        '<button class="kb-kind-opt cancel" data-v="">取消</button>' +
      '</div>';
    const close = (v) => { wrap.remove(); resolve(v); };
    wrap.querySelector('.kb-kind-mask').onclick = () => close('');
    wrap.querySelectorAll('.kb-kind-opt').forEach(b => {
      b.onclick = () => close(b.dataset.v);
    });
    document.body.appendChild(wrap);
  });
}

// ── App 卡 ──
async function newAppCard() {
  const name = prompt('知识卡文件名（推荐用 App 包名，如 com.example.app.md）:');
  if (!name) return;
  let full = name.trim();
  if (!/\.md$/.test(full)) full += '.md';
  if (!/^[\w.\-]+\.md$/.test(full)) { alert('文件名只能包含字母、数字、下划线、点、短横线'); return; }
  if (kbFiles.some(x=>x.name===full)) { alert('该知识卡已存在'); return; }
  const base = full.replace(/\.md$/, '');
  let tmpl;
  try {
    // 服务端按包名预填 app 字段
    const r = await fetch('/api/knowledge-template?app=' + encodeURIComponent(base));
    tmpl = await r.text();
  } catch(e) { tmpl = ''; }
  if (!tmpl) { tmpl = '# ' + base + '\n\n- **app**: ' + base + '\n'; }
  await createKbFile(full, tmpl);
}

// ── 场景卡 ──
async function newScenario() {
  const name = prompt('场景名（如 分屏、勿扰模式）:\n将保存为 knowledge/scenarios/sys.<场景名>.md');
  if (!name) return;
  const scene = name.trim();
  if (!/^[\w.\-\u4e00-\u9fa5]+$/.test(scene)) {
    alert('场景名只能包含中文、字母、数字、下划线、点、短横线'); return;
  }
  const full = 'scenarios/sys.' + scene + '.md';
  if (kbFiles.some(x=>x.name===full)) { alert('该场景卡已存在'); return; }
  let tmpl;
  try {
    // 服务端以现有场景卡为样本生成骨架：替换场景名、清空旧触发词
    const r = await fetch('/api/scenario-template?scene=' + encodeURIComponent(scene));
    tmpl = await r.text();
  } catch(e) { tmpl = ''; }
  await createKbFile(full, tmpl);
}

// 落盘 + 刷新列表（App 卡与场景卡共用）
async function createKbFile(full, tmpl) {
  kbCurrent = full;
  kbSetDoc(tmpl);
  kbSetReadOnly(false);
  $('#kb-save').disabled = false;
  $('#kb-del').disabled = false;
  $('#kb-del').style.display = '';
  const tip = $('#kb-protected-tip'); if (tip) tip.style.display = 'none';
  clearKbError();
  try {
    await api('/api/knowledge/'+encodeURIComponent(full), {
      method:'PUT', headers:{'Content-Type':'text/plain'}, body: tmpl
    });
    await loadKnowledgeList();
    const t = $('#kb-saved'); t.textContent = '✓ 已创建 ' + full;
    setTimeout(()=>t.textContent='', 2500);
  } catch(e) { showKbError(e.message); }
}
// ── 视觉模型配置 ──────────────────────────────────────────────────
// 凭据只存工作区 storage/vision.json；GET 只回脱敏掩码，明文绝不回显。
let visionLoaded = false;

async function loadVision() {
  if (visionLoaded) return;
  try {
    const c = await api('/api/vision');
    $('#v-base').value  = c.base_url || '';
    $('#v-model').value = c.model || '';
    $('#v-strategy').value = c.tap_strategy || 'auto';
    $('#v-path').textContent = c.path || '-';
    // 密钥不回填明文，仅在提示里显示掩码
    const hint = $('#v-key-hint');
    if (c.has_key) {
      hint.textContent = '已保存：' + c.api_key_masked + '（留空则不修改）';
      hint.className = 'vision-hint ok';
    } else if (c.env_available) {
      hint.textContent = '环境变量 DEEPSEEK_API_KEY 已设置（优先生效）';
      hint.className = 'vision-hint ok';
    } else {
      hint.textContent = '尚未配置，视觉断言会降级为 WARN';
      hint.className = 'vision-hint warn';
    }
    visionLoaded = true;
  } catch(e) {
    const s = $('#v-status');
    if (s) { s.textContent = '加载失败：' + e.message; s.className = 'vision-status err'; }
  }
}

async function saveVision() {
  const status = $('#v-status'), saved = $('#v-saved');
  status.textContent = ''; status.className = 'vision-status';
  try {
    const r = await api('/api/vision', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        base_url: $('#v-base').value,
        model:    $('#v-model').value,
        api_key:  $('#v-key').value,   // 留空 = 保留原值
        tap_strategy: $('#v-strategy').value,
      })
    });
    $('#v-key').value = '';            // 清空输入框，避免明文留在页面上
    const hint = $('#v-key-hint');
    if (r.has_key) {
      hint.textContent = '已保存：' + r.api_key_masked + '（留空则不修改）';
      hint.className = 'vision-hint ok';
    }
    $('#v-path').textContent = r.path || '-';
    saved.textContent = '✓ 已保存';
    setTimeout(()=>saved.textContent='', 2500);
    visionLoaded = true;
  } catch(e) {
    status.textContent = '保存失败：' + e.message;
    status.className = 'vision-status err';
  }
}

async function testVision() {
  const status = $('#v-status');
  status.textContent = '正在测试…'; status.className = 'vision-status';
  try {
    const r = await api('/api/vision/test', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        base_url: $('#v-base').value,
        model:    $('#v-model').value,
        // 输入框有值则用输入的测（未保存也能试）；否则用已保存的
        api_key:  $('#v-key').value || '',
      })
    });
    status.textContent = (r.ok ? '✓ ' : '✗ ') + r.message;
    status.className = 'vision-status ' + (r.ok ? 'ok' : 'err');
  } catch(e) {
    // api() 对非 2xx 抛错，错误信息里含服务端返回的 message
    status.textContent = '✗ ' + e.message;
    status.className = 'vision-status err';
  }
}

// CodeMirror 已由 <script src="/codemirror.bundle.js"> 同步加载
initKbEditor();
loadCases();
loadScripts();

// ── Dashboard ──────────────────────────────────────────────────────────
let dashLoaded = false;
async function loadDashboard() {
  try {
    const d = await api('/api/dashboard');
    // 顶部统计卡
    const s = d.summary;
    const pct = s.total ? Math.round(s.pass_rate * 100) : 0;
    const pctColor = pct >= 80 ? 'pass' : (pct >= 50 ? 'warn' : 'fail');
    $('#dash-stats').innerHTML =
      '<div class="dash-stat"><div class="dash-stat-label">总执行</div><div class="dash-stat-value accent">'+s.total+'</div></div>' +
      '<div class="dash-stat"><div class="dash-stat-label">通过率</div><div class="dash-stat-value '+pctColor+'">'+pct+'%</div><div class="dash-stat-sub">'+s.pass+' / '+s.total+'</div></div>' +
      '<div class="dash-stat"><div class="dash-stat-label">成功</div><div class="dash-stat-value pass">'+s.pass+'</div></div>' +
      '<div class="dash-stat"><div class="dash-stat-label">失败</div><div class="dash-stat-value fail">'+s.fail+'</div></div>' +
      '<div class="dash-stat"><div class="dash-stat-label">Flaky</div><div class="dash-stat-value '+(d.flaky_top.length?'warn':'')+'">'+s.flaky_count+'</div></div>';
    // Flaky Top 10
    const flakyEl = $('#dash-flaky');
    if (!d.flaky_top.length) {
      flakyEl.innerHTML = '<div class="empty" style="padding:20px">暂无 flaky 用例</div>';
    } else {
      flakyEl.innerHTML = d.flaky_top.map(f => {
        const p = Math.round(f.pass_rate * 100);
        const color = p < 30 ? 'var(--fail)' : (p < 70 ? 'var(--warn)' : 'var(--pass)');
        const name = f.script_path ? f.script_path.split(/[\\/]/).pop().replace(/\.py$/,'') : '?';
        return '<div class="dash-row">' +
          '<div class="dash-row-name" title="'+escapeHtml(f.script_path||'')+'">'+escapeHtml(name)+'</div>' +
          '<div class="dash-row-bar"><div class="dash-row-bar-fill" style="width:'+p+'%;background:'+color+'"></div></div>' +
          '<div class="dash-row-pct" style="color:'+color+'">'+p+'%</div>' +
          '<div class="dash-row-runs">'+f.runs+'次</div>' +
        '</div>';
      }).join('');
    }
    // 按类别通过率
    const catEl = $('#dash-category');
    if (!d.by_category.length) {
      catEl.innerHTML = '<div class="empty" style="padding:20px">暂无数据</div>';
    } else {
      catEl.innerHTML = d.by_category.map(c => {
        const p = Math.round(c.pass_rate * 100);
        const color = p >= 80 ? 'var(--pass)' : (p >= 50 ? 'var(--warn)' : 'var(--fail)');
        return '<div class="dash-row">' +
          '<div class="dash-row-name">'+escapeHtml(c.category)+'</div>' +
          '<div class="dash-row-bar"><div class="dash-row-bar-fill" style="width:'+p+'%;background:'+color+'"></div></div>' +
          '<div class="dash-row-pct" style="color:'+color+'">'+p+'%</div>' +
          '<div class="dash-row-runs">'+c.runs+'次</div>' +
        '</div>';
      }).join('');
    }
    dashLoaded = true;
  } catch(e) {
    $('#dash-stats').innerHTML = '<div class="empty">加载失败: '+e.message+'</div>';
  }
}

// ── 版本号：侧栏底部常驻，快速确认是否为最新版本 ──
(async function loadVersion() {
  try {
    const v = await api('/api/version');
    const el = $('#version-info');
    if (el) el.textContent = v.version || v.commit || '-';
  } catch(_) {
    const el = $('#version-info');
    if (el) el.textContent = '';
  }
})();

// ── 首屏初始化 ────────────────────────────────────────────────────────
// HTML 里 dashboard 是默认视图（#nav-dashboard 带 active、#view-dashboard 无
// 隐藏样式），但 show() 只由侧栏按钮的 onclick 触发 —— 不做初始化，首屏就永远
// 停在 HTML 写死的「加载中…」占位符上，必须手动点一下 Dashboard 才出数据。
// 这里直接调 loadDashboard()：只补数据，不复用 show()，避免连带触发其他视图
// 的加载（show() 里还有 scripts/knowledge/vision 的分支）。
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => { loadDashboard(); });
} else {
  loadDashboard();
}
