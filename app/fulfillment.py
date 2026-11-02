import json,uuid,threading,hashlib,re
from pathlib import Path
from pydantic import BaseModel,Field
from .documents import list_documents
from .word_templates import index_template,fill_template,fill_original_doc,office_convert
from .model_client import model_json

office_lock=threading.Lock()
BUSY={'queued','processing','receiving','collecting_files','preparing','generating'}

class FormField(BaseModel):
    target:str
    label:str
    kind:str='text'
    choices:list[str]=Field(default_factory=list)
    required:bool=False
    help:str=''
    repeat_group:str=''
    repeat_row:int=Field(default=0,ge=0)

class TemplateForm(BaseModel):
    fields:list[FormField]
    notes:list[str]=Field(default_factory=list)

def apply_optional_fields(state):
    # 获奖经历可能不存在；此类明细不能阻止申请人提交。
    for field in state.get('fields',[]):
        if any(word in field.get('label','') for word in ('获奖','奖项','颁奖','奖励情况')):
            field['required']=False
    return state

def get_state(store,tid):
    with store.connect() as c:r=c.execute('SELECT data FROM fulfillment WHERE task_id=?',(tid,)).fetchone()
    state=json.loads(r['data']) if r else None
    if state:
        apply_optional_fields(state)
        state['stale']=state.get('analysis_key')!=analysis_key(store,tid)
        from .output_naming import output_name
        state['output_name']=output_name(store.get(tid),state)
        old_name=state.get('sent_file_name',Path(state.get('template_name','申请材料')).stem+'_已填写.'+state.get('word_format','docx'))
        if state.get('sent_message_id') and old_name!=state['output_name']:
            state.pop('sent_message_id',None);state.pop('send_request_id',None)
    return state

