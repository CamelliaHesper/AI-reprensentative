"""按 MS-DOC 的 CLX/PlcPcd 读取 Word 97–2003 正文，不启动 Office 或执行宏。

规范：https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/01d5d8c4-cf9c-4ef9-80fd-439e763cfe01
不恢复排版；表格单元格边界以分隔符展示。
"""
import re
import struct
import olefile


def _number(data,offset,fmt='<I'):
    if offset<0 or offset+struct.calcsize(fmt)>len(data):
        raise ValueError('DOC 结构不完整')
    return struct.unpack_from(fmt,data,offset)[0]


def extract_stream_text(word,table,max_chars):
    if _number(word,0,'<H')!=0xA5EC or _number(word,2,'<H')<0xC1:
        raise ValueError('暂不支持此 DOC 版本')
    if _number(word,10,'<H') & 0x8100:
        raise ValueError('DOC 已加密或受密码保护，请解除后上传')
    csw=_number(word,32,'<H')
    long_count_at=34+csw*2
    cslw=_number(word,long_count_at,'<H')
    if cslw<4: raise ValueError('DOC 缺少正文长度')
    main_chars=_number(word,long_count_at+2+3*4)
    pairs_at=long_count_at+2+cslw*4
    if _number(word,pairs_at,'<H')<=33:
        raise ValueError('DOC 缺少文字片段索引')
    clx_at=_number(word,pairs_at+2+33*8)
    clx_size=_number(word,pairs_at+2+33*8+4)
    if clx_size>len(table)-clx_at: raise ValueError('DOC 文字索引越界')
    clx=table[clx_at:clx_at+clx_size]
    pos=0
    while pos<len(clx) and clx[pos]==1:
        pos+=3+_number(clx,pos+1,'<H')
    if pos>=len(clx) or clx[pos]!=2: raise ValueError('DOC 缺少文字片段表')
    size=_number(clx,pos+1)
    plc=clx[pos+5:pos+5+size]
    if len(plc)!=size or size<4 or (size-4)%12:
        raise ValueError('DOC 片段表损坏')
    count=(size-4)//12
    if count>100000: raise ValueError('DOC 文字片段数量超限')
    cps=[_number(plc,i*4) for i in range(count+1)]
    if not cps or cps[0]!=0 or any(a>b for a,b in zip(cps,cps[1:])) or cps[-1]<main_chars:
        raise ValueError('DOC 文字位置索引不完整')
    pieces=[]
    limit=min(main_chars,max_chars+1)
    for i in range(count):
        if cps[i]>=limit: break
        units=min(cps[i+1],limit)-cps[i]
        fc=_number(plc,(count+1)*4+i*8+2)
        compressed=bool(fc & 0x40000000)
        offset=fc & 0x3fffffff
        if compressed: offset//=2
        byte_count=units if compressed else units*2
        if offset+byte_count>len(word): raise ValueError('DOC 文字片段越界')
        data=word[offset:offset+byte_count]
        pieces.append(data.decode('cp1252' if compressed else 'utf-16le',errors='replace'))
    return ''.join(pieces)


def read_doc(path,max_chars):
    with olefile.OleFileIO(str(path)) as ole:
        if not ole.exists('WordDocument'): raise ValueError('不是有效的 Word DOC 文件')
        word=ole.openstream('WordDocument').read()
        flags=_number(word,10,'<H')
        table_name='1Table' if flags & 0x200 else '0Table'
        if not ole.exists(table_name): raise ValueError('DOC 缺少文字索引流')
        raw_limit=max_chars*4+4096
        text=extract_stream_text(word,ole.openstream(table_name).read(),raw_limit)
        raw_truncated=len(text)>raw_limit
    # 域指令不展示，只保留域的显示结果；不执行域、链接或宏。
    result=[]; fields=[]
    for char in text:
        if char=='\x13': fields.append(False)
        elif char=='\x14' and fields: fields[-1]=True
        elif char=='\x15' and fields: fields.pop()
        elif all(fields): result.append(char)
    text=''.join(result).replace('\r','\n').replace('\x07',' | ').replace('\x0b','\n').replace('\x0c','\n')
    return re.sub(r'[\x00-\x08\x0e-\x1f]','',text),raw_truncated
