/* 会议库侧栏与详情面板。

数据都来自后端：列表用 GET /api/meetings，详情用 GET /api/meetings/{id}，
待办修改走 PATCH，待确认区走 POST /api/review/{id}/resolve。 */

import * as api from './api.js';
import {
  detailById, meetingById, participantsOf, registerMeetings, setDetail, speakerColor, speakerName, store,
} from './store.js';
import { $, $$, ICON, citationsHtml, durationText, escapeHtml, mmss, toast } from './ui.js';

export async function loadLibrary(params = {}) {
  const payload = await api.listMeetings(params);
  registerMeetings(payload.meetings);
  if (!store.panelMeetingId && store.meetings.length) {
    store.panelMeetingId = store.meetings[0].id;
  }
  renderMeetingList();
  return store.meetings;
}

/* ── 对话列表（切换历史会话）─────────────────────────────────
   只负责"列出 + 点击分发"：消息怎么渲染由 chat.js 决定，
   两边通过自定义事件解耦，避免 panel ↔ chat 互相 import。 */

export async function loadConversations() {
  const items = await api.listConversations();
  store.conversations = items;
  renderConversationList();
  return items;
}

export function renderConversationList() {
  const list = $('#conversationList');
  if (!list) return;
  const items = store.conversations || [];
  list.innerHTML = items.map((item) => {
    const active = store.conversationId === item.id;
    const title = item.title || '（空对话）';
    return '<li><button class="conv' + (active ? ' active' : '') + '" type="button" data-cid="' +
      escapeHtml(item.id) + '"' + (active ? ' aria-current="true"' : '') + '>' +
      '<span class="conv-title">' + escapeHtml(title) + '</span>' +
      '<span class="conv-meta">' + escapeHtml(shortTime(item.updated_at)) +
      ' · ' + item.message_count + ' 条</span></button></li>';
  }).join('');
  $('#conversationCount').textContent = String(items.length);
  $('#convEmpty').hidden = items.length > 0;
}

function shortTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return (date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
    pad(date.getHours()) + ':' + pad(date.getMinutes());
}

export function handleConversationClick(event) {
  const button = event.target.closest('.conv');
  if (!button) return;
  // 交给 chat.js 去加载并渲染（它拥有消息渲染逻辑）
  document.dispatchEvent(
    new CustomEvent('ma:open-conversation', { detail: { id: button.dataset.cid } })
  );
}

export function renderMeetingList() {
  const list = $('#meetingList');
  const query = ($('#meetingSearch').value || '').trim().toLowerCase();
  const matched = store.meetings.filter((meeting) => {
    if (!query) return true;
    const names = meeting.participants.map((p) => p.name).join(' ');
    return (meeting.title + ' ' + names + ' ' + meeting.date).toLowerCase().includes(query);
  });

  list.innerHTML = matched.map((meeting) => {
    const avatars = meeting.participants.slice(0, 4).map((p) =>
      '<span class="avatar" style="background:' + speakerColor(p.id) + '" title="' +
      escapeHtml(p.name) + '">' + escapeHtml(p.name.slice(0, 1)) + '</span>').join('');
    return '<li><button class="mtg" type="button" data-mid="' + meeting.id + '"' +
      (store.panelMeetingId === meeting.id ? ' aria-current="true"' : '') + '>' +
      '<span class="mtg-top"><span class="mtg-title">' + escapeHtml(meeting.title) + '</span>' +
      '<span class="mtg-dot' + (meeting.status === 'processing' ? ' processing' : '') + '"></span></span>' +
      '<span class="mtg-meta"><span>' + escapeHtml(meeting.date.slice(5)) + '</span>' +
      '<span class="sep">·</span><span>' + durationText(meeting.duration) + '</span>' +
      '<span class="sep">·</span><span class="avatars">' + avatars + '</span>' +
      (meeting.review_count ? '<span class="badge">' + meeting.review_count + '</span>' : '') +
      '</span></button></li>';
  }).join('');

  $('#meetingCount').textContent = String(store.meetings.length);
  $('#libEmpty').hidden = matched.length > 0;
}

export async function loadDetail(meetingId) {
  const detail = await api.getMeeting(meetingId);
  setDetail(detail);
  store.panelMeetingId = meetingId;
  renderPanel();
  return detail;
}

export function setTab(tab) {
  store.tab = tab;
  $$('#tabs .tab').forEach((node) => {
    node.setAttribute('aria-selected', String(node.dataset.tab === tab));
  });
  renderPanel();
}

function section(name, count, inner) {
  return '<div class="sect"><div class="sect-head"><h3>' + name + '</h3>' +
    '<span class="count">' + count + '</span></div>' + inner + '</div>';
}

