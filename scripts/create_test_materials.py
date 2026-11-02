"""生成虚构的人工验收材料；不读取 .env，不调用模型或飞书。"""
import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from docx import Document

ROOT = Path(__file__).resolve().parent.parent
NOTICE = '''【虚构测试通知，请勿实际申报】
2026年创新实践项目开始申报，分为“研究探索”和“社会实践”两类，每人仅可选择一类。
研究探索：填写《研究探索申请表》；社会实践：填写《社会实践申请表》。请勿混用模板。
两类均填写姓名、学号、学院、联系电话和个人总结，个人总结150字以内。
获奖情况可不填；没有获奖不影响提交。无需填写组织审核意见或盖章栏。
截止时间：2026年12月1日18:00。请将电子版Word发给测试负责人。
文件名要求：姓名+学号+申报类别.docx。
本通知没有在线表单，无需向任何真实平台提交。
'''


def generate(destination: Path, with_doc: bool = False):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / '通知.txt').write_text(NOTICE, encoding='utf-8')
    templates = []
    for category in ('研究探索', '社会实践'):
        doc = Document()
        doc.add_heading(f'{category}申请表（虚构测试）', 0)
        table = doc.add_table(rows=7, cols=2)
        table.style = 'Table Grid'
        labels = ('姓名', '学号', '学院', '联系电话', '个人总结', '获奖情况（选填）', '组织审核意见（申请人勿填）')
        for row, label in zip(table.rows, labels):
            row.cells[0].text = label
        path = destination / f'{category}申请表.docx'
        doc.save(path)
        templates.append(path)
    (destination / '参考.csv').write_text('类别,模板\n研究探索,研究探索申请表.docx\n社会实践,社会实践申请表.docx\n', encoding='utf-8-sig')
    with ZipFile(destination / '标准材料.zip', 'w', ZIP_DEFLATED) as archive:
        for path in [destination / '通知.txt', *templates, destination / '参考.csv']:
            archive.write(path, '测试材料/' + path.name)
    with ZipFile(destination / '混合材料.zip', 'w', ZIP_DEFLATED) as archive:
        for path in [destination / '通知.txt', *templates]:
            archive.write(path, '中文目录/' + path.name)
        archive.writestr('中文目录/不支持类型.xyz', b'unsupported test fixture')
    expected = {'options': ['研究探索', '社会实践'], 'deadline': '2026-12-01 18:00',
                'input': {'姓名': '测试同学', '学号': 'DEMO2026001', '学院': '测试学院',
                          '联系电话': '测试号码（非真实联系方式）', '个人总结': '参与资料整理和团队协作，按时完成创新实践项目测试任务。'},
                'optional': ['获奖情况'], 'do_not_fill': ['组织审核意见'],
                'filename': '测试同学+DEMO2026001+研究探索.docx'}
    (destination / '预期结果.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding='utf-8')
    if with_doc:
        import sys
        sys.path.insert(0, str(ROOT))
        from app.office_worker import run
        legacy = destination / '研究探索申请表.doc'
        run(str(templates[0].resolve()), str(legacy.resolve()))
        with ZipFile(destination / '旧DOC材料.zip', 'w', ZIP_DEFLATED) as archive:
            archive.write(legacy, '中文目录/' + legacy.name)
            archive.write(destination / '通知.txt', '中文目录/通知.txt')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-doc', action='store_true', help='通过已安装的 Word/WPS 额外生成旧 DOC；需本机 Office COM 可用')
    args = parser.parse_args()
    path = generate(ROOT / 'test-materials', args.with_doc)
    print(f'测试材料已生成：{path}')
