/* =========================================================
   原型自测：node prototype/selftest.js
   校验数据完整性、回答引擎、渲染与导出，全部为纯逻辑测试，无需浏览器。
   ========================================================= */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const DIR = __dirname;

let pass = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) { pass++; console.log('  ok   ' + name); }
  else { failures.push(name + (detail ? ' → ' + detail : '')); console.log('  FAIL ' + name + (detail ? ' → ' + detail : '')); }
}

/* ── 构造最小 DOM 环境，仅用于加载脚本 ── */
function makeContext() {
  const fakeEl = () => ({
    style: {}, dataset: {}, classList: { add() {}, remove() {}, contains() { return false; } },
    children: [], hidden: false, innerHTML: '', textContent: '',
    appendChild() {}, remove() {}, addEventListener() {}, setAttribute() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    insertAdjacentHTML() {}, getBoundingClientRect() { return { bottom: 0, right: 0 }; }
  });
  const ctx = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    window: {
      innerWidth: 1440, innerHeight: 900,
      matchMedia: () => ({ matches: false }),
      addEventListener() {}
    },
    document: {
      readyState: 'complete', documentElement: { dataset: {} }, body: { appendChild() {} },
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      createElement: fakeEl
    },
    localStorage: { getItem: () => null, setItem() {} },
    Blob: function () {}, URL: { createObjectURL: () => '', revokeObjectURL() {} }
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  return ctx;
}