function citationsFor(detail, segmentIds) {
  return (segmentIds || []).map((segmentId) => {
    const segment = (detail.segments || []).find((item) => item.id === segmentId);
    if (!segment) return null;
    return {
      meeting_id: detail.id,
      segment_id: segment.id,
      label: speakerName(segment.speaker_id) + ' ' + mmss(segment.start),
    };
  }).filter(Boolean);
}

function ownerChip(ownerId) {
  if (!ownerId) return '<span class="pill warn">' + ICON.alert + '负责人未指定</span>';
  return '<span class="who"><span class="dot" style="background:' + speakerColor(ownerId) +
    '"></span>' + escapeHtml(speakerName(ownerId)) + '</span>';
}

function dueChip(item) {
  if (item.due) return '<span class="pill">' + ICON.cal + escapeHtml(item.due) + '</span>';
  if (item.due_text) {
    return '<span class="pill">' + ICON.clock + escapeHtml(item.due_text) + '</span>';
  }
  return '<span class="pill warn">' + ICON.clock + '时间未定</span>';
}

function renderMinutes(detail) {
  const summary = (detail.summary || []).map((topic, index) =>
    '<div class="card"><div class="card-title"><span class="idx">' + (index + 1) + '</span>' +
    escapeHtml(topic.topic) + '</div><ul class="card-points">' +
    topic.points.map((point) => '<li>' + escapeHtml(point) + '</li>').join('') + '</ul>' +
    '<div class="card-foot">' + citationsHtml(citationsFor(detail, topic.evidence)) +
    '</div></div>').join('');

  const decisions = (detail.decisions || []).map((decision) =>
    '<div class="card"><div class="card-title">' + escapeHtml(decision.content) + '</div>' +
    '<div class="card-foot">' + citationsHtml(citationsFor(detail, decision.evidence)) +
    '<span class="conf">置信度 ' + Math.round((decision.confidence || 0) * 100) + '%</span>' +
    '</div></div>').join('');

  const questions = (detail.open_questions || []).map((question) =>
    '<div class="card"><div class="card-title">' + escapeHtml(question.content) + '</div>' +
    '<div class="card-foot">' + citationsHtml(citationsFor(detail, question.evidence)) +
    '</div></div>').join('');

  const risks = (detail.risks || []).map((risk) =>
    '<div class="card"><div class="card-title"><span class="pill warn">' + ICON.alert +
    '风险</span></div><ul class="card-points"><li>' + escapeHtml(risk.content) + '</li></ul>' +
    '<div class="card-foot">' + citationsHtml(citationsFor(detail, risk.evidence)) +
    '</div></div>').join('');

  return section('摘要', (detail.summary || []).length, summary) +
    section('决议', (detail.decisions || []).length, decisions) +
    section('待确认问题', (detail.open_questions || []).length, questions) +
    section('风险', (detail.risks || []).length, risks);
}

function renderActions(detail) {
  const items = detail.action_items || [];
  if (!items.length) {
    return '<div class="panel-empty">' + ICON.empty + '<p>这场会议还没有待办</p></div>';
  }
  const rows = items.map((item) => {
    const key = detail.id + ':' + item.id;
    const done = item.status === 'done' || store.resolved.has(key);
    return '<div class="task' + (done ? ' done' : '') + '">' +
      '<button class="tickbox" type="button" data-toggle="' + key + '" aria-pressed="' + done +
      '" aria-label="标记完成">' + ICON.check + '</button>' +
      '<div class="task-main"><span class="task-text">' + escapeHtml(item.task) + '</span>' +
      '<span class="task-meta">' + ownerChip(item.owner) + dueChip(item) +
      (item.needs_review ? '<span class="pill warn">' + ICON.alert + '待确认</span>' : '') +
      '</span><span class="ans-meta">' +
      citationsHtml(citationsFor(detail, item.evidence)) + '</span></div></div>';
  }).join('');
  return section('待办清单', items.length, rows);
}

function renderReview(detail) {
  const items = (detail.review_items || []).filter((item) => !store.dismissed.has(item.id));
  if (!items.length) {
    return '<div class="panel-empty">' + ICON.check +
      '<p>没有待确认的条目<br><small>所有内容都已通过置信度校验或人工确认</small></p></div>';
  }
  // 按类型分组：待办要补负责人/时间，决议只需要复核结论（M4 决策 ②=A）
  const groups = [
    { kind: 'action_item', title: '待办条目', hint: '负责人或截止时间需要你确认' },
    { kind: 'decision', title: '决议条目', hint: '模型置信度偏低，请复核结论是否成立' },
  ];
  return groups.map((group) => {
    const grouped = items.filter((item) => item.kind === group.kind);
    if (!grouped.length) return '';
    return section(group.title, grouped.length,
      '<p class="review-why">' + ICON.help + escapeHtml(group.hint) + '</p>' +
      grouped.map(reviewCard).join(''));
  }).join('');
}

