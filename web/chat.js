/* 对话区：消息渲染、发送流程与音频上传。

提问与上传都走同一套编排接口：问答是 POST /api/chat（SSE），
音频是 POST /api/audio + GET /api/tasks/{id}/events（SSE）。 */

import * as api from './api.js';
import { loadLibrary, openPanel, setTab } from './panel.js';
import { store, speakerName } from './store.js';
import { $, $$, ICON, citationChip, escapeHtml, fileSize, mmss, stepsHtml, toast } from './ui.js';

const SUGGESTIONS = [
  { icon: 'search', title: '这场周会定了什么？', sub: '查询指定会议的结论与决议' },
  { icon: 'user', title: '王强一共答应了哪些事？', sub: '跨会议汇总某人的承诺与待办' },
  { icon: 'mic', title: '谁提到了活动弹窗的问题？', sub: '定位具体发言人与原声时间点' },
  { icon: 'alert', title: '这三场会有什么风险？', sub: '跨会议汇总未决事项与风险' },
];

export function updateSendState() {
  const input = $('#input');
  const hasText = input && input.value.trim().length > 0;
  $('#sendBtn').disabled = store.busy || (!hasText && !store.pendingFile);
  $('#attachBtn').disabled = store.busy;
}

export function autoGrow() {
  const el = $('#input');
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 168) + 'px';
}

export function scrollStream(force) {
  const box = $('#stream');
  const near = box.scrollHeight - box.scrollTop - box.clientHeight < 180;
  if (force || near) box.scrollTop = box.scrollHeight;
}

