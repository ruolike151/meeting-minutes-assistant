/* =========================================================
   会议纪要助手 · 交互原型逻辑
   说明：后端未接入，全部回答由本地模拟的「意图路由 + 数据装配」生成，
        数据结构与《产品设计文档》第 6 节保持一致。
   ========================================================= */

/* ───────────── 图标（统一 1.5px 描边，currentColor 着色）───────────── */

const ICON = {
  spark:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
  user:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  quote:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 4h14v16H5z"/><path d="M9 9h6M9 13h6M9 17h3"/></svg>',
  check:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5L19 7"/></svg>',
  alert:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>',
  clock:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  cal:     '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>',
  wave:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/></svg>',
  help:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.2 2.4c-.6.2-.9.7-.9 1.3v.3M12 17h.01"/></svg>',
  search:  '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  down:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>',
  doc:     '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
  table:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/></svg>',
  chat:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"/></svg>',
  mic:     '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3Z"/><path d="M19 11a7 7 0 0 1-14 0"/><path d="M12 18v3"/></svg>',
  empty:   '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h18v12H3z"/><path d="M8 7V5h8v2"/><path d="M3 12h18"/></svg>'
};

/* ───────────── 状态 ───────────── */

const state = {
  activeId: null,               // 对话作用域：null 表示全局
  panelMeetingId: 'm_20260911', // 详情面板展示的会议
  tab: 'minutes',
  messages: [],
  pendingFile: null,
  busy: false,
  resolved: new Set(),          // 已确认的待办 "mid:itemId"
  dismissed: new Set(),         // 已忽略的待确认条目
  panelOpen: window.innerWidth > 1080
};

/* ───────────── 工具函数 ───────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function meetingById(id) { return MEETINGS.find(m => m.id === id) || null; }
function speakerById(id) { return SPEAKERS[id] || { name: '未识别', role: '', short: '?', color: 'var(--fg-subtle)' }; }

function mmss(sec) {
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
}

function durationText(sec) {
  const m = Math.round(sec / 60);
  return m >= 60 ? Math.floor(m / 60) + ' 小时 ' + (m % 60) + ' 分' : m + ' 分钟';
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function fileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

/* 证据引用：把 [[会议ID:片段ID]] 渲染成可点击的 chip */
function renderRich(text) {
  return escapeHtml(text).replace(/\[\[([\w]+):([\w]+)\]\]/g, (_, mid, sid) => {
    const m = meetingById(mid);
    const seg = m ? m.segments.find(s => s.id === sid) : null;
    if (!seg) return '';
    const sp = speakerById(seg.speaker);
    const label = sp.name + ' ' + mmss(seg.start);
    return '<button class="cite" type="button" data-mid="' + mid + '" data-sid="' + sid + '"' +
           ' title="查看原声片段">' + ICON.quote + label + '</button>';
  });
}

/* ───────────── Toast ───────────── */

function toast(message, kind) {
  const stack = $('#toastStack');
  const node = document.createElement('div');
  node.className = 'toast' + (kind === 'ok' ? ' ok' : '');
  node.innerHTML = (kind === 'ok' ? ICON.check : ICON.spark) + '<span>' + escapeHtml(message) + '</span>';
  stack.appendChild(node);
  setTimeout(() => {
    node.classList.add('out');
    setTimeout(() => node.remove(), 260);
  }, 2400);
}
/* ───────────── 渲染：会议库 ───────────── */

function renderMeetingList() {
  const list = $('#meetingList');
  const q = $('#meetingSearch').value.trim().toLowerCase();

  const matched = MEETINGS.filter(m => {
    if (!q) return true;
    const names = m.participants.map(p => speakerById(p).name).join(' ');
    return (m.title + ' ' + names + ' ' + m.date).toLowerCase().includes(q);
  });

  list.innerHTML = matched.map(m => {
    const avatars = m.participants.slice(0, 4).map(p => {
      const sp = speakerById(p);
      return '<span class="avatar" style="background:' + sp.color + '" title="' + escapeHtml(sp.name) + '">' +
             escapeHtml(sp.short) + '</span>';
    }).join('');

    return '<li><button class="mtg" type="button" data-mid="' + m.id + '"' +
      (state.panelMeetingId === m.id ? ' aria-current="true"' : '') + '>' +
      '<span class="mtg-top"><span class="mtg-title">' + escapeHtml(m.title) + '</span>' +
      '<span class="mtg-dot' + (m.status === 'processing' ? ' processing' : '') + '"></span></span>' +
      '<span class="mtg-meta"><span>' + escapeHtml(m.date.slice(5)) + '</span>' +
      '<span class="sep">·</span><span>' + durationText(m.duration) + '</span>' +
      '<span class="sep">·</span><span class="avatars">' + avatars + '</span></span>' +
      '</button></li>';
  }).join('');

  $('#meetingCount').textContent = String(MEETINGS.length);
  $('#libEmpty').hidden = matched.length > 0;
}

/* ───────────── 渲染：欢迎态与示例问题 ───────────── */

const SUGGESTIONS = [
  { icon: 'search', title: '这场周会定了什么？', sub: '查询指定会议的结论与决议' },
  { icon: 'user',   title: 'C 一共答应了哪些事？', sub: '跨会议汇总某人的承诺与待办' },
  { icon: 'mic',    title: '谁提到了活动弹窗的问题？', sub: '定位具体发言人与原声时间点' },
  { icon: 'alert',  title: '这三场会有什么风险？', sub: '跨会议汇总未决事项与风险' }
];

function renderWelcome() {
  const cards = SUGGESTIONS.map((s, i) =>
    '<button class="suggest" type="button" data-q="' + escapeHtml(s.title) + '" style="animation-delay:' + (i * 70) + 'ms">' +
    ICON[s.icon] + '<span><b>' + escapeHtml(s.title) + '</b><small>' + escapeHtml(s.sub) + '</small></span></button>'
  ).join('');

  const node = document.createElement('div');
  node.className = 'welcome';
  node.innerHTML =
    '<span class="welcome-mark">' + ICON.spark + '</span>' +
    '<div><h2>把会议变成可追问的知识</h2>' +
    '<p>直接问，或上传一段会议录音。每条结论都会标注出处，点击引用可以回到原话核对。</p></div>' +
    '<div class="suggests"><span class="suggests-label">试试这些问题</span>' +
    '<div class="suggest-grid">' + cards + '</div></div>';
  return node;
}

