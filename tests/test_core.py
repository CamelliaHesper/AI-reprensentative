import io
import zipfile
from pathlib import Path

from app.parsers import parse_file
from app.store import Store


def test_chinese_csv_and_cells(tmp_path):
    p = tmp_path / 'a.csv'
    p.write_bytes('姓名,专业\n张三,软件工程'.encode('gb18030'))
    r = parse_file(p, '报名.csv')
    assert r['status'] == 'ok'
    assert '张三' in r['text'] and '软件工程' in r['text']


def test_office_documents(tmp_path):
    from docx import Document
    from openpyxl import Workbook
    p = tmp_path / 'a.docx'
    d = Document(); d.add_paragraph('报名要求'); d.add_table(rows=1, cols=1).cell(0, 0).text = '姓名'
    d.save(p)
    assert '报名要求' in parse_file(p, 'a.docx')['text']
    assert '姓名' in parse_file(p, 'a.docx')['text']
    p = tmp_path / 'a.xlsx'
    w = Workbook(); w.active['A1'] = '申请理由'; w.create_sheet('第二页')['B2'] = '联系电话'; w.save(p)
    t = parse_file(p, 'a.xlsx')['text']
    assert '申请理由' in t and '第二页' in t and 'B2' in t


def test_xlsx_without_disk_extension_and_inside_zip(tmp_path):
    from openpyxl import Workbook
    buf=io.BytesIO(); w=Workbook();w.active['A1']='学院名额';w.save(buf)
    p=tmp_path/'opaque-id';p.write_bytes(buf.getvalue())
    assert '学院名额' in parse_file(p,'名额.xlsx')['text']
    zpath=tmp_path/'资料.zip'
    with zipfile.ZipFile(zpath,'w') as z:z.writestr('申请材料/名额.xlsx',buf.getvalue())
    r=parse_file(zpath,'资料.zip')
    assert '学院名额' in r['text'] and not any('InvalidFileException' in x for x in r['warnings'])


def test_zip_rejects_traversal_and_handles_supported_entries(tmp_path):
    p = tmp_path / 'a.zip'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('../outside.csv', 'bad')
    r = parse_file(p, 'a.zip')
    assert r['status'] == 'error' and not (tmp_path.parent / 'outside.csv').exists()
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('报名.csv', '姓名,张三')
        z.writestr('unknown.exe', b'never execute')
    r = parse_file(p, 'a.zip')
    assert '张三' in r['text'] and any('跳过' in x for x in r['warnings'])


def test_corrupt_file_is_visible(tmp_path):
    p = tmp_path / 'bad'; p.write_bytes(b'not office')
    assert parse_file(p, 'bad.docx')['status'] == 'error'


def test_store_deduplicates_and_recovers(tmp_path):
    s = Store(tmp_path)
    tid, created = s.create_task('message-1', '填写报名表', 'feishu', {})
    again, duplicate = s.create_task('message-1', '重复', 'feishu', {})
    assert tid == again and created and not duplicate
    assert s.claim_next()['id'] == tid
    assert s.claim_next() is None
    s.recover()
    assert s.claim_next()['id'] == tid
