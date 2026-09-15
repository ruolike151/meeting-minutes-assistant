/* 轻前端入口：状态初始化、事件绑定与导出。

无构建、无框架，模块之间靠 ES Module 组织：
store（状态） / api（接口） / ui（渲染片段） / panel（会议库与详情） / chat（对话）。
业务规则全在后端，这里只负责渲染与交互。 */

import * as api from './api.js';
import {
  attachFile, autoGrow, clearAttachment, newChat, processAudio, renderWelcome, send, setScope,
  updateSendState,
} from './chat.js';
import {
  closePanel, handleConversationClick, handlePanelClick, loadConversations, loadDetail, loadLibrary,
  openPanel, openSegment, renderMeetingList, setTab,
} from './panel.js';
import { meetingById, store } from './store.js';
import { $, ICON, escapeHtml, toast } from './ui.js';

function setTheme(theme) {
  store.theme = theme;
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem('mna-theme', theme);
  } catch (error) {
    /* file:// 或隐私模式下可能不可用，忽略 */
  }
}

function triggerDownload(url) {
  // 文件名由后端的 Content-Disposition 决定（含中文，走 RFC 5987 编码）
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function doExport(kind) {
  const meeting = meetingById(store.panelMeetingId);
  if (kind === 'chat') {
    if (!store.conversationId) return toast('还没有对话可以导出');
    triggerDownload(api.conversationExportUrl(store.conversationId));
    return toast('已导出：对话记录.md', 'ok');
  }
  if (!meeting) return toast('请先在左侧选择一场会议');
  triggerDownload(api.exportUrl(meeting.id, kind));
  const label = { md: '纪要.md', csv: '待办.csv', json: 'result.json' }[kind];
  return toast('已导出：' + meeting.title + '_' + label, 'ok');
}

function toggleExportMenu() {
  const existing = $('#exportMenu');
  if (existing) {
    existing.remove();
    return;
  }
  const meeting = meetingById(store.panelMeetingId);
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.id = 'exportMenu';
  menu.setAttribute('role', 'menu');
  menu.innerHTML =
    '<button class="menu-item" type="button" role="menuitem" data-export="md">' + ICON.doc +
    '<span><b>纪要 Markdown</b><small>' + escapeHtml(meeting ? meeting.title : '当前会议') +
    '</small></span></button>' +
    '<button class="menu-item" type="button" role="menuitem" data-export="csv">' + ICON.table +
    '<span><b>待办 CSV</b><small>可导入项目管理工具</small></span></button>' +
    '<button class="menu-item" type="button" role="menuitem" data-export="json">' + ICON.doc +
    '<span><b>结构化 JSON</b><small>供其他系统消费</small></span></button>' +
    '<button class="menu-item" type="button" role="menuitem" data-export="chat">' + ICON.chat +
    '<span><b>对话记录</b><small>保留问答与引用来源</small></span></button>';
  document.body.appendChild(menu);
  const rect = $('#exportBtn').getBoundingClientRect();
  menu.style.top = rect.bottom + 8 + 'px';
  menu.style.right = Math.max(12, window.innerWidth - rect.right) + 'px';
  menu.addEventListener('click', (event) => {
    const button = event.target.closest('[data-export]');
    if (!button) return;
    doExport(button.dataset.export);
    menu.remove();
  });
}

function bindEvents() {
  $('#composerForm').addEventListener('submit', (event) => {
    event.preventDefault();
    if (!store.busy && store.pendingFile && !$('#input').value.trim()) {
      const file = store.pendingFile;
      clearAttachment();
      store.busy = true;
      updateSendState();
      processAudio(file);
      return;
    }
    send();
  });
  $('#input').addEventListener('input', () => {
    autoGrow();
    updateSendState();
  });
  $('#input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $('#composerForm').requestSubmit();
    }
  });

  $('#attachBtn').addEventListener('click', () => $('#fileInput').click());
  $('#fileInput').addEventListener('change', (event) => attachFile(event.target.files[0]));
  $('#attachRemove').addEventListener('click', clearAttachment);

  $('#newChatBtn').addEventListener('click', newChat);
  // 范围标签：点一下在「全部会议 / 当前会议」之间切换，对话内容保留
  $('#scopeChip').addEventListener('click', () => {
    const current = $('#scopeChip').dataset.meetingId;
    if (current) {
      setScope(null);
      toast('提问范围已切回「全部会议」', 'ok');
    } else {
      toast('当前已是全部会议；点左侧会议库的会议即可把范围限定到某一场');
    }
  });
  $('#themeBtn').addEventListener('click', () =>
    setTheme(store.theme === 'dark' ? 'light' : 'dark'));
  $('#exportBtn').addEventListener('click', toggleExportMenu);
  $('#panelClose').addEventListener('click', closePanel);
  $('#speakerBtn').addEventListener('click', () =>
    toast('直接问「有哪些待办、谁负责」，发言人汇总走后端 query_speaker 工具'));

  $('#meetingSearch').addEventListener('input', renderMeetingList);
  $('#conversationList').addEventListener('click', handleConversationClick);
  $('#meetingList').addEventListener('click', async (event) => {
    const button = event.target.closest('.mtg');
    if (!button) return;
    const meetingId = button.dataset.mid;
    store.panelMeetingId = meetingId;
    renderMeetingList();
    setTab('minutes');
    setScope(meetingId);
    openPanel();
    $('.app').classList.remove('lib-open');
    if (window.innerWidth > 1080) $('#scrim').hidden = true;
    try {
      await loadDetail(meetingId);
    } catch (error) {
      toast('会议详情加载失败：' + error.message);
    }
  });

  $('#tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (tab) setTab(tab.dataset.tab);
  });

  $('#stream').addEventListener('click', (event) => {
    const suggest = event.target.closest('.suggest');
    if (!suggest) return;
    $('#input').value = suggest.dataset.q;
    autoGrow();
    send();
  });

  // 证据回跳：回答与详情面板里的引用 chip 共用一套委托
  document.addEventListener('click', (event) => {
    const cite = event.target.closest('.cite');
    if (cite) openSegment(cite.dataset.mid, cite.dataset.sid);
  });
  $('#panelBody').addEventListener('click', (event) => {
    handlePanelClick(event).catch((error) => toast('操作失败：' + error.message));
  });

  const chat = $('.chat');
  ['dragenter', 'dragover'].forEach((name) =>
    chat.addEventListener(name, (event) => {
      event.preventDefault();
      chat.classList.add('dragging');
    }));
  chat.addEventListener('dragleave', (event) => {
    if (chat.contains(event.relatedTarget)) return;
    chat.classList.remove('dragging');
  });
  chat.addEventListener('drop', (event) => {
    event.preventDefault();
    chat.classList.remove('dragging');
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) attachFile(file);
  });

  document.addEventListener('click', (event) => {
    const menu = $('#exportMenu');
    if (!menu) return;
    if (menu.contains(event.target) || event.target.closest('#exportBtn')) return;
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
  window.addEventListener('resize', () => {
    if (window.innerWidth > 1080) {
      $('#scrim').hidden = true;
      $('.app').classList.remove('lib-open');
      if (store.panelOpen) $('#panel').hidden = false;
    }
  });
}

async function init() {
  let saved = null;
  try {
    saved = localStorage.getItem('mna-theme');
  } catch (error) {
    saved = null;
  }
  setTheme(saved || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));

  bindEvents();
  const welcome = renderWelcome();
  welcome.id = 'welcome';
  $('#stream').appendChild(welcome);
  setScope(null);
  updateSendState();

  try {
    await loadLibrary();
  } catch (error) {
    toast('会议库加载失败：' + error.message);
    return;
  }
  try {
    await loadConversations();
  } catch (error) {
    // 会话列表是辅助功能：拿不到也不该拦住主流程
    store.conversations = [];
  }
  if (store.panelMeetingId) {
    try {
      await loadDetail(store.panelMeetingId);
      openPanel();
    } catch (error) {
      toast('会议详情加载失败：' + error.message);
    }
  }
}

init();