/* ───────────── 渲染：对话流 ───────────── */

function scrollStream(force) {
  const box = $('#stream');
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 180;
  if (force || near) box.scrollTop = box.scrollHeight;
}

function appendMessage(msg) {
  const stream = $('#stream');
  $('#welcome')?.remove();

  const node = document.createElement('article');
  node.className = 'msg ' + msg.role;
  node.dataset.id = msg.id;

  const avatar = msg.role === 'user'
    ? '<span class="msg-avatar">' + ICON.user + '</span>'
    : '<span class="msg-avatar">' + ICON.spark + '</span>';

  node.innerHTML = avatar + '<div class="msg-body"><span class="msg-who">' +
    (msg.role === 'user' ? '你' : '纪要助手') + '</span>' +
    '<div class="bubble">' + (msg.html || '') + '</div></div>';

  stream.appendChild(node);
  scrollStream(true);
  return node;
}

function appendUserMessage(text) {
  const msg = { id: 'u' + Date.now(), role: 'user', html: '<p>' + escapeHtml(text) + '</p>' };
  state.messages.push(msg);
  appendMessage(msg);
}

/* ───────────── 渲染：思考中 ───────────── */

function appendThinking(label) {
  const node = document.createElement('article');
  node.className = 'msg assistant';
  node.dataset.thinking = '1';
  node.innerHTML = '<span class="msg-avatar">' + ICON.spark + '</span>' +
    '<div class="msg-body"><span class="msg-who">纪要助手</span>' +
    '<div class="bubble plain"><span class="thinking">' +
    '<span class="dots"><i></i><i></i><i></i></span>' + escapeHtml(label) + '</span></div></div>';
  $('#stream').appendChild(node);
  scrollStream(true);
  return node;
}

function removeThinking() {
  $$('[data-thinking="1"]').forEach(n => n.remove());
}

/* 逐字输出首段，随后展开结构化内容 */
function typeText(node, text, done) {
  const bubble = node.querySelector('.bubble');
  const target = document.createElement('p');
  const caret = document.createElement('span');
  caret.className = 'caret';
  bubble.appendChild(target);

  let i = 0;
  const step = Math.max(1, Math.round(text.length / 44));
  const timer = setInterval(() => {
    i = Math.min(text.length, i + step);
    target.textContent = text.slice(0, i);
    if (i < text.length) target.appendChild(caret);
    scrollStream();
    if (i >= text.length) {
      clearInterval(timer);
      caret.remove();
      done();
    }
  }, 24);
}
/* =========================================================
   回答引擎
   模拟正式版中「意图路由 → 工具调用 → 证据装配 → 生成回答」的完整链路，
   所有数字与引用均从 mock 数据实时计算，便于核对。
   ========================================================= */

const STOP = new Set(['什么','怎么','哪些','这个','那个','一下','帮我','请问','我们','他们',
  '会议','说了','了吗','的会','这批','几场','有没','没有','是不','不是','以及','关于',
  '今天','明天','昨天','中午','上午','下午','之前','之后','时候','现在','本周','上周','下周']);

