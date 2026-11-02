import struct
import pytest
from app.legacy_doc import extract_stream_text


def streams():
    word=bytearray(1024)
    struct.pack_into('<HH',word,0,0xA5EC,0xC1)
    struct.pack_into('<H',word,10,0x200)
    struct.pack_into('<H',word,32,14)
    struct.pack_into('<H',word,62,22)
    struct.pack_into('<I',word,76,10)
    struct.pack_into('<H',word,152,93)
    word[512:518]='姓名\r'.encode('utf-16le')
    word[600:607]=b'School\r'
    plc=struct.pack('<III',0,3,10)+struct.pack('<HIH',0,512,0)+struct.pack('<HIH',0,0x40000000|1200,0)
    table=b'\x02'+struct.pack('<I',len(plc))+plc
    struct.pack_into('<II',word,418,0,len(table))
    return bytes(word),table


def test_word_binary_piece_table_preserves_unicode_and_ansi_order():
    word,table=streams()
    assert extract_stream_text(word,table,100)=='姓名\rSchool\r'


def test_doc_rejects_encryption_and_invalid_piece_offsets():
    word,table=streams()
    encrypted=bytearray(word);struct.pack_into('<H',encrypted,10,0x300)
    with pytest.raises(ValueError,match='加密'):
        extract_stream_text(bytes(encrypted),table,100)
    corrupt=bytearray(table);struct.pack_into('<I',corrupt,19,999999)
    with pytest.raises(ValueError):
        extract_stream_text(word,bytes(corrupt),100)


def test_field_instructions_do_not_silently_consume_visible_text_budget(monkeypatch):
    import io
    from app.legacy_doc import read_doc
    raw='\x13HYPERLINK "https://example.com"\x14姓名\x15学校\r'
    word=bytearray(512+len(raw)*2)
    base,_=streams();word[:512]=base[:512]
    struct.pack_into('<I',word,76,len(raw));word[512:]=raw.encode('utf-16le')
    plc=struct.pack('<IIHIH',0,len(raw),0,512,0)
    table=b'\x02'+struct.pack('<I',len(plc))+plc
    struct.pack_into('<II',word,418,0,len(table))
    class Ole:
        def __init__(self,*a): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def exists(self,name): return True
        def openstream(self,name): return io.BytesIO(bytes(word) if name=='WordDocument' else table)
    monkeypatch.setattr('app.legacy_doc.olefile.OleFileIO',Ole)
    text,raw_truncated=read_doc('fixture',3)
    assert text.startswith('姓名学校') and not raw_truncated