const mockSrc = fs.readFileSync(path.join(DIR, 'mock-data.js'), 'utf8');
const appSrc = fs.readFileSync(path.join(DIR, 'app.js'), 'utf8')
  .replace(/if \(document\.readyState[\s\S]*$/, '');   // 去掉启动引导，只保留纯逻辑

const ctx = makeContext();
vm.runInContext(mockSrc, ctx);

/* vm 中的顶层 const 不会挂到 context 上，显式导出后使用 */
const EXPORTS = 'globalThis.__api = { buildAnswer, answerHtml, minutesMarkdown, actionsCsv,' +
                ' searchSegments, conversationMarkdown, stripMarkers, MEETINGS, SPEAKERS };';
vm.runInContext(appSrc + '\n;' + EXPORTS, ctx);

const api = ctx.__api;
const { buildAnswer, answerHtml, minutesMarkdown, actionsCsv, searchSegments, conversationMarkdown } = api;
const MEETINGS = api.MEETINGS;
const SPEAKERS = api.SPEAKERS;

/* ── 1. 数据完整性 ── */
console.log('\n[1] 数据完整性');
check('会议数量为 3', MEETINGS.length === 3, '实际 ' + MEETINGS.length);

let dangling = [];
let badOwner = [];
MEETINGS.forEach(m => {
  const ids = new Set(m.segments.map(s => s.id));
  const walk = (o, p) => {
    if (Array.isArray(o)) return o.forEach((v, i) => walk(v, p + '[' + i + ']'));
    if (o && typeof o === 'object') {
      (o.evidence || []).forEach(e => { if (!ids.has(e)) dangling.push(p + ' → ' + e); });
      Object.keys(o).forEach(k => walk(o[k], p + '.' + k));
    }
  };
  walk(m.minutes, m.id);
  m.minutes.action_items.forEach(a => {
    if (a.owner && !SPEAKERS[a.owner]) badOwner.push(a.id + ' → ' + a.owner);
  });
});
check('证据引用全部指向存在的片段', dangling.length === 0, dangling.join(', '));
check('待办负责人均存在于发言人库', badOwner.length === 0, badOwner.join(', '));
check('每场会议都有待办与风险', MEETINGS.every(m => m.minutes.action_items.length > 0 && m.minutes.risks.length > 0));
check('存在待确认条目', MEETINGS.some(m => m.minutes.action_items.some(a => a.needs_review)));

/* ── 2. 检索层 ── */
console.log('\n[2] 检索层');
const hits = searchSegments('活动弹窗', MEETINGS);
check('关键词检索能命中"活动弹窗"', hits.length > 0 && hits[0].seg.text.includes('活动弹窗'));
check('检索结果按相关度排序', hits.length < 2 || hits[0].score >= hits[1].score);
check('无关词返回空结果', searchSegments('紫菜蛋花汤', MEETINGS).length === 0);

/* ── 3. 意图路由与回答 ── */
console.log('\n[3] 回答引擎');
const cases = [
  { q: '这场周会定了什么？', expect: /决议/ },
  { q: '王强一共答应了哪些事？', expect: /王强/ },
  { q: '谁提到了活动弹窗的问题？', expect: /陈静/ },
  { q: '这三场会有什么风险？', expect: /风险/ },
  { q: '有什么还没定下来的？', expect: /未决|待确认/ },
  { q: '待办有哪些？', expect: /待办/ }
];

cases.forEach(c => {
  const res = buildAnswer(c.q);
  const html = answerHtml(res);
  check('回答 "' + c.q + '"', c.expect.test(res.lead + html), 'lead=' + res.lead);
  check('  └ 引用的片段均存在', !/data-sid="(undefined|null)"/.test(html));
  check('  └ 渲染无 undefined/NaN', !/undefined|NaN/.test(html));
});

/* 拒答：找不到证据时不编造 */
const nf = buildAnswer('公司食堂今天中午吃什么？');
check('无依据的问题返回拒答', /没有找到/.test(nf.lead), nf.lead);

/* 澄清：全局模式下范围不唯一时反问 */
const needClarify = buildAnswer('总结一下');
check('范围不明确时主动澄清', /哪一场会议/.test(needClarify.lead), needClarify.lead);

/* 引用标记解析 */
const dec = buildAnswer('这场周会定了什么？');
const decHtml = answerHtml(dec);
check('决议回答包含可点击的证据 chip', /class="cite"/.test(decHtml));
check('证据 chip 带有会议与片段 ID', /data-mid="m_20260911"/.test(decHtml));
check('打字机用的纯文本已剔除标记', !/\[\[/.test(ctx.stripMarkers(dec.lead)));

/* ── 4. 导出 ── */
console.log('\n[4] 导出');
const m1 = MEETINGS[0];
const md = minutesMarkdown(m1);
check('Markdown 含标题与日期', md.includes('# ' + m1.title) && md.includes(m1.date));
check('Markdown 含待办表格', md.includes('| 事项 | 负责人 | 截止 | 状态 |'));
check('Markdown 含全部待办', m1.minutes.action_items.every(a => md.includes(a.task)));
check('Markdown 无未解析标记', !md.includes('[['));

const csv = actionsCsv(m1);
check('CSV 带 BOM 便于 Excel 打开', csv.charCodeAt(0) === 0xFEFF);
check('CSV 行数等于待办数 + 表头 + 换行', csv.trim().split('\r\n').length === m1.minutes.action_items.length + 1);
check('CSV 字段被正确引用', csv.includes('"' + m1.minutes.action_items[0].task + '"'));

check('对话记录导出在无对话时给出提示', /还没有对话内容/.test(conversationMarkdown()));

/* ── 5. 前端资源一致性 ── */
console.log('\n[5] 资源一致性');
const html = fs.readFileSync(path.join(DIR, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(DIR, 'styles.css'), 'utf8');
const ids = new Set((html.match(/id="([^"]+)"/g) || []).map(s => s.slice(4, -1)));
const dynamicIds = new Set(['welcome', 'exportMenu']);
const refs = [...new Set((appSrc.match(/\$\('#([A-Za-z0-9_-]+)'\)/g) || [])
  .map(s => s.slice(4, -2)))].filter(id => !dynamicIds.has(id));
const missing = refs.filter(id => !ids.has(id));
check('脚本引用的 DOM 元素都存在', missing.length === 0, missing.join(', '));
check('样式表定义了主题变量', css.includes('[data-theme="dark"]') && css.includes('--primary'));
check('样式表支持降低动效偏好', css.includes('prefers-reduced-motion'));
check('页面引入了全部资源', ['styles.css', 'mock-data.js', 'app.js'].every(f => html.includes(f)));
check('未使用 emoji 作为图标', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(html));

/* ── 汇总 ── */
console.log('\n' + '─'.repeat(52));
if (failures.length) {
  console.log('失败 ' + failures.length + ' 项 / 通过 ' + pass + ' 项');
  failures.forEach(f => console.log('  · ' + f));
  process.exit(1);
}
console.log('全部通过：' + pass + ' 项检查');