function extractTerms(q) {
  const cleaned = q.replace(/[，。？！、,.?!\s“”"'（）()]/g, '');
  const terms = new Set();
  for (let i = 0; i < cleaned.length - 1; i++) {
    const g = cleaned.slice(i, i + 2);
    if (!STOP.has(g)) terms.add(g);
  }
  return Array.from(terms);
}

/* 检索层：片段级打分（正式版为「关键词 + 向量」混合检索） */
function searchSegments(q, meetings) {
  const terms = extractTerms(q);
  const hits = [];
  meetings.forEach(m => m.segments.forEach(seg => {
    let score = 0;
    terms.forEach(t => { if (seg.text.includes(t)) score += 1; });
    if (score > 0) hits.push({ m, seg, score });
  }));
  return hits.sort((a, b) => b.score - a.score);
}

/* 作用域解析：识别问题里提到的会议；无法唯一确定时反问 */
function resolveScope(q) {
  const mentioned = MEETINGS.filter(m => {
    const keys = [m.title.replace(/[（(].*?[)）]/g, '')];
    return keys.some(k => {
      for (let i = 0; i < k.length - 1; i++) {
        if (q.includes(k.slice(i, i + 2)) && !STOP.has(k.slice(i, i + 2))) return true;
      }
      return false;
    });
  });

  if (mentioned.length === 1) return { meetings: [mentioned[0]], narrowed: true };
  if (mentioned.length > 1) return { meetings: mentioned, ambiguous: true };
  if (state.activeId) return { meetings: [meetingById(state.activeId)], narrowed: false };
  return { meetings: MEETINGS, narrowed: false };
}

function cite(mid, sid) { return '[[' + mid + ':' + sid + ']]'; }

function citeRow(mid, ids) {
  if (!ids || !ids.length) return '';
  return renderRich(ids.map(id => cite(mid, id)).join(''));
}

function scopeLabel(meetings) {
  return meetings.length === 1 ? '《' + meetings[0].title + '》' : '全部 ' + meetings.length + ' 场会议';
}

/* ── 各类回答 ── */

function answerDecisions(meetings) {
  const rows = [];
  let n = 0;
  meetings.forEach(m => m.minutes.decisions.forEach(d => { rows.push({ m, d }); n++; }));
  if (!n) return notFound(meetings);

  const blocks = rows.map(({ m, d }) => ({
    title: m.title,
    text: d.content,
    cites: citeRow(m.id, d.evidence),
    meta: Math.round(d.confidence * 100) + '%'
  }));

  return {
    lead: scopeLabel(meetings) + '一共确认了 ' + n + ' 项决议，下面是逐条结论与出处。',
    blocks: [{ type: 'quote', title: '已确认的决议', items: blocks }]
  };
}

function answerActions(meetings) {
  const rows = [];
  meetings.forEach(m => m.minutes.action_items.forEach(a => rows.push({ m, a })));
  if (!rows.length) return notFound(meetings);

  const pending = rows.filter(r => !state.resolved.has(r.m.id + ':' + r.a.id));
  const blocks = pending.map(({ m, a }) => {
    const owner = a.owner ? speakerById(a.owner).name : '未指定负责人';
    const due = a.due || a.deadline || '未定时间';
    return {
      title: owner + ' · ' + due,
      text: a.task,
      cites: citeRow(m.id, a.evidence),
      warn: a.needs_review
    };
  });

  const warn = pending.filter(r => r.a.needs_review).length;
  return {
    lead: scopeLabel(meetings) + '共 ' + rows.length + ' 项待办，其中 ' + warn +
          ' 项因负责人或时间不明确而标记为待确认。',
    blocks: [{ type: 'quote', title: '待办清单', items: blocks }]
  };
}

function answerRisks(meetings) {
  const rows = [];
  meetings.forEach(m => m.minutes.risks.forEach(r => rows.push({ m, r })));
  if (!rows.length) return notFound(meetings);

  return {
    lead: scopeLabel(meetings) + '共记录了 ' + rows.length + ' 条风险，均来自与会者的原话。',
    blocks: [{ type: 'quote', title: '风险与预警', items: rows.map(({ m, r }) => ({
      title: m.title, text: r.content, cites: citeRow(m.id, r.evidence)
    })) }]
  };
}

function answerOpen(meetings) {
  const rows = [];
  meetings.forEach(m => {
    m.minutes.open_questions.forEach(q => rows.push({ m, text: q.content, cites: citeRow(m.id, q.evidence) }));
    m.minutes.action_items.filter(a => a.needs_review).forEach(a => rows.push({
      m, text: (a.task) + '（' + (a.review_reason || '信息不完整') + '）', cites: citeRow(m.id, a.evidence), warn: true
    }));
  });
  if (!rows.length) return {
    lead: scopeLabel(meetings) + '没有找到未决事项。',
    blocks: []
  };

  return {
    lead: '共找到 ' + rows.length + ' 项尚未闭环的内容，建议在下次会议确认。',
    blocks: [{ type: 'quote', title: '待确认与未决事项', items: rows }]
  };
}

function answerSpeakerTasks(sp, meetings) {
  const rows = [];
  meetings.forEach(m => m.minutes.action_items.forEach(a => { if (a.owner === sp.id) rows.push({ m, a }); }));
  if (!rows.length) {
    return {
      lead: '在' + scopeLabel(meetings) + '中没有找到由' + sp.name + '承担的待办事项。',
      blocks: []
    };
  }

  const speaking = meetings.reduce((sum, m) => {
    const own = m.segments.filter(s => s.speaker === sp.id);
    return sum + own.reduce((x, s) => x + (s.end - s.start), 0);
  }, 0);

  const open = rows.filter(({ m, a }) => !state.resolved.has(m.id + ':' + a.id));
  return {
    lead: sp.name + '（' + sp.role + '）在' + scopeLabel(meetings) + '中共承担 ' + rows.length +
          ' 项待办，累计发言约 ' + Math.round(speaking / 60) + ' 分钟。',
    blocks: [{
      type: 'quote',
      title: '待办明细',
      items: open.map(({ m, a }) => ({
        title: m.title + ' · ' + (a.due || a.deadline || '时间未定'),
        text: a.task,
        cites: citeRow(m.id, a.evidence),
        warn: a.needs_review
      }))
    }]
  };
}

function answerSpeakerSpeech(sp, meetings) {
  const rows = [];
  meetings.forEach(m => m.segments.forEach(s => { if (s.speaker === sp.id) rows.push({ m, s }); }));
  if (!rows.length) return notFound(meetings);

  const total = rows.reduce((x, { s }) => x + (s.end - s.start), 0);
  const top = rows.slice().sort((a, b) => (b.s.end - b.s.start) - (a.s.end - a.s.start)).slice(0, 3);

  return {
    lead: sp.name + '在' + scopeLabel(meetings) + '中共发言 ' + rows.length + ' 次，累计约 ' +
          Math.round(total / 60) + ' 分钟。以下是发言最长的三段。',
    blocks: [{ type: 'quote', title: '主要发言', items: top.map(({ m, s }) => ({
      title: m.title + ' · ' + mmss(s.start),
      text: s.text.length > 90 ? s.text.slice(0, 90) + '…' : s.text,
      cites: citeRow(m.id, [s.id])
    })) }]
  };
}

function answerWho(q, meetings) {
  const hits = searchSegments(q, meetings).slice(0, 3);
  if (!hits.length || hits[0].score < 2) return notFound(meetings);

  const top = hits[0];
  const sp = speakerById(top.seg.speaker);
  const others = hits.slice(1).filter(h => h.seg.speaker !== top.seg.speaker);

  return {
    lead: '这个问题主要是 ' + sp.name + '（' + sp.role + '）在《' + top.m.title + '》' +
          mmss(top.seg.start) + ' 提到的。',
    blocks: [{
      type: 'quote',
      title: '相关原话',
      items: hits.map(({ m, seg }) => ({
        title: speakerById(seg.speaker).name + ' · ' + m.title + ' ' + mmss(seg.start),
        text: seg.text.length > 110 ? seg.text.slice(0, 110) + '…' : seg.text,
        cites: citeRow(m.id, [seg.id])
      }))
    }],
    footnote: others.length ? '另有 ' + others.length + ' 处相关发言，可点击引用核对。' : ''
  };
}

function answerContent(q, meetings) {
  const hits = searchSegments(q, meetings).slice(0, 4);
  if (!hits.length || hits[0].score < 2) return notFound(meetings);

  const byMeeting = new Map();
  hits.forEach(h => {
    if (!byMeeting.has(h.m.id)) byMeeting.set(h.m.id, []);
    byMeeting.get(h.m.id).push(h);
  });

  const items = [];
  byMeeting.forEach((list, mid) => {
    const m = meetingById(mid);
    const best = list[0];
    items.push({
      title: m.title + ' · ' + speakerById(best.seg.speaker).name + ' ' + mmss(best.seg.start),
      text: best.seg.text.length > 120 ? best.seg.text.slice(0, 120) + '…' : best.seg.text,
      cites: citeRow(mid, list.map(x => x.seg.id))
    });
  });

  return {
    lead: '在' + scopeLabel(meetings) + '中找到 ' + hits.length + ' 处相关内容，最相关的是下面几条。',
    blocks: [{ type: 'quote', title: '相关片段', items }]
  };
}

/* 检索不到时不编造 */
function notFound(meetings) {
  return {
    lead: '在' + scopeLabel(meetings) + '中没有找到能支撑答案的记录。',
    blocks: [{
      type: 'note',
      text: '我不会基于没有出处的信息作答。可以换个说法再问，或先上传相关会议的录音。'
    }]
  };
}

/* 澄清追问：范围不唯一时不猜 */
function clarify(meetings) {
  return {
    lead: '你指的是下面哪一场会议？确认之后我再具体回答。',
    blocks: [{
      type: 'quote',
      title: '可选会议',
      items: meetings.map(m => ({
        title: m.title + ' · ' + m.date,
        text: m.participants.map(p => speakerById(p).name).join('、'),
        cites: ''
      }))
    }]
  };
}
/* ───────────── 意图路由（模拟 Agent 的分派）───────────── */

function buildAnswer(q) {
  const scope = resolveScope(q);
  const meetings = scope.meetings;
  const sp = Object.values(SPEAKERS).find(s => q.includes(s.name));

  if (/^(总结|汇总|概括|梳理)/.test(q) && meetings.length > 1 && !scope.narrowed) return clarify(meetings);

  if (sp && /(答应|承诺|负责|待办|做了哪些|哪些事|任务|跟进)/.test(q)) return answerSpeakerTasks(sp, meetings);
  if (sp) return answerSpeakerSpeech(sp, meetings);

  if (/谁(提|说|讲|负责|跟|来|发现|建议)/.test(q)) return answerWho(q, meetings);
  if (/(风险|隐患)/.test(q)) return answerRisks(meetings);
  if (/(没定|未定|待确认|悬而未决|没结论|没闭环|遗留)/.test(q)) return answerOpen(meetings);
  if (/(待办|要做什么|任务|谁要|跟进)/.test(q)) return answerActions(meetings);
  if (/(定了什么|决议|结论|拍板|确定|通过)/.test(q)) return answerDecisions(meetings);
  return answerContent(q, meetings);
}

function stripMarkers(text) {
  return String(text).replace(/\[\[[\w]+:[\w]+\]\]/g, '');
}

/* ───────────── 回答渲染 ───────────── */

function itemHtml(it) {
  let inner = '';
  if (it.title) inner += '<b>' + escapeHtml(it.title) + '</b>';
  if (it.text) inner += '<span class="ans-text">' + escapeHtml(it.text) + '</span>';

  const meta = [];
  if (it.cites) meta.push(it.cites);
  if (it.warn) meta.push('<span class="pill warn">' + ICON.alert + '待确认</span>');
  if (it.meta) meta.push('<span class="conf">置信度 ' + escapeHtml(it.meta) + '</span>');
  if (meta.length) inner += '<span class="ans-meta">' + meta.join('') + '</span>';

  return '<li><span class="ans-item">' + inner + '</span></li>';
}

function answerHtml(res) {
  let html = '<p>' + renderRich(res.lead) + '</p>';

  (res.blocks || []).forEach(b => {
    if (b.type === 'note') {
      html += '<div class="answer-block"><p>' + escapeHtml(b.text) + '</p></div>';
      return;
    }
    if (!b.items || !b.items.length) return;
    html += '<div class="answer-block"><h4>' + escapeHtml(b.title) + '</h4><ol>' +
            b.items.map(itemHtml).join('') + '</ol></div>';
  });

  if (res.footnote) html += '<p class="ans-foot">' + escapeHtml(res.footnote) + '</p>';
  return html;
}

function bindCites(root) {
  $$('.cite', root).forEach(btn => {
    btn.addEventListener('click', () => openSegment(btn.dataset.mid, btn.dataset.sid));
  });
}

/* ───────────── 渲染：详情面板 ───────────── */

function setTab(tab) {
  state.tab = tab;
  $$('#tabs .tab').forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === tab)));
  renderPanel();
}