export function renderWelcome() {
  const cards = SUGGESTIONS.map((item, index) =>
    '<button class="suggest" type="button" data-q="' + escapeHtml(item.title) +
    '" style="animation-delay:' + (index * 70) + 'ms">' + ICON[item.icon] +
    '<span><b>' + escapeHtml(item.title) + '</b><small>' + escapeHtml(item.sub) +
    '</small></span></button>').join('');
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

export function appendMessage(message) {
  const stream = $('#stream');
  const welcome = $('#welcome');
  if (welcome) welcome.remove();
  const node = document.createElement('article');
  node.className = 'msg ' + message.role;
  node.dataset.id = message.id || '';
  const avatar = message.role === 'user'
    ? '<span class="msg-avatar">' + ICON.user + '</span>'
    : '<span class="msg-avatar">' + ICON.spark + '</span>';
  node.innerHTML = avatar + '<div class="msg-body"><span class="msg-who">' +
    (message.role === 'user' ? '你' : '纪要助手') + '</span>' +
    '<div class="bubble">' + (message.html || '') + '</div></div>';
  stream.appendChild(node);
  scrollStream(true);
  return node;
}

export function appendUserMessage(text) {
  store.messages.push({ role: 'user', content: text });
  return appendMessage({ role: 'user', html: '<p>' + escapeHtml(text) + '</p>' });
}

function appendThinking(label) {
  const node = document.createElement('article');
  node.className = 'msg assistant';
  node.dataset.thinking = '1';
  node.innerHTML = '<span class="msg-avatar">' + ICON.spark + '</span>' +
    '<div class="msg-body"><span class="msg-who">纪要助手</span>' +
    '<div class="bubble plain"><span class="thinking">' +
    '<span class="dots"><i></i><i></i><i></i></span>' +
    '<span class="thinking-label">' + escapeHtml(label) + '</span></span></div></div>';
  $('#stream').appendChild(node);
  scrollStream(true);
  return node;
}

function removeThinking() {
  $$('[data-thinking="1"]').forEach((node) => node.remove());
}

function typeText(node, text, done) {
  const bubble = node.querySelector('.bubble');
  const target = document.createElement('p');
  const caret = document.createElement('span');
  caret.className = 'caret';
  bubble.appendChild(target);
  let index = 0;
  const step = Math.max(1, Math.round(text.length / 44));
  const timer = setInterval(() => {
    index = Math.min(text.length, index + step);
    target.textContent = text.slice(0, index);
    if (index < text.length) target.appendChild(caret);
    scrollStream();
    if (index >= text.length) {
      clearInterval(timer);
      caret.remove();
      done();
    }
  }, 24);
}

function itemHtml(item) {
  let inner = '';
  if (item.title) inner += '<b>' + escapeHtml(item.title) + '</b>';
  if (item.text) inner += '<span class="ans-text">' + escapeHtml(item.text) + '</span>';
  const meta = [];
  if (item.citations && item.citations.length) meta.push(item.citations.map(citationChip).join(''));
  if (item.warn) meta.push('<span class="pill warn">' + ICON.alert + '待确认</span>');
  if (item.meta) meta.push('<span class="conf">置信度 ' + escapeHtml(item.meta) + '</span>');
  if (meta.length) inner += '<span class="ans-meta">' + meta.join('') + '</span>';
  return '<li><span class="ans-item">' + inner + '</span></li>';
}

export function renderAnswer(answer) {
  let html = '<p>' + escapeHtml(answer.lead) + '</p>';
  (answer.blocks || []).forEach((block) => {
    if (block.kind === 'note') {
      html += '<div class="answer-block"><p>' + escapeHtml(block.text || '') + '</p></div>';
      return;
    }
    if (!block.items || !block.items.length) return;
    html += '<div class="answer-block"><h4>' + escapeHtml(block.title || '') + '</h4><ol>' +
      block.items.map(itemHtml).join('') + '</ol></div>';
  });
  if (answer.footnote) html += '<p class="ans-foot">' + escapeHtml(answer.footnote) + '</p>';
  return html;
}

export async function send(text) {
  if (store.busy) return;
  const input = $('#input');
  const message = (text || input.value).trim();
  if (!message) return;
  if (!text) {
    input.value = '';
    autoGrow();
  }
  appendUserMessage(message);
  store.busy = true;
  updateSendState();

  const thinking = appendThinking('正在理解意图与查询范围…');
  const label = thinking.querySelector('.thinking-label');
  try {
    const result = await api.chat(message, {
      conversationId: store.conversationId,
      meetingId: store.activeId,
      onEvent: (event, data) => {
        if (event === 'stage' && data.label) label.textContent = data.label + '…';
      },
    });
    store.conversationId = result.conversation_id;
    removeThinking();
    const answer = result.answer;
    store.messages.push({ role: 'assistant', content: result.message.content });
    const node = appendMessage({ id: result.message.id, role: 'assistant', html: '' });
    typeText(node, answer.lead, () => {
      node.querySelector('.bubble').innerHTML = renderAnswer(answer);
      scrollStream();
    });
  } catch (error) {
    removeThinking();
    appendMessage({
      role: 'assistant',
      html: '<p>' + escapeHtml('这次没跑通：' + error.message) + '</p>',
    });
  } finally {
    store.busy = false;
    updateSendState();
  }
}

export function clearAttachment() {
  store.pendingFile = null;
  $('#attachChip').hidden = true;
  $('#fileInput').value = '';
  updateSendState();
}

export function attachFile(file) {
  if (!file) return;
  store.pendingFile = file;
  $('#attachName').textContent = file.name;
  $('#attachSize').textContent = fileSize(file.size);
  $('#attachChip').hidden = false;
  updateSendState();
  toast('已选择音频，发送后开始处理');
}

function jobHtml(file) {
  return '<div class="job"><div class="job-head">' +
    '<span class="msg-avatar" style="background:linear-gradient(140deg,var(--sp-3),var(--accent))">' + ICON.wave + '</span>' +
    '<span class="job-file"><b>' + escapeHtml(file.name) + '</b>' +
    '<small>' + fileSize(file.size) + ' · 离线后端处理中</small></span>' +
    '<span class="job-pct">0%</span></div>' +
    '<div class="job-bar"><i></i></div>' +
    '<div class="job-steps"></div></div>';
}

function citationFor(detail, segmentId) {
  const segment = (detail.segments || []).find((item) => item.id === segmentId);
  if (!segment) return null;
  return {
    meeting_id: detail.id,
    segment_id: segment.id,
    label: speakerName(segment.speaker_id) + ' ' + mmss(segment.start),
  };
}

function uploadResultHtml(detail) {
  const items = (detail.summary || []).map((topic) => ({
    title: topic.topic,
    text: topic.points.join('；'),
    citations: (topic.evidence || []).map((id) => citationFor(detail, id)).filter(Boolean),
  }));
  const review = (detail.review_items || []).length;
  const lead = '《' + detail.title + '》处理完成。识别出 ' + detail.segments.length +
    ' 个发言片段，已生成摘要 ' + (detail.summary || []).length + ' 条' +
    (review ? '，其中 ' + review + ' 项需要你确认。' : '。');
  return '<p>' + escapeHtml(lead) + '</p>' +
    '<div class="answer-block"><h4>会议摘要</h4><ol>' +
    items.map(itemHtml).join('') + '</ol></div>';
}

export async function processAudio(file) {
  appendUserMessage('上传会议录音：' + file.name + '（' + fileSize(file.size) + '）');
  const node = appendMessage({ role: 'assistant', html: jobHtml(file) });
  const bar = node.querySelector('.job-bar i');
  const pct = node.querySelector('.job-pct');
  const steps = node.querySelector('.job-steps');
  try {
    const task = await api.uploadAudio(file);
    steps.innerHTML = stepsHtml(task.stages, 0);
    const meetingId = await new Promise((resolve, reject) => {
      api.taskEvents(task.task_id, (event, data) => {
        if (event === 'stage') {
          steps.innerHTML = stepsHtml(task.stages, data.index);
          const progress = Math.round(((data.index + 1) / task.stages.length) * 100);
          bar.style.width = progress + '%';
          pct.textContent = progress + '%';
        } else if (event === 'result') {
          resolve(data.meeting_id);
        } else if (event === 'error') {
          reject(new Error(data.message || '处理失败'));
        }
      });
    });
    bar.style.width = '100%';
    pct.textContent = '100%';
    steps.innerHTML = stepsHtml(task.stages, task.stages.length);

    await loadLibrary();
    const detail = await api.getMeeting(meetingId);
    store.details.set(detail.id, detail);
    node.querySelector('.bubble').innerHTML = uploadResultHtml(detail);
    scrollStream(true);

    store.panelMeetingId = detail.id;
    store.activeId = detail.id;
    setScope(detail.id);
    setTab((detail.review_items || []).length ? 'review' : 'minutes');
    openPanel();
    toast('纪要已生成：' + detail.title, 'ok');
  } catch (error) {
    node.querySelector('.bubble').innerHTML =
      '<p>' + escapeHtml('音频处理失败：' + error.message) + '</p>';
  } finally {
    store.busy = false;
    updateSendState();
  }
}

export function setScope(meetingId) {
  store.activeId = meetingId;
  const meeting = store.meetings.find((item) => item.id === meetingId);
  const title = $('#chatTitle');
  const sub = $('#chatSubtitle');
  if (!meeting) {
    title.textContent = '全局对话';
    sub.textContent = '可查询任意会议，或上传录音生成纪要';
  } else {
    title.textContent = '正在就《' + meeting.title + '》对话';
    sub.textContent = meeting.date + ' · 提问范围已限定在本场会议';
  }
}

export function newChat() {
  store.messages = [];
  store.conversationId = null;
  $('#stream').innerHTML = '';
  const welcome = renderWelcome();
  welcome.id = 'welcome';
  $('#stream').appendChild(welcome);
  setScope(null);
  toast('已开始新的对话');
}