function reviewCard(item) {
  const editForm = item.editable
    ? '<div class="review-edit" data-edit-form="' + item.id + '" hidden>' +
      '<label class="field"><span>负责人</span>' +
      '<select data-field="owner">' +
      '<option value="">保持未指定</option>' +
      (detailById(item.meeting_id) ? detailById(item.meeting_id).participants || [] : [])
        .map((p) => '<option value="' + escapeHtml(p.id) + '"' +
          (p.id === item.owner ? ' selected' : '') + '>' + escapeHtml(p.name) + '</option>').join('') +
      '</select></label>' +
      '<label class="field"><span>截止时间</span>' +
      '<input type="date" data-field="due" value="' + escapeHtml(item.due || '') + '"></label>' +
      '<button class="btn btn-primary" type="button" data-save="' + item.id + '">保存并确认</button>' +
      '</div>'
    : '';
  const editButton = item.editable
    ? '<button class="btn btn-ghost" type="button" data-edit="' + item.id + '">修改</button>' : '';
  return '<div class="card review-card" data-review="' + item.id + '">' +
    '<ul class="card-points"><li>' + escapeHtml(item.task) + '</li></ul>' +
    '<p class="review-why">' + ICON.help + escapeHtml(item.reason) + '</p>' +
    '<div class="card-foot">' + ownerChip(item.owner) + dueChip(item) +
    citationsHtml(item.citations) +
    '<span class="conf">置信度 ' + Math.round((item.confidence || 0) * 100) + '%</span>' +
    '</div>' + editForm +
    '<div class="review-actions">' +
    '<button class="btn btn-ghost" type="button" data-dismiss="' + item.id + '">忽略</button>' +
    editButton +
    '<button class="btn btn-primary" type="button" data-confirm="' + item.id + '">' +
    ICON.check + '确认通过</button></div></div>';
}

function renderRaw(detail) {
  // 有原声（且文件真的在）才给播放器：点击引用时会定位到该片段的起点（M4 决策 ④=A）。
  // 样例会议只有转写没有音频文件，此时给一句说明，而不是一个点了没反应的播放器。
  const playable = Boolean(detail.audio_available);
  const audio = playable
    ? '<div class="audio-bar"><audio id="panelAudio" controls preload="none" src="/api/meetings/' +
      encodeURIComponent(detail.id) + '/audio"></audio></div>'
    : detail.audio_file
      ? '<p class="raw-note">这场会议没有可播放的原声文件（样例数据只带转写文本），' +
        '点击引用仍会定位并高亮对应片段。</p>'
      : '';
  const turns = (detail.segments || []).map((segment) => {
    const low = segment.speaker_confidence < 0.93;
    return '<div class="turn' + (low ? ' low' : '') + '" id="seg-' + segment.id +
      '" data-sid="' + segment.id + '">' +
      '<div class="turn-side"><span class="turn-time">' + mmss(segment.start) + ' - ' +
      mmss(segment.end) + '</span><span class="turn-who" style="color:' +
      speakerColor(segment.speaker_id) + '">' + escapeHtml(speakerName(segment.speaker_id)) +
      '</span></div><div class="turn-text">' + escapeHtml(segment.text) + '</div></div>';
  }).join('');
  return audio + '<p class="raw-note">完整转写 · 点击回答中的引用会跳转并高亮对应片段' +
    (playable ? '，并从该句开始播放' : '（只做高亮，不播放）') +
    '。声纹置信度低于 0.93 的片段带 ? 标记。</p>' + turns;
}

export function renderPanel() {
  const detail = detailById(store.panelMeetingId);
  const body = $('#panelBody');
  if (!detail) {
    $('#panelTitle').textContent = '会议详情';
    $('#panelSub').textContent = '从左侧会议库选择一场会议';
    body.innerHTML = '<div class="panel-empty">' + ICON.empty + '<p>还没有选中会议</p></div>';
    return;
  }
  $('#panelTitle').textContent = detail.title;
  $('#panelSub').textContent = detail.date + ' ' + (detail.time || '') + ' · ' +
    durationText(detail.duration) + ' · ' + participantsOf(detail.id).length + ' 人 · ' +
    (detail.segments || []).length + ' 个发言片段';

  const reviewCount = (detail.review_items || [])
    .filter((item) => !store.dismissed.has(item.id)).length;
  $('#tabs .tab[data-tab="review"]').innerHTML =
    '待确认' + (reviewCount ? '<span class="badge">' + reviewCount + '</span>' : '');

  if (store.tab === 'minutes') body.innerHTML = renderMinutes(detail);
  else if (store.tab === 'actions') body.innerHTML = renderActions(detail);
  else if (store.tab === 'review') body.innerHTML = renderReview(detail);
  else body.innerHTML = renderRaw(detail);
}