function renderPanel() {
  const m = meetingById(state.panelMeetingId);
  const body = $('#panelBody');
  const title = $('#panelTitle');
  const sub = $('#panelSub');

  if (!m) {
    title.textContent = '会议详情';
    sub.textContent = '从左侧会议库选择一场会议';
    body.innerHTML = '<div class="panel-empty">' + ICON.empty + '<p>还没有选中会议</p></div>';
    return;
  }

  title.textContent = m.title;
  sub.textContent = m.date + ' ' + m.time + ' · ' + durationText(m.duration) + ' · ' +
                    m.participants.length + ' 人 · ' + m.segments.length + ' 个发言片段';

  const review = m.minutes.action_items.filter(a =>
    a.needs_review && !state.dismissed.has(m.id + ':' + a.id));

  const reviewTab = $('#tabs .tab[data-tab="review"]');
  reviewTab.innerHTML = '待确认' + (review.length ? '<span class="badge">' + review.length + '</span>' : '');

  if (state.tab === 'minutes') body.innerHTML = renderMinutes(m);
  else if (state.tab === 'actions') body.innerHTML = renderActions(m);
  else if (state.tab === 'review') body.innerHTML = renderReview(m, review);
  else body.innerHTML = renderRaw(m);

  bindCites(body);
  bindPanelActions(m);
  return m;
}

function renderMinutes(m) {
  const summary = m.minutes.summary.map((s, i) =>
    '<div class="card"><div class="card-title"><span class="idx">' + (i + 1) + '</span>' + escapeHtml(s.topic) + '</div>' +
    '<ul class="card-points">' + s.points.map(p => '<li>' + escapeHtml(p) + '</li>').join('') + '</ul>' +
    '<div class="card-foot">' + citeRow(m.id, s.evidence) + '</div></div>'
  ).join('');

  const decisions = m.minutes.decisions.map(d =>
    '<div class="card"><div class="card-title">' + escapeHtml(d.content) + '</div>' +
    '<div class="card-foot">' + citeRow(m.id, d.evidence) +
    '<span class="conf">置信度 ' + Math.round(d.confidence * 100) + '%</span></div></div>'
  ).join('');

  const questions = m.minutes.open_questions.map(q =>
    '<div class="card"><div class="card-title">' + escapeHtml(q.content) + '</div>' +
    '<div class="card-foot">' + citeRow(m.id, q.evidence) + '</div></div>'
  ).join('');

  const risks = m.minutes.risks.map(r =>
    '<div class="card"><div class="card-title"><span class="pill warn">' + ICON.alert + '风险</span></div>' +
    '<ul class="card-points"><li>' + escapeHtml(r.content) + '</li></ul>' +
    '<div class="card-foot">' + citeRow(m.id, r.evidence) + '</div></div>'
  ).join('');

  return section('摘要', m.minutes.summary.length, summary) +
         section('决议', m.minutes.decisions.length, decisions) +
         section('待确认问题', m.minutes.open_questions.length, questions) +
         section('风险', m.minutes.risks.length, risks);
}

