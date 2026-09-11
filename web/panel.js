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
      '<p>没有待确认的条目<br><small>所有内容都已通过置信度校验</small></p></div>';
  }
  return section('需要人工确认', items.length, items.map((item) =>
    '<div class="card" data-review="' + item.id + '">' +
    '<ul class="card-points"><li>' + escapeHtml(item.task) + '</li></ul>' +
    '<p class="review-why">' + ICON.help + escapeHtml(item.reason) + '</p>' +
    '<div class="card-foot">' + ownerChip(item.owner) + citationsHtml(item.citations) +
    '</div><div class="review-actions">' +
    '<button class="btn btn-ghost" type="button" data-dismiss="' + item.id + '">忽略</button>' +
    '<button class="btn btn-primary" type="button" data-confirm="' + item.id + '">' +
    ICON.check + '确认通过</button></div></div>').join(''));
}

function renderRaw(detail) {
  const turns = (detail.segments || []).map((segment) => {
    const low = segment.speaker_confidence < 0.93;
    return '<div class="turn' + (low ? ' low' : '') + '" id="seg-' + segment.id +
      '" data-sid="' + segment.id + '">' +
      '<div class="turn-side"><span class="turn-time">' + mmss(segment.start) + ' - ' +
      mmss(segment.end) + '</span><span class="turn-who" style="color:' +
      speakerColor(segment.speaker_id) + '">' + escapeHtml(speakerName(segment.speaker_id)) +
      '</span></div><div class="turn-text">' + escapeHtml(segment.text) + '</div></div>';
  }).join('');
  return '<p class="raw-note">完整转写 · 点击回答中的引用会跳转并高亮对应片段。' +
    '声纹置信度低于 0.93 的片段带 ? 标记。</p>' + turns;
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

export function openSegment(meetingId, segmentId) {
  if (!meetingById(meetingId)) return;
  store.panelMeetingId = meetingId;
  openPanel();
  const run = () => {
    setTab('raw');
    const node = $('#seg-' + segmentId);
    if (!node) return;
    $$('.turn.hit').forEach((item) => item.classList.remove('hit'));
    node.classList.add('hit');
    node.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => node.classList.remove('hit'), 2600);
  };
  if (detailById(meetingId)) run();
  else loadDetail(meetingId).then(run).catch(() => toast('会议详情加载失败'));
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
    await api.resolveReview(key, { decision: 'confirm' });
    store.dismissed.add(key);
    await refreshDetail(key.split(':')[0]);
    toast('已确认，条目已并入最终纪要', 'ok');
    return;
  }
  const dismissBtn = event.target.closest('[data-dismiss]');
  if (dismissBtn) {
    const key = dismissBtn.dataset.dismiss;
    await api.resolveReview(key, { decision: 'dismiss' });
    store.dismissed.add(key);
    await refreshDetail(key.split(':')[0]);
    toast('已忽略该条目');
  }
}

async function refreshDetail(meetingId) {
  await loadLibrary();
  await loadDetail(meetingId);
}