export function openPanel() {
  $('#panel').hidden = false;
  store.panelOpen = true;
  if (window.innerWidth <= 1080) $('#scrim').hidden = false;
  else $('.app').classList.remove('panel-closed');
}

export function closePanel() {
  $('#panel').hidden = true;
  $('#scrim').hidden = true;
  const menu = $('#exportMenu');
  if (menu) menu.remove();
  if (window.innerWidth > 1080) $('.app').classList.add('panel-closed');
  store.panelOpen = false;
}

export async function openSegment(meetingId, segmentId) {
  // 跨会议引用：引用可能来自侧栏当前没有的会议，先补齐会议库再跳（M4 T6）
  if (!meetingById(meetingId)) {
    try {
      await loadLibrary();
    } catch (error) {
      toast('会议库加载失败，无法跳转引用');
      return;
    }
  }
  if (!meetingById(meetingId)) {
    toast('这条引用所属的会议不在会议库里');
    return;
  }
  store.panelMeetingId = meetingId;
  openPanel();
  const run = () => {
    setTab('raw');
    focusSegment(segmentId);
    playSegment(meetingId, segmentId);
  };
  if (detailById(meetingId)) {
    run();
    return;
  }
  try {
    await loadDetail(meetingId);
    run();
  } catch (error) {
    toast('会议详情加载失败');
  }
}

function focusSegment(segmentId) {
  const node = $('#seg-' + segmentId);
  if (!node) return;
  $$('.turn.hit').forEach((item) => item.classList.remove('hit'));
  node.classList.add('hit');
  node.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(() => node.classList.remove('hit'), 2600);
}

function playSegment(meetingId, segmentId) {
  const detail = detailById(meetingId);
  const audio = $('#panelAudio');
  if (!detail || !audio) return;
  const segment = (detail.segments || []).find((item) => item.id === segmentId);
  if (!segment) return;
  try {
    audio.currentTime = segment.start;
    audio.play();
  } catch (error) {
    /* 浏览器可能因自动播放策略拒绝：定位仍然生效，不弹错 */
  }
}

export async function handlePanelClick(event) {
  const toggle = event.target.closest('[data-toggle]');
  if (toggle) {
    const key = toggle.dataset.toggle;
    const done = !store.resolved.has(key);
    if (done) store.resolved.add(key); else store.resolved.delete(key);
    await api.patchActionItem(key, { status: done ? 'done' : 'pending' });
    await refreshDetail(key.split(':')[0]);
    toast(done ? '已标记为完成' : '已恢复为待处理', 'ok');
    return;
  }
  const confirmBtn = event.target.closest('[data-confirm]');
  if (confirmBtn) {
    const key = confirmBtn.dataset.confirm;
    await resolveWithRollback(key, { decision: 'confirm' }, '已确认，条目已并入最终纪要');
    return;
  }
  const dismissBtn = event.target.closest('[data-dismiss]');
  if (dismissBtn) {
    const key = dismissBtn.dataset.dismiss;
    await resolveWithRollback(key, { decision: 'dismiss' }, '已忽略该条目');
    return;
  }
  const editBtn = event.target.closest('[data-edit]');
  if (editBtn) {
    const form = document.querySelector('[data-edit-form="' + editBtn.dataset.edit + '"]');
    if (form) form.hidden = !form.hidden;
    return;
  }
  const saveBtn = event.target.closest('[data-save]');
  if (saveBtn) {
    const key = saveBtn.dataset.save;
    const card = saveBtn.closest('[data-edit-form]') || document;
    const owner = (card.querySelector('[data-field="owner"]') || {}).value || null;
    const due = (card.querySelector('[data-field="due"]') || {}).value || null;
    if (!owner && !due) {
      toast('至少填负责人或截止时间');
      return;
    }
    await resolveWithRollback(
      key, { action: 'edit', owner: owner, due: due }, '已保存并确认', true);
  }
}

/* 人工确认的乐观更新：先动本地状态让界面立刻响应，请求失败再回滚并提示。
   M1 的版本只乐观不回滚，网络失败时界面会"假装成功"（M4 T7 修正）。 */
async function resolveWithRollback(key, body, okText, confirmAfterEdit) {
  store.dismissed.add(key);
  renderPanel();
  try {
    if (confirmAfterEdit) {
      await api.resolveReview(key, body);
      await api.resolveReview(key, { decision: 'confirm' });
    } else {
      await api.resolveReview(key, body);
    }
    await refreshDetail(key.split(':')[0]);
    toast(okText, 'ok');
  } catch (error) {
    store.dismissed.delete(key);
    renderPanel();
    toast('操作没成功：' + error.message);
  }
}

async function refreshDetail(meetingId) {
  await loadLibrary();
  await loadDetail(meetingId);
}