function section(name, count, inner) {
  return '<div class="sect"><div class="sect-head"><h3>' + name + '</h3>' +
         '<span class="count">' + count + '</span></div>' + inner + '</div>';
}

function renderActions(m) {
  const rows = m.minutes.action_items.map(a => {
    const key = m.id + ':' + a.id;
    const done = state.resolved.has(key);
    const owner = a.owner
      ? '<span class="who"><span class="dot" style="background:' + speakerById(a.owner).color + '"></span>' +
        escapeHtml(speakerById(a.owner).name) + '</span>'
      : '<span class="pill warn">' + ICON.alert + '负责人未指定</span>';
    const due = a.due
      ? '<span class="pill">' + ICON.cal + escapeHtml(a.due) + '</span>'
      : (a.deadline ? '<span class="pill">' + ICON.clock + escapeHtml(a.deadline) + '</span>'
                    : '<span class="pill warn">' + ICON.clock + '时间未定</span>');

    return '<div class="task' + (done ? ' done' : '') + '" data-key="' + key + '">' +
      '<button class="tickbox" type="button" data-toggle="' + key + '" aria-pressed="' + done + '" aria-label="标记完成">' + ICON.check + '</button>' +
      '<div class="task-main"><span class="task-text">' + escapeHtml(a.task) + '</span>' +
      '<span class="task-meta">' + owner + due +
      (a.needs_review ? '<span class="pill warn">' + ICON.alert + '待确认</span>' : '') + '</span>' +
      '<span class="ans-meta">' + citeRow(m.id, a.evidence) + '</span></div></div>';
  }).join('');

  return section('待办清单', m.minutes.action_items.length, rows);
}

function renderReview(m, review) {
  if (!review.length) {
    return '<div class="panel-empty">' + ICON.check +
      '<p>没有待确认的条目<br><small>所有内容都已通过置信度校验</small></p></div>';
  }

  return section('需要人工确认', review.length, review.map(a => {
    const key = m.id + ':' + a.id;
    const owner = a.owner
      ? '<span class="who"><span class="dot" style="background:' + speakerById(a.owner).color + '"></span>' +
        escapeHtml(speakerById(a.owner).name) + '</span>'
      : '<span class="pill warn">' + ICON.alert + '负责人未指定</span>';

    return '<div class="card" data-review="' + key + '">' +
      '<ul class="card-points"><li>' + escapeHtml(a.task) + '</li></ul>' +
      '<p class="review-why">' + ICON.help + escapeHtml(a.review_reason || '信息不完整') + '</p>' +
      '<div class="card-foot">' + owner + citeRow(m.id, a.evidence) + '</div>' +
      '<div class="review-actions">' +
      '<button class="btn btn-ghost" type="button" data-dismiss="' + key + '">忽略</button>' +
      '<button class="btn btn-primary" type="button" data-confirm="' + key + '">' + ICON.check + '确认通过</button>' +
      '</div></div>';
  }).join(''));
}

function renderRaw(m) {
  const turns = m.segments.map(s => {
    const sp = speakerById(s.speaker);
    const low = s.confidence < 0.93;
    return '<div class="turn' + (low ? ' low' : '') + '" id="seg-' + s.id + '" data-sid="' + s.id + '">' +
      '<div class="turn-side"><span class="turn-time">' + mmss(s.start) + ' – ' + mmss(s.end) + '</span>' +
      '<span class="turn-who" style="color:' + sp.color + '">' + escapeHtml(sp.name) + '</span></div>' +
      '<div class="turn-text">' + escapeHtml(s.text) + '</div></div>';
  }).join('');

  return '<p class="raw-note">完整转写 · 点击回答中的引用会跳转并高亮对应片段。带 ? 标记的片段声纹置信度较低。</p>' + turns;
}

function bindPanelActions(m) {
  $$('[data-toggle]', $('#panelBody')).forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.toggle;
    if (state.resolved.has(key)) state.resolved.delete(key); else state.resolved.add(key);
    renderPanel();
    toast(state.resolved.has(key) ? '已标记为完成' : '已恢复为待处理', 'ok');
  }));

  $$('[data-confirm]', $('#panelBody')).forEach(btn => btn.addEventListener('click', () => {
    const key = btn.dataset.confirm;
    state.dismissed.add(key);
    state.resolved.delete(key);
    const m2 = meetingById(key.split(':')[0]);
    const item = m2.minutes.action_items.find(a => a.id === key.split(':')[1]);
    item.needs_review = false;
    item.owner_source = item.owner_source === 'missing' ? 'confirmed' : item.owner_source;
    renderPanel();
    toast('已确认，条目已并入最终纪要', 'ok');
  }));

  $$('[data-dismiss]', $('#panelBody')).forEach(btn => btn.addEventListener('click', () => {
    state.dismissed.add(btn.dataset.dismiss);
    renderPanel();
    toast('已忽略该条目');
  }));
}

/* 证据回跳 */
function openSegment(mid, sid) {
  const m = meetingById(mid);
  if (!m) return;
  state.panelMeetingId = mid;
  openPanel();
  setTab('raw');
  const node = $('#seg-' + sid);
  if (!node) return;
  $$('.turn.hit').forEach(n => n.classList.remove('hit'));
  node.classList.add('hit');
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => node.classList.remove('hit'), 2600);
}
/* ───────────── 上传音频：模拟处理流水线 ───────────── */

const STAGES = [
  '音频标准化 · 16kHz 单声道',
  '语音转写 · WhisperX',
  '声纹识别与身份对齐 · ECAPA-TDNN',
  '生成结构化纪要 · Agent'
];

const wait = ms => new Promise(r => setTimeout(r, ms));

function stepsHtml(active) {
  return STAGES.map((label, i) => {
    const cls = i < active ? 'done' : (i === active ? 'active' : '');
    const mark = i < active
      ? '<span class="tick">' + ICON.check + '</span>'
      : (i === active ? '<span class="spin"></span>'
                      : '<span class="tick" style="opacity:.22">' + ICON.check + '</span>');
    return '<div class="step ' + cls + '">' + mark + escapeHtml(label) + '</div>';
  }).join('');
}

