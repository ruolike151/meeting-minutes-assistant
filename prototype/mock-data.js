/* =========================================================
   模拟数据 —— 交互原型专用
   正式版由后端 API 返回，此处结构对齐《产品设计文档》第 6 节
   ========================================================= */

const SPEAKERS = {
  p_chen: { id: 'p_chen', name: 'A', role: '运营',    short: '陈', color: 'var(--sp-1)' },
  p_li:   { id: 'p_li',   name: 'B', role: '设计',    short: '李', color: 'var(--sp-2)' },
  p_wang: { id: 'p_wang', name: 'C', role: '前端开发', short: '王', color: 'var(--sp-3)' },
  p_zhao: { id: 'p_zhao', name: 'D', role: '项目负责人', short: '赵', color: 'var(--sp-4)' },
  p_sun:  { id: 'p_sun',  name: 'E', role: '测试',    short: '孙', color: 'var(--sp-5)' }
};

const MEETINGS = [
  /* ─────────────── 会议 1 ─────────────── */
  {
    id: 'm_20260911',
    title: '官网改版周会',
    date: '2026-09-11',
    time: '10:00',
    duration: 1660,
    status: 'processed',
    audio: '官网改版周会_0911.mp3',
    participants: ['p_chen', 'p_li', 'p_wang', 'p_zhao', 'p_sun'],

    segments: [
      { id: 's1',  start: 3,    end: 42,   speaker: 'p_zhao', confidence: 0.96,
        text: '各位早，今天同步一下本周官网改版项目的进度，大概二十分钟。先请运营这边说一下需求收集的情况。' },
      { id: 's2',  start: 42,   end: 190,  speaker: 'p_chen', confidence: 0.94,
        text: '我们这周一共收集了二十条用户反馈，主要集中在两个问题上：第一是首页入口太深，用户找不到核心服务入口；第二是活动弹窗不够醒目，目前点击转化率只有百分之一点二。需求已经整理成文档同步给设计了。' },
      { id: 's3',  start: 190,  end: 445,  speaker: 'p_li',   confidence: 0.95,
        text: '收到。我这边首页新版初稿已经完成了，把入口层级从三级简化成两级，活动展示区域的面积扩大了一倍。目前只差配色细节的微调，今天中午之前可以把全套设计稿交付给开发。' },
      { id: 's4',  start: 445,  end: 487,  speaker: 'p_zhao', confidence: 0.97,
        text: '中午交付没问题吧？这个时间点要卡住，后面开发的排期比较紧。' },
      { id: 's5',  start: 487,  end: 640,  speaker: 'p_wang', confidence: 0.93,
        text: '我这边代码框架已经准备就绪了，在等设计稿定稿。旧页面目前运行正常，没有遗留的 bug。拿到设计稿之后，大概需要三个工作日完成迭代开发。' },
      { id: 's6',  start: 640,  end: 736,  speaker: 'p_zhao', confidence: 0.96,
        text: '三个工作日，那下周三能提测？测试那边需要提前排期。' },
      { id: 's7',  start: 736,  end: 870,  speaker: 'p_sun',  confidence: 0.91,
        text: '可以，下周三提测的话我提前一天准备用例。不过有一点要提醒，这次改版的埋点如果要一起上，需要提前跟数据那边对齐口径，不然上线了统计不准。' },
      { id: 's8',  start: 870,  end: 960,  speaker: 'p_zhao', confidence: 0.95,
        text: '埋点这块谁来跟？' },
      { id: 's9',  start: 960,  end: 1100, speaker: 'p_chen', confidence: 0.92,
        text: '我们运营跟吧，我这边本来就要看转化数据，顺带一起对齐了。' },
      { id: 's10', start: 1100, end: 1320, speaker: 'p_zhao', confidence: 0.96,
        text: '好。另外说一下风险，现在距离月底上线还有十九天，如果设计稿延迟超过一天，整个排期就要顺延。这个要提前预警，不要等到最后才发现。' },
      { id: 's11', start: 1320, end: 1530, speaker: 'p_li',   confidence: 0.94,
        text: '明白，如果今天中午有意外情况，我上午就会同步给大家。' },
      { id: 's12', start: 1530, end: 1660, speaker: 'p_zhao', confidence: 0.97,
        text: '行，那今天就这样。总结一下：设计今天中午交付全套稿，开发拿到就开始，下周三提测，运营负责埋点对齐。有问题群里同步。' }
    ],

    minutes: {
      summary: [
        { topic: '用户反馈与需求', evidence: ['s2'],
          points: [
            '本周收集 20 条用户反馈，集中在入口过深与弹窗不醒目',
            '活动弹窗当前点击转化率 1.2%',
            '需求已整理成文档并同步设计'
          ] },
        { topic: '设计进度', evidence: ['s3'],
          points: [
            '首页新版初稿已完成，入口层级由三级简化为两级',
            '活动展示区域面积扩大一倍',
            '今日中午前交付全套设计稿'
          ] },
        { topic: '开发与测试排期', evidence: ['s5', 's6', 's7'],
          points: [
            '开发框架已就绪，设计稿定稿后约需 3 个工作日',
            '计划下周三提测，测试提前一天准备用例',
            '埋点统计口径需提前与数据团队对齐'
          ] }
      ],

      decisions: [
        { content: '首页入口层级由三级简化至两级，活动展示区面积扩大一倍。', evidence: ['s3'], confidence: 0.94 },
        { content: '上线节奏维持月底不变，开发完成后下周三提测。', evidence: ['s6', 's10'], confidence: 0.91 },
        { content: '本次改版埋点口径由运营侧负责对齐。', evidence: ['s8', 's9'], confidence: 0.88 }
      ],

      action_items: [
        { id: 'a1', task: '交付首页改版全套设计稿', owner: 'p_li', owner_source: 'explicit',
          due: '2026-09-11 12:00', deadline: '今天中午', status: 'pending',
          evidence: ['s3'], needs_review: false },
        { id: 'a2', task: '完成前端迭代开发（约 3 个工作日）', owner: 'p_wang', owner_source: 'explicit',
          due: '2026-09-16', deadline: '下周三', status: 'pending',
          evidence: ['s5', 's6'], needs_review: false },
        { id: 'a3', task: '准备测试用例并完成提测', owner: 'p_sun', owner_source: 'explicit',
          due: '2026-09-16', deadline: '下周三', status: 'pending',
          evidence: ['s7'], needs_review: false },
        { id: 'a4', task: '与数据团队对齐埋点统计口径', owner: 'p_chen', owner_source: 'explicit',
          due: null, deadline: null, status: 'pending',
          evidence: ['s7', 's9'], needs_review: true,
          review_reason: '会上确认了负责人，但没有约定完成时间' }
      ],

      open_questions: [
        { content: '埋点统计口径需要在开发提测前完成对齐，具体时间未定。', evidence: ['s7'] }
      ],

      risks: [
        { content: '距月底上线仅剩 19 天，设计稿若延迟超过一天，整个排期将顺延。', evidence: ['s10'] }
      ]
    }
  }

,
  /* ─────────────── 会议 2 ─────────────── */
  {
    id: 'm_20260909',
    title: '会员体系二期需求评审',
    date: '2026-09-09',
    time: '14:30',
    duration: 3120,
    status: 'processed',
    audio: '会员体系二期评审_0909.mp3',
    participants: ['p_chen', 'p_wang', 'p_zhao', 'p_sun'],

    segments: [
      { id: 's1', start: 12,   end: 96,   speaker: 'p_sun',  confidence: 0.95,
        text: '今天评审会员体系二期的需求，主要两块内容：积分规则和等级规则。我们先看积分这一块。' },
      { id: 's2', start: 96,   end: 342,  speaker: 'p_wang', confidence: 0.93,
        text: '积分规则我看了一遍，有一个问题：用户退款之后积分怎么处理？现在文档里没有写这一条。' },
      { id: 's3', start: 342,  end: 520,  speaker: 'p_chen', confidence: 0.92,
        text: '退款扣回，这个我们运营侧是明确的，退款当天扣回对应的积分。' },
      { id: 's4', start: 520,  end: 726,  speaker: 'p_wang', confidence: 0.94,
        text: '那如果积分已经被用户花掉了呢？是扣成负数，还是直接冻结账户？这两种处理方式对开发来说差别很大。' },
      { id: 's5', start: 726,  end: 900,  speaker: 'p_zhao', confidence: 0.96,
        text: '这个会上定不了，需要跟财务确认。先记为待确认事项，不要卡住后面的评审。' },
      { id: 's6', start: 900,  end: 1120, speaker: 'p_sun',  confidence: 0.94,
        text: '等级这块，升级规则是按自然月累计，还是滚动十二个月？这两种算法不一样。' },
      { id: 's7', start: 1120, end: 1300, speaker: 'p_chen', confidence: 0.91,
        text: '我们倾向滚动十二个月，这样跟会员的有效期是对齐的，用户理解成本也低一些。' },
      { id: 's8', start: 1300, end: 1420, speaker: 'p_zhao', confidence: 0.95,
        text: '那就滚动十二个月，这个定了。C 你评估一下工作量。' },
      { id: 's9', start: 1420, end: 1700, speaker: 'p_wang', confidence: 0.93,
        text: '滚动计算需要额外维护一张积分流水表，工作量大概增加两天，整体算下来八天左右。' },
      { id: 's10', start: 1700, end: 1980, speaker: 'p_zhao', confidence: 0.96,
        text: '八天可以接受，那这版就按滚动十二个月来做。退款积分的问题，下周一之前跟财务确认，E 你来跟这件事。' }
    ],

    minutes: {
      summary: [
        { topic: '积分规则的争议点', evidence: ['s2', 's3', 's4', 's5'],
          points: [
            '退款当天扣回积分，运营侧规则明确',
            '积分已被消费后的处理方式（扣成负数 / 冻结账户）未定',
            '需与财务确认，会上记为待确认事项'
          ] },
        { topic: '等级升级规则', evidence: ['s6', 's7', 's8'],
          points: [
            '在自然月累计与滚动 12 个月之间选择',
            '最终确定采用滚动 12 个月，与会员有效期对齐'
          ] },
        { topic: '开发工作量', evidence: ['s9', 's10'],
          points: [
            '滚动计算需新增积分流水表，工作量增加约 2 天',
            '二期整体工作量约 8 天，评审通过'
          ] }
      ],

      decisions: [
        { content: '会员等级升级规则采用滚动 12 个月累计。', evidence: ['s7', 's8'], confidence: 0.93 },
        { content: '会员体系二期开发工作量按 8 天评估，方案通过。', evidence: ['s9', 's10'], confidence: 0.9 },
        { content: '退款积分若已被消费的处理方式不在本次评审定夺，需与财务确认。', evidence: ['s5'], confidence: 0.92 }
      ],

      action_items: [
        { id: 'a1', task: '与财务确认退款后积分已消费的处理方式', owner: 'p_sun', owner_source: 'explicit',
          due: '2026-09-14', deadline: '下周一之前', status: 'pending',
          evidence: ['s5', 's10'], needs_review: false },
        { id: 'a2', task: '新增积分流水表并实现滚动 12 个月等级计算', owner: 'p_wang', owner_source: 'explicit',
          due: null, deadline: null, status: 'pending',
          evidence: ['s8', 's9'], needs_review: true,
          review_reason: '会上未约定该任务的完成时间' }
      ],

      open_questions: [
        { content: '积分已被消费后发生退款，是扣成负数还是冻结账户，需与财务确认。', evidence: ['s4', 's5'] }
      ],

      risks: [
        { content: '滚动 12 个月方案需要额外的积分流水表，存在历史积分数据迁移成本。', evidence: ['s9'] }
      ]
    }
  },

  /* ─────────────── 会议 3 ─────────────── */
  {
    id: 'm_20260904',
    title: '移动端体验专项讨论',
    date: '2026-09-04',
    time: '16:00',
    duration: 2700,
    status: 'processed',
    audio: '移动端体验专项_0904.mp3',
    participants: ['p_li', 'p_wang', 'p_zhao'],

    segments: [
      { id: 's1', start: 8,    end: 62,   speaker: 'p_zhao', confidence: 0.96,
        text: '今天过一下移动端体验专项，主要看启动速度。B 你这边有实测数据吗？' },
      { id: 's2', start: 62,   end: 268,  speaker: 'p_li',   confidence: 0.94,
        text: '有。我们用真机测了十二款机型，冷启动中位数是二点八秒，最慢的机型要四点一秒。用户反馈里，有百分之二十三的人提到打开慢。' },
      { id: 's3', start: 268,  end: 372,  speaker: 'p_zhao', confidence: 0.95,
        text: '行业基准大概在什么水平？' },
      { id: 's4', start: 372,  end: 520,  speaker: 'p_li',   confidence: 0.93,
        text: '同类产品普遍在两秒以内，我们这边的差距还是比较明显的。' },
      { id: 's5', start: 520,  end: 880,  speaker: 'p_wang', confidence: 0.94,
        text: '我看了一下，主要耗时在首页的首屏接口和图片资源上。首屏接口现在是串行调的三个，其实可以并发；图片没有做压缩，也没有做懒加载。' },
      { id: 's6', start: 880,  end: 980,  speaker: 'p_zhao', confidence: 0.96,
        text: '这两块优化下来能到什么程度？' },
      { id: 's7', start: 980,  end: 1240, speaker: 'p_wang', confidence: 0.92,
        text: '接口并发大概能省六百毫秒，图片优化能省四百到五百毫秒，加起来应该能进两秒以内。' },
      { id: 's8', start: 1240, end: 1520, speaker: 'p_zhao', confidence: 0.96,
        text: '那这个专项就按这两块做，C 你来负责，排到下个迭代里。另外 B 准备一份优化前后的对比数据，下次会上看效果。' },
      { id: 's9', start: 1520, end: 1620, speaker: 'p_li',   confidence: 0.95,
        text: '好的，没问题。' },
      { id: 's10', start: 1620, end: 1860, speaker: 'p_zhao', confidence: 0.94,
        text: '还有一个问题，老机型的兼容测试由谁来做？这件事我们一直没有明确下来。' }
    ],

    minutes: {
      summary: [
        { topic: '启动性能现状', evidence: ['s2', 's4'],
          points: [
            '12 款真机实测冷启动中位数 2.8 秒，最慢机型 4.1 秒',
            '23% 的用户反馈提到打开慢',
            '同类产品普遍在 2 秒以内'
          ] },
        { topic: '优化方向', evidence: ['s5', 's7'],
          points: [
            '首屏三个接口由串行改为并发，预计节省约 600 毫秒',
            '图片资源压缩与懒加载，预计节省 400–500 毫秒',
            '合计目标为冷启动进入 2 秒以内'
          ] }
      ],

      decisions: [
        { content: '移动端体验专项聚焦首屏接口并发化与图片资源优化两条主线。', evidence: ['s5', 's8'], confidence: 0.92 },
        { content: '该专项排入下一迭代，由 C 负责实现。', evidence: ['s8'], confidence: 0.94 }
      ],

      action_items: [
        { id: 'a1', task: '实现首屏接口并发调用、图片压缩与懒加载', owner: 'p_wang', owner_source: 'explicit',
          due: null, deadline: '下个迭代', status: 'pending',
          evidence: ['s5', 's8'], needs_review: false },
        { id: 'a2', task: '准备优化前后的启动耗时对比数据', owner: 'p_li', owner_source: 'explicit',
          due: null, deadline: '下次会议前', status: 'pending',
          evidence: ['s8'], needs_review: true,
          review_reason: '下次会议未给出具体日期' },
        { id: 'a3', task: '负责老机型兼容测试', owner: null, owner_source: 'missing',
          due: null, deadline: null, status: 'pending',
          evidence: ['s10'], needs_review: true,
          review_reason: '会上提出问题但未指定负责人' }
      ],

      open_questions: [
        { content: '老机型兼容测试由谁负责，会上未明确。', evidence: ['s10'] }
      ],

      risks: [
        { content: '最慢机型冷启动 4.1 秒，与同类产品 2 秒基准差距明显，存在用户流失风险。', evidence: ['s2', 's4'] }
      ]
    }
  }
];