/* 全局状态与数据访问。

刻意保持极小（《技术方案》12 节）：一个 store 对象 + 若干查询函数，
不引入响应式框架与虚拟 DOM。所有业务规则都在后端，前端只做展示。 */

export const store = {
  meetings: [],              // MeetingSummary[]
  details: new Map(),        // id -> MeetingDetail
  activeId: null,            // 对话作用域：null 表示全局
  panelMeetingId: null,
  tab: 'minutes',
  conversationId: null,
  pendingFile: null,
  busy: false,
  theme: 'light',
  panelOpen: false,
  resolved: new Set(),       // 本地乐观状态：已完成的待办 key
  dismissed: new Set(),      // 本地乐观状态：已忽略的待确认 key
};

const PALETTE = ['var(--sp-1)', 'var(--sp-2)', 'var(--sp-3)', 'var(--sp-4)', 'var(--sp-5)'];
const speakerColors = new Map();
const speakerNames = new Map();

export function registerSpeakers(participants) {
  (participants || []).forEach((p) => {
    if (!speakerColors.has(p.id)) {
      speakerColors.set(p.id, PALETTE[speakerColors.size % PALETTE.length]);
    }
    speakerNames.set(p.id, p.name);
  });
}

export function speakerColor(id) {
  return speakerColors.get(id) || 'var(--fg-subtle)';
}

export function speakerName(id) {
  return speakerNames.get(id) || '未识别';
}

export function meetingById(id) {
  return store.meetings.find((m) => m.id === id) || null;
}

export function participantsOf(id) {
  const meeting = meetingById(id);
  return meeting ? meeting.participants : [];
}

export function detailById(id) {
  return store.details.get(id) || null;
}

export function setDetail(detail) {
  store.details.set(detail.id, detail);
  return detail;
}

export function registerMeetings(meetings) {
  store.meetings = meetings || [];
  store.meetings.forEach((m) => registerSpeakers(m.participants));
}