function jobHtml(file) {
  return '<div class="job"><div class="job-head">' +
    '<span class="msg-avatar" style="background:linear-gradient(140deg,var(--sp-3),var(--accent))">' + ICON.wave + '</span>' +
    '<span class="job-file"><b>' + escapeHtml(file.name) + '</b>' +
    '<small>' + fileSize(file.size) + ' · 模拟处理中</small></span>' +
    '<span class="job-pct">0%</span></div>' +
    '<div class="job-bar"><i></i></div>' +
    '<div class="job-steps">' + stepsHtml(0) + '</div></div>';
}

async function processAudio(file) {
  const name = file.name || '会议录音.mp3';
  const size = file.size || 12840000;

  appendUserMessage('上传会议录音：' + name + '（' + fileSize(size) + '）');
  const node = appendMessage({ id: 'job' + Date.now(), role: 'assistant', html: jobHtml({ name, size }) });
  const bar = node.querySelector('.job-bar i');
  const pct = node.querySelector('.job-pct');
  const steps = node.querySelector('.job-steps');

  for (let i = 0; i < STAGES.length; i++) {
    steps.innerHTML = stepsHtml(i);
    for (let k = 0; k <= 100; k += 20) {
      bar.style.width = ((i * 100 + k) / STAGES.length) + '%';
      pct.textContent = Math.round((i * 100 + k) / STAGES.length) + '%';
      await wait(90);
    }
  }

  bar.style.width = '100%';
  pct.textContent = '100%';
  steps.innerHTML = stepsHtml(STAGES.length);

  const m = makeMeetingFromUpload(name);
  MEETINGS.unshift(m);
  renderMeetingList();

  const review = m.minutes.action_items.filter(a => a.needs_review).length;
  const lead = '《' + m.title + '》处理完成。时长 ' + durationText(m.duration) + '，识别出 ' +
    m.participants.length + ' 位参会人、' + m.segments.length + ' 个发言片段，已生成摘要 ' +
    m.minutes.summary.length + ' 条、决议 ' + m.minutes.decisions.length + ' 项、待办 ' +
    m.minutes.action_items.length + ' 项' + (review ? '，其中 ' + review + ' 项需要你确认。' : '。');

  const res = {
    lead: lead,
    blocks: [{
      type: 'quote',
      title: '会议摘要',
      items: m.minutes.summary.map(s => ({
        title: s.topic,
        text: s.points.join('；'),
        cites: citeRow(m.id, s.evidence)
      }))
    }]
  };

  appendMessage({ id: 'a' + Date.now(), role: 'assistant', html: '', plain: stripMarkers(res.lead) });
  const answerNode = $('#stream').lastElementChild;
  await wait(260);
  answerNode.querySelector('.bubble').innerHTML = answerHtml(res);
  bindCites(answerNode);
  scrollStream(true);

  state.panelMeetingId = m.id;
  state.tab = review ? 'review' : 'minutes';
  renderPanel();
  openPanel();
  toast('纪要已生成：' + m.title, 'ok');
  state.busy = false;
  updateSendState();
}

/* 上传后的会议内容模板（真实场景由感知层与 Agent 生成） */
function makeMeetingFromUpload(fileName) {
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const date = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
  const title = (fileName || '会议录音').replace(/\.[^.]+$/, '').slice(0, 24);

  return {
    id: 'm_up_' + Date.now(),
    title: title, date: date, time: pad(now.getHours()) + ':' + pad(now.getMinutes()),
    duration: 1265, status: 'processed', audio: fileName,
    participants: ['p_zhao', 'p_chen', 'p_wang', 'p_li'],

    segments: [
      { id: 's1', start: 5,    end: 58,   speaker: 'p_zhao', confidence: 0.96,
        text: '今天对齐一下双周进度，重点看支付模块这块。' },
      { id: 's2', start: 58,   end: 240,  speaker: 'p_chen', confidence: 0.93,
        text: '支付这边用户反馈有一个比较集中的问题，退款到账时间太长，平均要三天。' },
      { id: 's3', start: 240,  end: 520,  speaker: 'p_wang', confidence: 0.94,
        text: '这个是因为退款走的是定时任务批处理，一天只跑一次。如果改成实时回调，当天就能到账，开发量大概三天。' },
      { id: 's4', start: 520,  end: 700,  speaker: 'p_zhao', confidence: 0.97,
        text: '三天可以做，那就改。B 你那边配合出一版退款状态的展示文案。' },
      { id: 's5', start: 700,  end: 800,  speaker: 'p_li',   confidence: 0.95,
        text: '可以，我明天之前给。' },
      { id: 's6', start: 800,  end: 1080, speaker: 'p_zhao', confidence: 0.96,
        text: '好。另外提醒一下，这次改动涉及资金链路，上线前必须过一轮完整的回归测试，这个不能省。' }
    ],

    minutes: {
      summary: [
        { topic: '退款到账时长问题', evidence: ['s2', 's3'],
          points: [
            '用户反馈退款平均 3 天到账',
            '根因是退款走定时任务批处理，一天执行一次',
            '改为实时回调可当天到账，开发量约 3 天'
          ] },
        { topic: '后续动作', evidence: ['s4', 's5', 's6'],
          points: [
            '退款链路改为实时回调',
            '设计侧补充退款状态展示文案',
            '上线前必须完成资金链路全量回归测试'
          ] }
      ],
      decisions: [
        { content: '退款到账由定时批处理改为实时回调方案，开发量按 3 天评估。', evidence: ['s3', 's4'], confidence: 0.93 }
      ],
      action_items: [
        { id: 'a1', task: '将退款到账改造为实时回调（约 3 天）', owner: 'p_wang', owner_source: 'explicit',
          due: null, deadline: null, status: 'pending', evidence: ['s3', 's4'], needs_review: true,
          review_reason: '会上未约定具体的交付日期' },
        { id: 'a2', task: '输出退款状态展示文案', owner: 'p_li', owner_source: 'explicit',
          due: null, deadline: '明天之前', status: 'pending', evidence: ['s4', 's5'], needs_review: false },
        { id: 'a3', task: '上线前完成资金链路全量回归测试', owner: null, owner_source: 'missing',
          due: null, deadline: null, status: 'pending', evidence: ['s6'], needs_review: true,
          review_reason: '会上强调必须执行，但未指定负责人' }
      ],
      open_questions: [
        { content: '退款链路改造的提测时间未明确。', evidence: ['s6'] }
      ],
      risks: [
        { content: '改动涉及资金链路，若上线前未完成全量回归测试，存在资损风险。', evidence: ['s6'] }
      ]
    }
  };
}
/* ───────────── 导出（浏览器侧真实生成文件）───────────── */

