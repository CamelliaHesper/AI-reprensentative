// 填写助手：根据用户自然语言要求生成可审查的字段回填补丁。
// 对应本机完整版 app/form_assistant.py 的能力，无会话状态（字段由前端传入）。
export async function POST(req: Request) {
  const origin = req.headers.get('origin');
  if (origin) {
    const seen = new URL(req.url).hostname, from = new URL(origin).hostname;
    // 托管网关会把请求转发到内部地址，公网域名与内部 host 不一致属正常，按域名后缀放行
    if (from !== seen && !from.endsWith('.workbuddy.host') && from !== 'localhost' && from !== '127.0.0.1')
      return Response.json({ error: '请求来源不正确' }, { status: 403 });
  }
  try {
    const raw = await req.text();
    if (raw.length > 120000) throw Error('内容过长，请精简后重试');
    const { fields, message } = JSON.parse(raw);
    const key = process.env.MODEL_API_KEY, base = process.env.MODEL_BASE_URL, model = process.env.MODEL_NAME;
    if (!key || !base || !model) return Response.json({ error: '服务端模型尚未配置，请联系维护者' }, { status: 503 });
    if (typeof message !== 'string' || !message.trim() || message.length > 2000) throw Error('请输入 1～2000 字的要求');
    if (!Array.isArray(fields) || !fields.length || fields.length > 60) throw Error('表单字段不完整');
    for (const f of fields) {
      if (typeof f?.target !== 'string' || typeof f?.label !== 'string') throw Error('表单字段不完整');
      if (typeof f.value === 'string' && f.value.length > 15000) throw Error('字段内容过长');
    }
    const context = fields.map((f: any) => ({
      target: f.target, label: String(f.label).slice(0, 100),
      help: String(f.help || '').slice(0, 600), value: String(f.value || '').slice(0, 15000),
    }));
    const system = '你是表单填写助手。按用户要求仅修改明确对应的字段，其他字段不要返回。' +
      '只能使用用户请求或表单中明确提供的真实事实，禁止编造荣誉、职务、成绩和经历。' +
      '信息不足先在 reply 中追问，updates 留空。' +
      '总结类字段必须遵守 help 和用户指定的字数上限。' +
      '排名百分比不要自行计算：只有用户明确给出第几名及总人数时，才填写 rank_target、rank_position、rank_total，且不要在 updates 中填排名或百分比。' +
      'rank_target 应选择标签含“百分比/占比/%”的字段。' +
      '“2/30”表示第2名共30人；“2到30”含义不明，必须追问。' +
      '返回严格JSON：{"reply":"给用户的回复","updates":[{"target":"字段target","value":"新值"}],"rank_target":"","rank_position":null,"rank_total":null}。回复用中文。';
    const res = await fetch(base.replace(/\/$/, '') + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${key}` },
      body: JSON.stringify({
        model,
        messages: [{ role: 'system', content: system }, { role: 'user', content: '用户要求：' + message + '\n当前表单：' + JSON.stringify(context) }],
        response_format: { type: 'json_object' }, max_tokens: 4000,
      }),
      signal: AbortSignal.timeout(90000),
    });
    if (!res.ok) return Response.json({ error: res.status === 402 ? '模型账户余额不足' : '模型服务暂时不可用，请稍后重试' }, { status: 502 });
    const json: any = await res.json();
    const answer = JSON.parse(json.choices?.[0]?.message?.content || '{}');
    const byTarget = new Map(fields.map((f: any) => [f.target, f]));
    const patches = new Map<string, string>();
    for (const u of Array.isArray(answer.updates) ? answer.updates : []) {
      if (!byTarget.has(u?.target)) throw Error('模型返回了无效字段，请重新描述要求');
      patches.set(u.target, String(u.value ?? ''));
    }
    // 排名百分比由服务端计算，不信任模型算术（移植自 form_assistant.py）。
    // 模型未返回 rank_target 但消息中明确给出排名时，服务端同样兜底处理。
    const explicit = [...message.matchAll(/(?<!\d)(\d+)\s*[/／]\s*(\d+)(?!\d)/g)];
    const named = message.match(/第\s*(\d+)\s*名[^0-9]{0,12}(?:共|总(?:人数)?|全系|全班|全院)?\s*(\d+)\s*人/);
    let n = NaN, total = NaN;
    if (explicit.length === 1) { n = Number(explicit[0][1]); total = Number(explicit[0][2]); }
    else if (named) { n = Number(named[1]); total = Number(named[2]); }
    else if (answer.rank_target) { n = Number(answer.rank_position); total = Number(answer.rank_total); }
    if (answer.rank_target || !Number.isNaN(n)) {
      if (!Number.isInteger(n) || !Number.isInteger(total) || n < 1 || n > total)
        throw Error('请明确排名和总人数，例如第 2 名，共 30 人');
      const percent = (f: any) => /百分|占比|[%％]/.test(f.label);
      const ratio = (f: any) => f.label.includes('排名') && !percent(f);
      const percentages = fields.filter(percent).map((f: any) => f.target);
      let target = String(answer.rank_target || '');
      if (!byTarget.has(target) || !percentages.includes(target)) {
        if (percentages.length === 1) target = percentages[0];
        else return Response.json({
          reply: `第 ${n} 名、共 ${total} 人，对应前 ${(Math.round(n * 10000 / total) / 100).toFixed(2)}%，但未找到唯一的百分比填写位置。请明确要填写哪个百分比字段。`,
          updates: [],
        }, { headers: { 'Cache-Control': 'no-store' } });
      }
      const pct = (Math.round(n * 10000 / total) / 100).toFixed(2);
      for (const k of [...patches.keys()]) {
        const f = byTarget.get(k);
        if (ratio(f) || percentages.includes(k)) patches.delete(k);
      }
      if (byTarget.get(target).label.includes('总人数')) patches.set(target, `${n}/${total}（${pct}%）`);
      else {
        patches.set(target, `${pct}%`);
        const ratios = fields.filter(ratio).map((f: any) => f.target);
        if (ratios.length === 1) patches.set(ratios[0], `${n}/${total}`);
      }
      answer.reply = `第 ${n} 名、共 ${total} 人，对应前 ${pct}%。已按字段分别填写，请核对。`;
    }
    // 字数上限：取 help 与用户要求中的最小值
    for (const [target, value] of patches) {
      const f = byTarget.get(target);
      if (f.label.includes('排名') && !/百分|占比|[%％]/.test(f.label) && value.includes('%'))
        throw Error('不能把百分比写入排名字段，请重新要求填写“排名百分比”');
      const limits = [...(String(f.help || '') + ' ' + message).matchAll(/(\d+)\s*字/g)].map(m => Number(m[1]));
      const limit = Math.min(15000, ...limits);
      if (value.length > limit) patches.set(target, value.slice(0, limit));
    }
    return Response.json(
      { reply: String(answer.reply || '已按要求处理，请核对回填内容。'), updates: [...patches].map(([target, value]) => ({ target, value })) },
      { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return Response.json(
      { error: e instanceof Error && /要求|字段|排名|百分比|过长|不完整/.test(e.message) ? e.message : '助手暂时无法处理，请换种说法或稍后重试' },
      { status: 400, headers: { 'Cache-Control': 'no-store' } });
  }
}
