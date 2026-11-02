import pytest
from app.auth import Auth,Credentials,binding_reply
from app.store import Store


@pytest.mark.parametrize('phrase',[
    '绑定{code}','绑定 {code}','帮我绑定账号，验证码是：{code}',
    '麻烦关联飞书账号，验证码{code}。','{code}','“{code}”',
])
def test_flexible_binding_with_valid_code(tmp_path,phrase):
    store=Store(tmp_path);auth=Auth(store)
    account,_=auth.credentials(Credentials(username='sample',password='sample-password'),True)
    code=auth.issue_binding(account['id'])
    response=binding_reply(store,{'text':phrase.format(code=code),'is_mentioned':True,'sender_id':'ou_real'})
    assert '成功' in response
    with store.connect() as c:assert c.execute('SELECT sender_id FROM feishu_bindings').fetchone()['sender_id']=='ou_real'


def test_negative_intent_and_multiple_codes_do_not_bind(tmp_path):
    store=Store(tmp_path);auth=Auth(store)
    account,_=auth.credentials(Credentials(username='sample',password='sample-password'),True)
    code=auth.issue_binding(account['id'])
    msg={'is_mentioned':True,'sender_id':'ou_real'}
    assert '未执行' in binding_reply(store,{**msg,'text':'不要绑定 '+code})
    assert '多个' in binding_reply(store,{**msg,'text':'帮我绑定 '+code+' 0000000000000000'})
    assert '成功' in binding_reply(store,{**msg,'text':'绑定'+code})


def test_claiming_success_without_code_cannot_bind(tmp_path):
    store=Store(tmp_path);Auth(store)
    result=binding_reply(store,{'text':'绑定成功了','is_mentioned':True,'sender_id':'ou_user'})
    assert '不会改变' in result
    with store.connect() as c:assert c.execute('SELECT count(*) FROM feishu_bindings').fetchone()[0]==0


@pytest.mark.parametrize('text',['绑定0123456789ABCDEF','绑定 0123456789ABCDEF','帮我绑定账号，验证码是0123456789ABCDEF','0123456789ABCDEF','绑定成功了'])
def test_binding_messages_never_create_tasks(tmp_path,text):
    from app.conversation import accept_message
    store=Store(tmp_path)
    message={'message_id':'bind-message','sender_id':'ou_sender','chat_id':'oc_group','chat_type':'group','is_mentioned':True,'text':text}
    assert accept_message(store,message) is None
    assert store.listing()==[]