def analysis_key(store,tid):
    task=store.get(tid)
    dependency={'analysis':task['analysis'],'files':[f['id'] for f in task['files']],
        'resources':task['metadata'].get('resources',[]),'supplements':task['metadata'].get('supplements',[])}
    if task['metadata'].get('analysis_revision'):dependency['analysis_revision']=task['metadata']['analysis_revision']
    return hashlib.sha256(json.dumps(dependency,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def save_state(store,tid,state):
    with store.connect() as c:c.execute('INSERT OR REPLACE INTO fulfillment VALUES (?,?)',(tid,json.dumps(state,ensure_ascii=False)))

def doc_for(store,tid,did):
    d=next((x for x in list_documents(store,tid) if x['id']==did),None)
    if not d:raise ValueError('文档不属于当前任务')
    return d

def template_path(store,doc):
    ext=Path(doc['name']).suffix.lower()
    if ext=='.docx':
        import shutil
        root=store.root/'prepared'/doc['id'];root.mkdir(parents=True,exist_ok=True)
        target=root/'source.docx'
        if not target.exists():shutil.copyfile(doc['path'],target)
        return target
    if ext!='.doc':raise ValueError('请选择 Word 申请表模板')
    root=store.root/'prepared'/doc['id'];root.mkdir(parents=True,exist_ok=True);target=root/'template.docx'
    with office_lock:
        if not target.exists():
            import shutil
            source=root/'original.doc';shutil.copyfile(doc['path'],source);office_convert(source,target)
    return target

def prepare_form(store,tid,option_id,template_id,cfg):
    option=next((x for x in store.get(tid)['analysis'].get('options',[]) if x['id']==option_id),None)
    if not option:raise ValueError('所选事项不存在，请重新解析需求')
    if template_id not in option.get('template_ids',[]):raise ValueError('模板不属于所选事项')
    doc=doc_for(store,tid,template_id);targets=index_template(template_path(store,doc))
    form=model_json(cfg,TemplateForm,'识别所选事项中需要申请人填写的字段。事项：'+json.dumps(option,ensure_ascii=False)+
        '\n模板位置：'+json.dumps(targets,ensure_ascii=False)+
        '\n只选择值单元格或需要替换的整段，不选择标签、标题、学校审核或签章栏。target只能使用输入的真实ID且不可重复。'
        'label为字段名，kind为text/textarea/select/date/number，choices必须有原文依据。不要编造申报事实。'
        'required默认false，只有通知或模板明确要求申请人必须提供的信息才能标true；不能因为表格有这一栏、'
        '有模板示例值或处于第一行就设为必填。获奖情况（时间、奖项名称、颁奖单位及所有重复行）均为选填，'
        '没有获奖经历时允许全部留空。经历类重复行不要求每行填写。'
        '精简交互：社会活动、获奖等相同结构的多行字段，用repeat_group标记同一经历类别，repeat_row从1开始标记行号；'
        '每条经历的时间、单位、职务仍是不同字段，不可混成一个值。普通基本信息repeat_group留空、repeat_row=0。'
        '不提取纯提示语或说明段作为填写字段，不把同一单元格重复列出。保留真实填写位置，不因语义近似删除不同用途字段。')
    lookup={x['id']:x for x in targets};seen=set();fields=[];profile=store.profile_for_task(store.get(tid))
    for f in form.fields:
        if f.target not in lookup or f.target in seen:raise ValueError('模型字段定位无效，请重新识别')
        seen.add(f.target);original=lookup[f.target]['text'];value=original;source='模板原值'
        for k,v in profile.items():
            if ''.join(k.split())==''.join(f.label.split()) and v:value=v;source='个人资料';break
        fields.append({**f.model_dump(),'original':original,'value':value,'source':source})
    if not fields:raise ValueError('未识别到可填写字段，请检查模板')
    state={'revision':uuid.uuid4().hex,'analysis_key':analysis_key(store,tid),'option':option,'document_id':template_id,'template_name':doc['name'],
        'fields':fields,'notes':form.notes,'stage':'filling','outputs':[]}
    apply_optional_fields(state)
    save_state(store,tid,state);return state

def update_values(store,tid,revision,values):
    state=get_state(store,tid)
    if state and state.get('editor',{}).get('active'):raise ValueError('请先保存或取消在线编辑，再修改表单')
    if not state or state['revision']!=revision or state.get('stale'):raise ValueError('需求或表单已变化，请重新选择事项并核对')
    if not set(values)<={f['target'] for f in state['fields']} or any(len(v)>15000 for v in values.values()):raise ValueError('字段无效或内容过长')
    for f in state['fields']:
        if f['target'] in values:f['value']=values[f['target']]
    state.update(revision=uuid.uuid4().hex,stage='review',outputs=[])
    state.pop('sent_message_id',None)
    state.pop('send_request_id',None)
    state.pop('editor',None)
    state.pop('edited_path',None)
    save_state(store,tid,state);return state

def generate(store,tid,revision,confirmed):
    state=get_state(store,tid)
    if not confirmed:raise ValueError('请先确认所选事项、模板和填写内容，再生成文件')
    if not state or state['revision']!=revision or state.get('stale') or state['stage'] not in {'review','generated'}:raise ValueError('请先保存表单并检查，修改或重新解析后需要重新确认')
    if state['stage']=='generated':return state
    missing=[f['label'] for f in state['fields'] if f['required'] and not f['value'].strip()]
    if missing:raise ValueError('请填写必填信息：'+'、'.join(missing))
    doc=doc_for(store,tid,state['document_id']);source=template_path(store,doc)
    root=store.root/'outputs'/tid/state['revision'];root.mkdir(parents=True,exist_ok=True)
    native=Path(doc['name']).suffix.lower()=='.doc'
    out=root/('filled.doc' if native else 'filled.docx');pdf=root/'preview.pdf'
    values={f['target']:f['value'] for f in state['fields']}
    # 字数提示是模板指导文字，不属于申请人填写内容；清空同一填写单元格内的独立提示段。
    targets=index_template(source)
    summary_cells={re.sub(r'p\d+$','',f['target']) for f in state['fields'] if any(x in f['label'] for x in ('个人总结','个人描述','个人简介'))}
    for t in targets:
        if re.sub(r'p\d+$','',t['id']) in summary_cells and re.fullmatch(r'[（(]?\s*\d+\s*字以内\s*[）)]?',t['text'].strip()):values[t['id']]=''
    if native:fill_original_doc(source.parent/'original.doc',source,out,values,normalize=True)
    else:fill_template(source,out,values,normalize=True)
    warnings=[]
    try:
        with office_lock:office_convert(out,pdf)
    except Exception:warnings.append('PDF版式预览生成失败，请下载 Word 后检查排版')
    state.update(stage='generated',word_format='doc' if native else 'docx',outputs=['docx']+(['pdf'] if pdf.exists() else []),warnings=warnings)
    save_state(store,tid,state);return state

def output_path(store,tid,state,kind):
    if kind=='pdf' and state.get('edited_path'):
        return Path(state['edited_path']).with_name('preview.pdf')
    if kind not in {'docx','pdf'} or kind not in state.get('outputs',[]):raise ValueError('生成文件不存在')
    if kind=='docx' and state.get('edited_path'):return Path(state['edited_path'])
    return store.root/'outputs'/tid/state['revision']/('filled.'+state.get('word_format','docx') if kind=='docx' else 'preview.pdf')
