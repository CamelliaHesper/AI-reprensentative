"""Build filenames from notice rules and confirmed form fields."""
import re
from pathlib import Path


def output_name(task,state):
    extension=state.get('word_format','docx')
    pattern=state.get('option',{}).get('naming_pattern') or task.get('analysis',{}).get('naming_pattern','')
    if not pattern:
        text='\n'.join([task.get('text',''),*task.get('metadata',{}).get('supplements',[])])
        match=re.search(r'(?:文件命名|命名|文件名)\s*[：:]\s*([^\n。；;]+)',text)
        if match and '姓名' in match[1] and '学号' in match[1]:
            pattern=match[1].strip().replace('姓名','{姓名}').replace('学号','{学号}')
            pattern=re.sub(r'优秀共青团员[/／]干部', '{申报事项}', pattern)
    if not pattern:return Path(state.get('template_name','申请材料')).stem+'_已填写.'+extension
    values={re.sub(r'\s','',f['label']):str(f.get('value','')).strip() for f in state.get('fields',[])}
    values['申报事项']=state.get('option',{}).get('title','')
    values['事项']=values['申报事项']
    def replace(match):
        value=values.get(match[1],'')
        if not value:raise ValueError('文件命名需要填写：'+match[1])
        return value
    try:name=re.sub(r'\{([^{}]+)\}',replace,pattern)
    except ValueError:return Path(state['template_name']).stem+'_已填写.'+extension
    name=re.sub(r'\.(?:docx?|pdf)$','',name,flags=re.I)
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',name).strip(' .')[:160]
    if not name:return '申请材料.'+extension
    return name+'.'+extension
