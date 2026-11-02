import csv
import io
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from .config import Settings

SUPPORTED = {'.docx', '.doc', '.xlsx', '.xls', '.csv', '.zip', '.txt', '.pdf'}


def _archive_check(z, cfg):
    entries = z.infolist()
    if len(entries) > cfg.max_zip_entries or sum(x.file_size for x in entries) > cfg.max_zip_mb * 1024**2:
        raise ValueError('压缩内容超过条目数或解压体积限制')
    for x in entries:
        name = x.filename.replace('\\', '/')
        if (PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                or ':' in name or stat.S_ISLNK(x.external_attr >> 16)):
            raise ValueError('压缩包包含不安全的路径或链接')
        if x.flag_bits & 1:
            raise ValueError('不支持加密压缩包')


def parse_file(path, name, cfg=None):
    cfg = cfg or Settings()
    warnings = []
    try:
        path = Path(path)
        if path.stat().st_size > cfg.max_file_mb * 1024**2:
            raise ValueError('文件超过大小限制')
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED:
            raise ValueError('暂不支持此文件类型')
        if suffix in {'.zip', '.xlsx', '.docx'}:
            with zipfile.ZipFile(path) as z:
                _archive_check(z, cfg)
        lines = []
        if suffix=='.pdf':
            from .pdf_reader import read_pdf
            pdf_text,pdf_warnings=read_pdf(path,cfg)
            lines=[pdf_text];warnings.extend(pdf_warnings)
        elif suffix in {'.csv', '.txt'}:
            raw = path.read_bytes()
            try:
                text = raw.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = raw.decode('gb18030')
            if suffix == '.csv':
                try:
                    dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
                except csv.Error:
                    dialect = csv.excel
                for i, row in enumerate(csv.reader(io.StringIO(text), dialect), 1):
                    lines.append(f'第{i}行：'+' | '.join(row))
                    if sum(map(len, lines[-1:])) > cfg.max_text_chars or i > 50000:
                        warnings.append('内容较多，已截断后续行'); break
            else:
                lines = [text]
        elif suffix == '.docx':
            from docx import Document
            from docx.oxml.ns import qn
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            d = Document(path)
            for element in d.element.body:
                if element.tag == qn('w:p'):
                    lines.append(Paragraph(element, d).text)
                elif element.tag == qn('w:tbl'):
                    for row in Table(element, d).rows:
                        lines.append(' | '.join(cell.text for cell in row.cells))
        elif suffix == '.xlsx':
            from openpyxl import load_workbook
            w = load_workbook(io.BytesIO(path.read_bytes()), read_only=True, data_only=False, keep_links=False)
            try:
                count = 0
                for sheet in w:
                    lines.append(f'工作表：{sheet.title}')
                    for row in sheet.iter_rows():
                        for cell in row:
                            count += 1
                            if cell.value is not None:
                                lines.append(f'{cell.coordinate}：{cell.value}')
                            if count >= 200000:
                                warnings.append('单元格数量超过限制，后续内容未提取'); break
                        if count >= 200000: break
                    if count >= 200000: break
                warnings.append('公式按原公式展示，未重新计算；未使用缓存值')
            finally:
                w.close()
        elif suffix == '.xls':
            import xlrd
            w = xlrd.open_workbook(path, on_demand=True)
            try:
                count = 0
                for sheet in w.sheets():
                    lines.append('工作表：'+sheet.name)
                    for i in range(sheet.nrows):
                        lines.append(f'第{i+1}行：'+' | '.join(str(x) for x in sheet.row_values(i)))
                        count += sheet.ncols
                        if count >= 200000: break
                    if count >= 200000:
                        warnings.append('单元格数量超过限制，后续内容未提取'); break
            finally:
                w.release_resources()
        elif suffix == '.doc':
            from .legacy_doc import read_doc
            try:
                text,raw_truncated=read_doc(path,cfg.max_text_chars)
                if not text.strip() and not raw_truncated: raise ValueError('DOC 未提取到正文文字')
                truncated=raw_truncated or len(text)>cfg.max_text_chars
                return {'status':'ok','text':text[:cfg.max_text_chars],
                    'warnings':['旧版 DOC 按正文文字顺序提取，表格以分隔符展示；不保留版式、图片及页眉页脚。']+
                        (['提取文本达到字符限制，后续内容已截断'] if truncated else []),
                    'error':'','truncated':truncated}
            except Exception as native_error:
                native_reason=str(native_error) if isinstance(native_error,ValueError) else 'DOC 二进制结构不支持或已损坏'
            exe = cfg.libreoffice_path or shutil.which('soffice')
            if not exe:
                default = Path(r'C:\Program Files\LibreOffice\program\soffice.exe')
                exe = str(default) if default.is_file() else None
            if not exe:
                raise ValueError(native_reason+'；可安装 LibreOffice 转换，或另存为 DOCX 上传')
            with tempfile.TemporaryDirectory(dir=path.parent) as td:
                root = Path(td); source = root / 'source.doc'; shutil.copyfile(path, source)
                subprocess.run([exe, '-env:UserInstallation='+(root/'profile').as_uri(), '--headless',
                    '--convert-to', 'docx', '--outdir', td, str(source)], timeout=45, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return parse_file(root/'source.docx', 'converted.docx', cfg)
        elif suffix == '.zip':
            with zipfile.ZipFile(path) as z, tempfile.TemporaryDirectory(dir=path.parent) as td:
                for i, entry in enumerate(z.infolist()):
                    if entry.is_dir(): continue
                    ext = Path(entry.filename).suffix.lower()
                    if ext not in SUPPORTED or ext == '.zip':
                        warnings.append(f'跳过 {entry.filename}：不支持的类型或嵌套压缩包'); continue
                    if entry.file_size > cfg.max_file_mb*1024**2:
                        warnings.append(f'跳过 {entry.filename}：文件过大'); continue
                    target = Path(td)/str(i)
                    target.write_bytes(z.read(entry))
                    result = parse_file(target, entry.filename, cfg)
                    lines.append(f'附件：{entry.filename}\n{result["text"]}')
                    warnings.extend(f'{entry.filename}：{w}' for w in result['warnings'])
                    if result['status'] == 'error': warnings.append(f'{entry.filename}：{result["error"]}')
        text = '\n'.join(lines)
        truncated = len(text) > cfg.max_text_chars
        if truncated: warnings.append('提取文本达到字符限制，后续内容已截断')
        if not text.strip(): warnings.append('未提取到文本；图片扫描件不支持 OCR，请人工检查')
        return {'status': 'partial' if any('未配置视觉模型' in w for w in warnings) else 'ok',
            'text': text[:cfg.max_text_chars], 'warnings': warnings, 'error': '', 'truncated': truncated}
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else f'文件损坏、格式不符或转换失败（{type(exc).__name__}）'
        return {'status': 'error', 'text': '', 'warnings': warnings, 'error': message, 'truncated': False}
