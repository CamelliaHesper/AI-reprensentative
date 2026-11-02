from pathlib import Path
import io
from docx import Document
import pytest
from app.word_templates import index_template, fill_template
from app.documents import ingest_documents
from app.store import Store
from app.config import Settings


def template(path):
    d=Document();t=d.add_table(rows=2,cols=4);t.style='Table Grid'
    for cell,text in zip(t.rows[0].cells,['姓名','原姓名','性别','男']):cell.text=text
    t.cell(1,0).text='申请理由';t.cell(1,1).merge(t.cell(1,3)).text='原理由'
    d.save(path)


def test_template_target_replacement_keeps_table_structure(tmp_path):
    p=tmp_path/'source.docx';template(p)
    targets=index_template(p)
    name=next(x for x in targets if x['text']=='原姓名')
    reason=next(x for x in targets if x['text']=='原理由')
    out=tmp_path/'filled.docx'
    fill_template(p,out,{name['id']:'测试用户',reason['id']:'申请理由说明'})
    original=Document(p);filled=Document(out)
    assert original.tables[0].cell(0,1).text=='原姓名'
    assert filled.tables[0].cell(0,1).text=='测试用户'
    assert filled.tables[0].cell(0,3).text=='男'
    assert filled.tables[0]._tbl.tblPr.xml==original.tables[0]._tbl.tblPr.xml
    assert filled.sections[0]._sectPr.xml==original.sections[0]._sectPr.xml
    assert len(filled.tables[0].rows)==2
    with pytest.raises(ValueError):fill_template(p,out,{'not-a-target':'x'})


def test_multiline_cells_keep_original_paragraphs_and_untouched_xml(tmp_path):
    p=tmp_path/'source.docx';d=Document();cell=d.add_table(rows=1,cols=1).cell(0,0)
    cell.text='原第一段';cell.add_paragraph('原第二段').runs[0].bold=True;d.save(p)
    targets=index_template(p);first=next(x for x in targets if x['text']=='原第一段')
    second=next(x for x in targets if x['text']=='原第二段')
    out=tmp_path/'out.docx'
    fill_template(p,out,{first['id']:'原第一段',second['id']:'原第二段'})
    assert Document(out).tables[0]._tbl.xml==Document(p).tables[0]._tbl.xml
    fill_template(p,out,{first['id']:'新第一段'})
    cell=Document(out).tables[0].cell(0,0)
    assert len(cell.paragraphs)==2 and cell.paragraphs[1].text=='原第二段'
    assert cell.paragraphs[1].runs[0].bold


def test_native_cell_index_ignores_vertical_merge_continuation(tmp_path):
    p=tmp_path/'merged.docx';d=Document();t=d.add_table(rows=3,cols=2)
    t.cell(0,0).merge(t.cell(1,0)).text='合并标签';t.cell(1,1).text='第二行值';d.save(p)
    target=next(x for x in index_template(p) if x['text']=='第二行值')
    assert target['native_cell']==3 and target['native_row']==2


def test_doc_body_paragraph_job_supports_fill_date(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from app.word_templates import fill_original_doc
    index=tmp_path/'index.docx';doc=Document();doc.add_paragraph('申请表');doc.add_paragraph('填表时间： 年 月 日');doc.save(index)
    target=next(x for x in index_template(index) if '填表时间' in x['text'])
    out=tmp_path/'filled.doc';jobs=[]
    def run(args,**kwargs):
        jobs.extend(json.loads(Path(args[-1]).read_text(encoding='utf-8')))
        out.write_bytes(b'test doc');return SimpleNamespace(returncode=0)
    monkeypatch.setattr('app.word_templates.subprocess.run',run)
    fill_original_doc(tmp_path/'original.doc',index,out,{target['id']:'填表时间：2026年11月2日'})
    assert jobs[0]['kind']=='body_paragraph' and jobs[0]['body_paragraph']==2
    assert jobs[0]['expected']=='填表时间： 年 月 日'


def test_zip_documents_are_independent_with_pdf_text(tmp_path):
    import fitz,zipfile
    pdf=fitz.open();page=pdf.new_page();page.insert_text((50,50),'Application rules');raw=pdf.tobytes();pdf.close()
    z=io.BytesIO()
    with zipfile.ZipFile(z,'w') as archive:
        archive.writestr('通知.pdf',raw);archive.writestr('内容.csv','姓名,学校')
    s=Store(tmp_path/'data');tid,_=s.create_task('m','通知','local',{})
    s.add_file(tid,'材料.zip',z.getvalue())
    docs=ingest_documents(s,tid,Settings(data_dir=s.root))
    assert len(docs)==2
    assert any('Application rules' in x['result']['text'] for x in docs)
    assert len(ingest_documents(s,tid,Settings(data_dir=s.root)))==2


def test_generation_requires_confirmation_and_current_revision(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.fulfillment import save_state,get_state,analysis_key
    app=create_app(Settings(data_dir=tmp_path/'data'),run_worker=False);s=app.state.store
    tid,_=s.create_task('m','申请','local',{},status='awaiting_confirmation')
    source=tmp_path/'form.docx';template(source);s.add_file(tid,'表.docx',source.read_bytes())
    doc=ingest_documents(s,tid,Settings(data_dir=s.root))[0]
    target=next(x for x in index_template(source) if x['text']=='原姓名')['id']
    state={'revision':'r1','analysis_key':analysis_key(s,tid),'stage':'review','document_id':doc['id'],'template_name':'表.docx',
        'option':{'id':'member','title':'申请事项'},'fields':[{'target':target,'label':'姓名','value':'用户填写','original':'原姓名','required':True}],'outputs':[]}
    save_state(s,tid,state)
    def pdf_convert(src,dest):
        import fitz
        pdf=fitz.open();pdf.new_page();pdf.save(dest);pdf.close()
    monkeypatch.setattr('app.fulfillment.office_convert',pdf_convert)
    with TestClient(app) as c:
        assert c.post(f'/api/tasks/{tid}/generate',json={'revision':'r1','confirmed':False}).status_code==400
        assert c.post(f'/api/tasks/{tid}/generate',json={'revision':'stale','confirmed':True}).status_code==400
        assert get_state(s,tid)['outputs']==[]
        assert c.post(f'/api/tasks/{tid}/generate',json={'revision':'r1','confirmed':True}).status_code==200
        response=c.get(f'/api/tasks/{tid}/outputs/docx')
        d=Document(io.BytesIO(response.content))
        assert d.tables[0].cell(0,1).text=='用户填写'
        assert d.tables[0].cell(0,3).text=='男'
        assert c.put(f'/api/tasks/{tid}/form',json={'revision':'r1','values':{target:'再次修改'}}).status_code==200
        assert c.get(f'/api/tasks/{tid}/outputs/docx').status_code==404
        assert c.post(f'/api/tasks/{tid}/generate',json={'revision':'r1','confirmed':True}).status_code==400


def test_scanned_pdf_without_vision_does_not_claim_text_was_read(tmp_path):
    import fitz
    from app.parsers import parse_file
    p=tmp_path/'scan.pdf';pdf=fitz.open();pdf.new_page();pdf.save(p);pdf.close()
    result=parse_file(p,'scan.pdf',Settings(vision_api_key='',vision_model='',vision_base_url=''))
    assert any('未配置视觉模型' in w for w in result['warnings'])
    assert result['text']==''
    assert result['status']=='partial'