function download(filename, text, mime) {
  const blob = new Blob([text], { type: mime + ';charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function ownerName(a) { return a.owner ? speakerById(a.owner).name : '未指定'; }
function dueText(a) { return a.due || a.deadline || '未定'; }

function minutesMarkdown(m) {
  const L = [];
  L.push('# ' + m.title);
  L.push('');
  L.push('- 日期：' + m.date + ' ' + m.time);
  L.push('- 时长：' + durationText(m.duration));
  L.push('- 参会人：' + m.participants.map(p => speakerById(p).name).join('、'));
  L.push('');
  L.push('## 摘要');
  m.minutes.summary.forEach((s, i) => {
    L.push('### ' + (i + 1) + '. ' + s.topic);
    s.points.forEach(p => L.push('- ' + p));
    L.push('');
  });
  L.push('## 决议');
  m.minutes.decisions.forEach(d => L.push('- ' + d.content));
  L.push('');
  L.push('## 待办');
  L.push('| 事项 | 负责人 | 截止 | 状态 |');
  L.push('| --- | --- | --- | --- |');
  m.minutes.action_items.forEach(a => L.push('| ' + a.task + ' | ' + ownerName(a) + ' | ' + dueText(a) +
    ' | ' + (state.resolved.has(m.id + ':' + a.id) ? '已完成' : '待处理') + ' |'));
  L.push('');
  if (m.minutes.open_questions.length) {
    L.push('## 待确认问题');
    m.minutes.open_questions.forEach(q => L.push('- ' + q.content));
    L.push('');
  }
  L.push('## 风险');
  m.minutes.risks.forEach(r => L.push('- ' + r.content));
  L.push('');
  L.push('---');
  L.push('> 由「会议纪要助手」生成。每条结论均绑定原声片段，可在应用内点击引用核对。');
  return L.join('\n');
}

function actionsCsv(m) {
  const esc = v => '"' + String(v).replace(/"/g, '""') + '"';
  const rows = [['事项', '负责人', '截止时间', '状态', '是否待确认', '来源片段']];
  m.minutes.action_items.forEach(a => rows.push([
    a.task, ownerName(a), dueText(a),
    state.resolved.has(m.id + ':' + a.id) ? '已完成' : '待处理',
    a.needs_review ? '是' : '否',
    a.evidence.join(' ')
  ]));
  return '\ufeff' + rows.map(r => r.map(esc).join(',')).join('\r\n');
}

function conversationMarkdown() {
  const L = ['# 对话记录', '', '导出时间：' + new Date().toLocaleString('zh-CN'), ''];
  state.messages.forEach(msg => {
    if (!msg.plain) return;
    L.push(msg.role === 'user' ? '**我：** ' + msg.plain : '**纪要助手：** ' + msg.plain.replace(/\n/g, '\n\n'));
    L.push('');
  });
  if (L.length <= 4) L.push('（本次会话还没有对话内容）');
  return L.join('\n');
}

function doExport(kind) {
  const m = meetingById(state.panelMeetingId);
  if (kind === 'minutes') {
    if (!m) return toast('请先在左侧选择一场会议');
    download(m.title + '_纪要.md', minutesMarkdown(m), 'text/markdown');
    toast('已导出：' + m.title + '_纪要.md', 'ok');
  } else if (kind === 'csv') {
    if (!m) return toast('请先在左侧选择一场会议');
    download(m.title + '_待办.csv', actionsCsv(m), 'text/csv');
    toast('已导出：' + m.title + '_待办.csv', 'ok');
  } else {
    download('对话记录.md', conversationMarkdown(), 'text/markdown');
    toast('已导出：对话记录.md', 'ok');
  }
}

function toggleExportMenu() {
  const existing = $('#exportMenu');
  if (existing) { existing.remove(); return; }

  const m = meetingById(state.panelMeetingId);
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.id = 'exportMenu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML =
    '<button class="menu-item" type="button" role="menuitem" data-export="minutes">' + ICON.doc +
      '<span><b>纪要 Markdown</b><small>' + escapeHtml(m ? m.title : '当前会议') + '</small></span></button>' +
    '<button class="menu-item" type="button" role="menuitem" data-export="csv">' + ICON.table +
      '<span><b>待办 CSV</b><small>可导入项目管理工具</small></span></button>' +
    '<button class="menu-item" type="button" role="menuitem" data-export="chat">' + ICON.chat +
      '<span><b>对话记录</b><small>保留问答与引用来源</small></span></button>';

  document.body.appendChild(menu);
  const r = $('#exportBtn').getBoundingClientRect();
  menu.style.top = (r.bottom + 8) + 'px';
  menu.style.right = Math.max(12, window.innerWidth - r.right) + 'px';

  menu.addEventListener('click', e => {
    const btn = e.target.closest('[data-export]');
    if (!btn) return;
    doExport(btn.dataset.export);
    menu.remove();
  });
}

/* ───────────── 发送流程 ───────────── */

function updateSendState() {
  const hasText = $('#input').value.trim().length > 0;
  $('#sendBtn').disabled = state.busy || (!hasText && !state.pendingFile);
  $('#attachBtn').disabled = state.busy;
}

function blocksText(res) {
  const parts = [];
  (res.blocks || []).forEach(b => {
    if (b.type === 'note') { parts.push(b.text); return; }
    if (!b.items) return;
    parts.push('【' + b.title + '】');
    b.items.forEach(it => parts.push('- ' + [it.title, it.text].filter(Boolean).join('：')));
  });
  return parts.join('\n');
}

async function send() {
  if (state.busy) return;
  const input = $('#input');
  const text = input.value.trim();

  if (!text && state.pendingFile) {
    const file = state.pendingFile;
    clearAttachment();
    state.busy = true;
    updateSendState();
    processAudio(file);
    return;
  }
  if (!text) return;

  input.value = '';
  autoGrow();
  appendUserMessage(text);

  state.busy = true;
  updateSendState();

  appendThinking('正在检索会议记录并核对证据…');
  await wait(560);
  const res = buildAnswer(text);
  await wait(200);
  removeThinking();

  const node = appendMessage({ id: 'a' + Date.now(), role: 'assistant', html: '' });
  const lead = stripMarkers(res.lead);
  state.messages.push({ id: node.dataset.id, role: 'assistant', plain: lead + '\n' + blocksText(res) });

  typeText(node, lead, () => {
    node.querySelector('.bubble').innerHTML = answerHtml(res);
    bindCites(node);
    state.busy = false;
    updateSendState();
    scrollStream();
  });
}

/* ───────────── 交互辅助 ───────────── */

function autoGrow() {
  const el = $('#input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 168) + 'px';
}

function clearAttachment() {
  state.pendingFile = null;
  $('#attachChip').hidden = true;
  $('#fileInput').value = '';
  updateSendState();
}

function attachFile(file) {
  if (!file) return;
  state.pendingFile = { name: file.name, size: file.size };
  $('#attachName').textContent = file.name;
  $('#attachSize').textContent = fileSize(file.size);
  $('#attachChip').hidden = false;
  updateSendState();
  toast('已选择音频，发送后开始处理');
}

function setScope(mid) {
  state.activeId = mid;
  const m = meetingById(mid);
  const title = $('#chatTitle');
  const sub = $('#chatSubtitle');

  if (!m) {
    title.textContent = '全局对话';
    sub.textContent = '可查询任意会议，或上传录音生成纪要';
  } else {
    title.textContent = '正在就《' + m.title + '》对话';
    sub.textContent = m.date + ' · 提问范围已限定在本场会议，点右侧 × 可切回全局';
  }
  updateSendState();
}

function openPanel() {
  $('#panel').hidden = false;
  state.panelOpen = true;
  if (window.innerWidth <= 1080) $('#scrim').hidden = false;
  else $('.app').classList.remove('panel-closed');
}

function closePanel() {
  $('#panel').hidden = true;
  $('#scrim').hidden = true;
  $('#exportMenu')?.remove();
  if (window.innerWidth > 1080) $('.app').classList.add('panel-closed');
  state.panelOpen = false;
}

function newChat() {
  state.messages = [];
  $('#stream').innerHTML = '';
  const welcome = renderWelcome();
  welcome.id = 'welcome';
  $('#stream').appendChild(welcome);
  setScope(null);
  toast('已开始新的对话');
}
function setTheme(theme) {
  state.theme = theme;
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('mna-theme', theme); } catch (e) { /* file:// 下可能不可用 */ }
}

/* ───────────── 事件绑定与启动 ───────────── */

function init() {
  let saved = null;
  try { saved = localStorage.getItem('mna-theme'); } catch (e) { /* 忽略 */ }
  setTheme(saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  renderMeetingList();
  const welcome = renderWelcome();
  welcome.id = 'welcome';
  $('#stream').appendChild(welcome);
  setScope(null);
  renderPanel();
  openPanel();
  updateSendState();

  /* 输入与发送 */
  $('#composerForm').addEventListener('submit', e => { e.preventDefault(); send(); });
  $('#input').addEventListener('input', () => { autoGrow(); updateSendState(); });
  $('#input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });

  /* 音频上传 */
  $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', e => attachFile(e.target.files[0]));
  $('#attachRemove').addEventListener('click', clearAttachment);

  /* 顶栏与侧栏 */
  $('#newChatBtn').addEventListener('click', newChat);
  $('#themeBtn').addEventListener('click', () => setTheme(state.theme === 'dark' ? 'light' : 'dark'));
  $('#exportBtn').addEventListener('click', toggleExportMenu);
  $('#panelClose').addEventListener('click', closePanel);
  $('#speakerBtn').addEventListener('click', () => toast('发言人库在正式版中用于注册与管理声纹'));

  /* 会议库 */
  $('#meetingSearch').addEventListener('input', renderMeetingList);
  $('#meetingList').addEventListener('click', e => {
    const btn = e.target.closest('.mtg');
    if (!btn) return;
    const mid = btn.dataset.mid;
    state.panelMeetingId = mid;
    renderMeetingList();
    setTab('minutes');
    openPanel();
    setScope(mid);
    $('.app').classList.remove('lib-open');
    if (window.innerWidth > 1080) $('#scrim').hidden = true;
  });

  /* 详情面板标签页 */
  $('#tabs').addEventListener('click', e => {
    const tab = e.target.closest('.tab');
    if (tab) setTab(tab.dataset.tab);
  });

  /* 示例问题 */
  $('#stream').addEventListener('click', e => {
    const s = e.target.closest('.suggest');
    if (!s) return;
    $('#input').value = s.dataset.q;
    autoGrow();
    send();
  });

  /* 拖拽上传 */
  const chat = $('.chat');
  ['dragenter', 'dragover'].forEach(ev => chat.addEventListener(ev, e => {
    e.preventDefault();
    chat.classList.add('dragging');
  }));
  chat.addEventListener('dragleave', e => {
    if (chat.contains(e.relatedTarget)) return;
    chat.classList.remove('dragging');
  });
  chat.addEventListener('drop', e => {
    e.preventDefault();
    chat.classList.remove('dragging');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) attachFile(f);
  });

  /* 导出菜单与遮罩 */
  document.addEventListener('click', e => {
    const menu = $('#exportMenu');
    if (!menu) return;
    if (menu.contains(e.target) || e.target.closest('#exportBtn')) return;
    menu.remove();
  });
  $('#scrim').addEventListener('click', () => {
    closePanel();
    $('.app').classList.remove('lib-open');
  });
  $('#sidebarToggle').addEventListener('click', () => {
    $('.app').classList.add('lib-open');
    $('#scrim').hidden = false;
  });

  /* 视口变化 */
  window.addEventListener('resize', () => {
    if (window.innerWidth > 1080) {
      $('#scrim').hidden = true;
      $('.app').classList.remove('lib-open');
      if (state.panelOpen) $('#panel').hidden = false;
    }
  });

  toast('原型演示 · 数据为模拟数据，后端未接入');
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();