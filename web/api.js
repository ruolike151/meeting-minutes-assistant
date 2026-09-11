/* 后端接口封装。

对话走 SSE（fetch + ReadableStream 解析），音频任务进度走 EventSource，
导出直接拼 URL 交给浏览器下载。前端不持有任何业务规则。 */

async function toJson(response) {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (error) {
      detail = response.statusText;
    }
    throw new Error(detail || ('请求失败：' + response.status));
  }
  return response.json();
}

function parseFrame(raw) {
  let event = 'message';
  const data = [];
  raw.split('\n').forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data.push(line.slice(5).trim());
  });
  if (!data.length) return null;
  try {
    return { event, data: JSON.parse(data.join('\n')) };
  } catch (error) {
    return { event, data: { text: data.join('\n') } };
  }
}

async function readStream(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index = buffer.indexOf('\n\n');
    while (index >= 0) {
      const frame = parseFrame(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
      if (frame) onEvent(frame.event, frame.data);
      index = buffer.indexOf('\n\n');
    }
  }
}

export async function listMeetings(params = {}) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const suffix = query.toString() ? '?' + query.toString() : '';
  return toJson(await fetch('/api/meetings' + suffix));
}

export async function getMeeting(id) {
  return toJson(await fetch('/api/meetings/' + encodeURIComponent(id)));
}

/* getMinutes / listReview 对应文档 11 节的独立接口。M1 的界面走的是
   MeetingDetail 聚合视图，这两个方法留给后续里程碑与其他调用方。 */
export async function getMinutes(id) {
  return toJson(await fetch('/api/meetings/' + encodeURIComponent(id) + '/minutes'));
}

export async function patchActionItem(itemId, body) {
  return toJson(
    await fetch('/api/action-items/' + encodeURIComponent(itemId), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  );
}

export async function listReview(meetingId) {
  const suffix = meetingId ? '?meeting_id=' + encodeURIComponent(meetingId) : '';
  return toJson(await fetch('/api/review' + suffix));
}

export async function resolveReview(itemId, body) {
  return toJson(
    await fetch('/api/review/' + encodeURIComponent(itemId) + '/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  );
}

export async function chat(message, options = {}) {
  const { conversationId, meetingId, onEvent } = options;
  const response = await fetch('/api/chat?stream=1', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      message,
      conversation_id: conversationId || null,
      meeting_id: meetingId || null,
    }),
  });
  if (!response.ok || !response.body) {
    return chatJson(message, options);
  }
  let final = null;
  await readStream(response, (event, data) => {
    if (event === 'done') final = data;
    if (onEvent) onEvent(event, data);
  });
  if (!final) throw new Error('对话流意外结束');
  return final;
}

export async function chatJson(message, options = {}) {
  const { conversationId, meetingId } = options;
  const body = {
    message,
    conversation_id: conversationId || null,
    meeting_id: meetingId || null,
  };
  return toJson(
    await fetch('/api/chat?stream=0', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  );
}

export async function uploadAudio(file) {
  const form = new FormData();
  form.append('file', file);
  return toJson(await fetch('/api/audio', { method: 'POST', body: form }));
}

export function taskEvents(taskId, onEvent) {
  const source = new EventSource('/api/tasks/' + encodeURIComponent(taskId) + '/events');
  ['stage', 'result', 'done', 'error'].forEach((name) => {
    source.addEventListener(name, (event) => {
      let data = {};
      try {
        data = JSON.parse(event.data);
      } catch (error) {
        data = {};
      }
      onEvent(name, data);
      if (name === 'done' || name === 'error') source.close();
    });
  });
  return source;
}

export function exportUrl(meetingId, format) {
  return '/api/export/' + encodeURIComponent(meetingId) + '?format=' + format;
}

export function conversationExportUrl(conversationId) {
  return '/api/conversations/' + encodeURIComponent(conversationId) + '/export?format=md';
}
