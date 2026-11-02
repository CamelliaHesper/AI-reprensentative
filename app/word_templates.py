import copy
import subprocess
import sys
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def office_convert(source,destination):
    destination=Path(destination)
    result=subprocess.run([sys.executable,'-m','app.office_worker',str(source),str(destination)],
        cwd=Path(__file__).resolve().parent.parent,timeout=90,capture_output=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode or not destination.is_file():
        raise ValueError('本机 Word 转换不可用，请确认 Word 已安装并激活；原文件保持不变')
    return destination


def targets_for(doc):
    items=[]
    top_tables=doc.element.body.xpath('./w:tbl')
    def safe(p):return not p.xpath('.//w:drawing|.//w:object|.//w:fldChar|.//w:hyperlink|.//w:sdt')
    for i,cell in enumerate(doc.element.body.xpath('.//w:tc')):
        if cell.xpath('./w:tbl'):continue
        merge=cell.xpath('./w:tcPr/w:vMerge')
        if merge and merge[0].get(qn('w:val'))!='restart':continue
        row=cell.getparent();siblings=list(row)
        idx=siblings.index(cell)
        previous=next((c for c in reversed(siblings[:idx]) if c.tag==qn('w:tc')),None)
        label=''.join(n.text or '' for n in previous.iter(qn('w:t'))) if previous is not None else ''
        table=cell.getparent().getparent()
        native={'native_table':top_tables.index(table)+1,'native_cell':table.xpath('.//w:tc').index(cell)+1} if table in top_tables else {}
        if native:
            valid_cells=[]
            for candidate in table.xpath('.//w:tc'):
                vm=candidate.xpath('./w:tcPr/w:vMerge')
                if vm and vm[0].get(qn('w:val'))!='restart':continue
                valid_cells.append(candidate)
            native['native_cell']=valid_cells.index(cell)+1
            before=row.xpath('./w:trPr/w:gridBefore')
            column=1+(int(before[0].get(qn('w:val'),'0')) if before else 0)
            for previous_cell in siblings[:idx]:
                if previous_cell.tag!=qn('w:tc'):continue
                span=previous_cell.xpath('./w:tcPr/w:gridSpan')
                column+=int(span[0].get(qn('w:val'),'1')) if span else 1
            native.update(native_row=table.xpath('./w:tr').index(row)+1,native_column=column)
        paragraphs=cell.xpath('./w:p')
        for j,p in enumerate(paragraphs):
            if not safe(p):continue
            text=Paragraph(p,doc).text
            items.append(({'id':f'c{i}' if len(paragraphs)==1 else f'c{i}p{j}',
                'text':text,'label':label,'kind':'cell',**native,'native_paragraph':j+1},p))
    for i,p in enumerate(doc.element.body.xpath('./w:p')):
        text=Paragraph(p,doc).text
        if text.strip() and safe(p):items.append(({'id':f'p{i}','text':text,'label':'','kind':'paragraph','native_body_paragraph':i+1},p))
    return items


def index_template(path):
    return [item for item,_ in targets_for(Document(path))]


def fill_template(source,destination,values,normalize=False):
    from docx.oxml import OxmlElement
    doc=Document(source)
    lookup={item['id']:node for item,node in targets_for(doc)}
    if not set(values)<=set(lookup):raise ValueError('表单字段不属于当前模板，请重新识别')
    for key,value in values.items():
        p=lookup[key]
        if str(value)==Paragraph(p,doc).text and not normalize:continue
        texts=p.xpath('.//w:t')
        for text in texts:text.text=''
        for element in p.xpath('.//w:br|.//w:tab'):
            element.getparent().remove(element)
        if texts:
            anchor=texts[0];run=anchor.getparent();run.remove(anchor)
        else:
            runs=p.xpath('./w:r')
            run=runs[0] if runs else OxmlElement('w:r')
            if not runs:p.append(run)
        for i,line in enumerate(str(value).split('\n')):
            if i:run.append(OxmlElement('w:br'))
            text=OxmlElement('w:t');text.set(qn('xml:space'),'preserve');text.text=line;run.append(text)
        if normalize:
            from docx.shared import Pt, RGBColor
            for r in Paragraph(p,doc).runs:
                r.font.name='宋体';r.font.size=Pt(10.5);r.font.color.rgb=RGBColor(0,0,0);r.font.bold=False;r.font.italic=False
                r._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
    doc.save(destination)


def fill_original_doc(source,index_source,destination,values,normalize=False):
    """旧DOC直接由Word在原件内存副本中填值，避免DOCX转换改变分页。"""
    import json,tempfile
    lookup={x['id']:x for x in index_template(index_source)}
    changes=[]
    for key,value in values.items():
        t=lookup.get(key)
        if t and t.get('kind')=='paragraph' and t.get('native_body_paragraph'):
            changes.append({'kind':'body_paragraph','body_paragraph':t['native_body_paragraph'],
                'expected':t['text'],'value':str(value),'normalize':normalize})
            continue
        if not t or 'native_cell' not in t:raise ValueError('旧DOC的该字段不能安全定位，请先另存DOCX或改用表格字段')
        changes.append({'table':t['native_table'],'cell':t['native_cell'],'row':t['native_row'],'column':t['native_column'],
            'paragraph':t['native_paragraph'],'expected':t['text'],'value':str(value),'normalize':normalize})
    with tempfile.TemporaryDirectory(dir=Path(destination).parent) as td:
        job=Path(td)/'changes.json';job.write_text(json.dumps(changes,ensure_ascii=False),encoding='utf-8')
        result=subprocess.run([sys.executable,'-m','app.office_worker',str(source),str(destination),str(job)],
            cwd=Path(__file__).resolve().parent.parent,timeout=90,capture_output=True,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode or not Path(destination).is_file():
            reason=result.stderr.decode(errors='replace').strip().splitlines()[-1:]
            raise ValueError('旧DOC原模板字段定位或填写失败（'+''.join(reason)+'），未改动原文件')
