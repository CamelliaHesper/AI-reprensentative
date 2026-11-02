import json
from app.feishu import normalize_message


def event(content, msg_type='text', mentions=None, parent_id=None):
    return {'event': {'sender': {'sender_type': 'user', 'sender_id': {'open_id':'user-1'}}, 'message': {
        'message_id':'m1', 'chat_id':'c1', 'chat_type':'group', 'message_type':msg_type,
        'content':json.dumps(content), 'mentions':mentions or [], 'parent_id':parent_id, 'create_time':'1234'}}}


def test_only_group_mentions_trigger_and_text_is_extracted():
    assert normalize_message(event({'text':'普通群消息'})) is None
    data = normalize_message(event({'text':'@_user_1 请填写附件'}, mentions=[{'key':'@_user_1','id':{'open_id':'bot'}}]), 'bot')
    assert data['text'] == '请填写附件'
    assert normalize_message(event({'text':'hi'}, mentions=[{'key':'x','id':{'open_id':'other'}}]), 'bot') is None


def test_reply_attachment_and_file_reference_are_preserved():
    data = normalize_message(event({'file_key':'f1','file_name':'报名.xlsx'}, 'file',
        [{'key':'x','id':{'open_id':'bot'}}], 'parent'), 'bot')
    assert data['parent_id'] == 'parent'
    assert data['resources'][0] == {'message_id':'m1','file_key':'f1','name':'报名.xlsx'}


def test_real_mention_plus_pasted_bot_name_does_not_break_upload_command():
    p=event({'text':'@_user_1 @我的计划agent 开始上传'},mentions=[{'key':'@_user_1','name':'我的计划agent','id':{'open_id':'bot'}}],parent_id='prompt')
    assert normalize_message(p,'bot')['text']=='开始上传'
