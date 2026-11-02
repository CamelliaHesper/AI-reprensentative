"""Generate reviewable form patches; never generate documents or save on behalf of users."""
import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pydantic import BaseModel, Field
from .fulfillment import get_state
from .model_client import model_json


class Patch(BaseModel):
    target: str
    value: str


class Suggestion(BaseModel):
    reply: str
    updates: list[Patch] = Field(default_factory=list)
    rank_target: str = ''
    rank_position: int | None = None
    rank_total: int | None = None


def suggest(store, tid, cfg, revision, values, message):
    state = get_state(store, tid)
    if not state or state.get('stale') or state['revision'] != revision:
        raise ValueError('表单已变化，请刷新后重试')
    fields = {f['target']: f for f in state['fields']}
    if set(values) != set(fields) or any(len(v) > 15000 for v in values.values()):
        raise ValueError('表单字段不完整或内容过长')
    if not message.strip() or len(message) > 2000:
        raise ValueError('请输入 1—2000 字的要求')
    context = [dict(target=k, label=f['label'], help=f.get('help', ''),
                    value=values[k], template_example=values[k] == f.get('original')
                    and f.get('source') != '个人资料') for k, f in fields.items()]
    answer = model_json(cfg, Suggestion,
        '你是表单填写助手。按用户要求仅修改明确对应的字段，其他字段不要返回。'
        '只能使用用户请求或表单中明确提供的真实事实，template_example=true 的原模板值不能作为用户经历。'
        '信息不足先在 reply 中追问，updates 留空；禁止编造荣誉、职务、成绩。'
        '总结必须遵守字段 help 和用户指定字数上限。排名百分比不要自行计算：'
        '只有明确第几名及总人数时填写 rank_target、rank_position、rank_total，且不要在 updates 中填排名。'
        'rank_target应选择“排名百分比”字段，不能选择独立的“排名/总人数”；这两个字段分别保留2/30和6.67%。'
        '只有原模板把排名、总人数、百分比放在同一字段时才选择合并字段。'
        '“2/30”表示第2名共30人；“二到三十”“2到30”含义不明，必须追问。'
        '回复用中文。用户要求：' + message + '\n当前表单：' + json.dumps(context, ensure_ascii=False))
    patches = {p.target: p.value for p in answer.updates}
    if len(patches) != len(answer.updates) or set(patches) - set(fields):
        raise ValueError('模型返回了无效字段，请重新描述要求')
    if answer.rank_target:
        target = answer.rank_target
        n, total = answer.rank_position, answer.rank_total
        if target not in fields or n is None or total is None or not 1 <= n <= total:
            raise ValueError('请明确排名和总人数，例如第 2 名，共 30 人')
        explicit=re.findall(r'(?<!\d)(\d+)\s*[/／]\s*(\d+)(?!\d)',message)
        if len(explicit)==1:
            n,total=map(int,explicit[0])
            if not 1<=n<=total:raise ValueError('排名应在1到总人数之间')
        percentage = (Decimal(n) * 100 / Decimal(total)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        def percent_field(f):return bool(re.search(r'百分|占比|[%％]',f['label']))
        def ratio_field(f):return '排名' in f['label'] and not percent_field(f)
        percentages=[k for k,f in fields.items() if percent_field(f)]
        # Treat the model target as a suggestion; the server verifies field meaning.
        if target not in percentages:
            if len(percentages)==1:target=percentages[0]
            else:
                return dict(revision=revision,reply=f'排名 {n}/{total} 对应前 {percentage}%，但未找到唯一的百分比填写位置。请明确要填写哪个百分比字段；原排名保持不变。',updates=[])
        for key in list(patches):
            if ratio_field(fields[key]) or key in percentages:patches.pop(key)
        if '总人数' in fields[target]['label']:
            patches[target]=f'{n}/{total}（{percentage}%）'
        else:
            patches[target]=f'{percentage}%'
            ratios=[k for k,f in fields.items() if ratio_field(f)]
            if len(ratios)==1:patches[ratios[0]]=f'{n}/{total}'
        answer.reply = f'第 {n} 名、共 {total} 人，对应前 {percentage}%。已按字段分别填写，排名不会被百分比覆盖，请核对。'
    for target, value in patches.items():
        f = fields[target]
        if '排名' in f['label'] and not re.search(r'百分|占比|[%％]',f['label']) and '%' in value:
            raise ValueError('不能把百分比写入排名字段，请重新要求填写“排名百分比”')
        limits = re.findall(r'(\d+)\s*字', f.get('help', '') + ' ' + message)
        limit = min([15000] + [int(x) for x in limits])
        if len(value) > limit:
            raise ValueError(f'生成的“{f["label"]}”超过 {limit} 字，请重试或要求缩短')
        if f.get('choices') and value not in f['choices']:
            raise ValueError('模型返回的值不在可选项中')
    return dict(revision=revision, reply=answer.reply,
                updates=[dict(target=k, value=v) for k, v in patches.items()])
