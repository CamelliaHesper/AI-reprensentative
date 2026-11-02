from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings


def account(client,name):
    result=client.post('/api/auth/register',json={'username':name,'password':'Strong-test-password-123'})
    assert result.status_code==200,result.text
    return result.json()


def test_login_and_tasks_are_isolated(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as a,TestClient(app) as b:
        assert a.get('/api/tasks').status_code==401
        account(a,'alice');account(b,'bob')
        tid=a.post('/api/tasks',json={'text':'Alice private task'}).json()['id']
        assert b.get('/api/tasks').json()==[]
        for method,path,body in [('GET','',None),('DELETE','',None),('POST','/restore',{}),('POST','/retry',{}),
            ('GET','/documents',None),('GET','/fulfillment',None),('GET','/outputs/docx',None),
            ('POST','/editor/start',{}),('POST','/form-assistant',{}),('POST','/send-feishu',{}),('POST','/complete',{})]:
            r=b.request(method,'/api/tasks/'+tid+path,json=body)
            assert r.status_code==404,(path,r.text)
        a.put('/api/profile',json={'姓名':'Alice'})
        assert b.get('/api/profile').json()=={}
        assert a.get('/api/profile').json()=={'姓名':'Alice'}
        a.post('/api/auth/logout')
        assert a.get('/api/tasks/'+tid).status_code==401


def test_feishu_binding_is_one_time_and_cannot_claim_others(tmp_path):
    import time
    from app.auth import digest
    app=create_app(Settings(data_dir=tmp_path),run_worker=False);store=app.state.store
    tid,_=store.create_task('original','飞书旧任务','feishu',{'sender_id':'ou_alice'})
    with TestClient(app) as a,TestClient(app) as b:
        alice=account(a,'alice');account(b,'bob')
        assert a.get('/api/tasks/'+tid).status_code==404
        code=a.post('/api/auth/binding-code').json()['code']
        assert '成功' in app.state.auth.consume_binding(code,'ou_alice')
        assert a.get('/api/tasks/'+tid).status_code==200
        assert b.get('/api/tasks/'+tid).status_code==404
        assert '无效' in app.state.auth.consume_binding(code,'ou_alice')
        other=b.post('/api/auth/binding-code').json()['code']
        assert '失败' in app.state.auth.consume_binding(other,'ou_alice')
        assert not b.get('/api/auth/me').json()['feishu_bound']
        expired=a.post('/api/auth/binding-code').json()['code']
        with store.connect() as c:c.execute('UPDATE binding_codes SET expires=? WHERE code=?',(time.time()-1,digest(expired)))
        assert '过期' in app.state.auth.consume_binding(expired,'ou_else')
        assert a.get('/api/auth/me').json()['feishu_bound']


def test_old_local_data_and_global_profile_not_claimed(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False);store=app.state.store
    tid,_=store.create_task('old','历史本地任务','local',{})
    store.set_value('profile',{'姓名':'旧个人资料'})
    with TestClient(app) as a:
        account(a,'new_account')
        assert a.get('/api/tasks/'+tid).status_code==404
        assert a.get('/api/tasks').json()==[]
        assert a.get('/api/profile').json()=={}


def test_completion_status_and_revision_invalidation(tmp_path):
    from app.fulfillment import save_state,analysis_key,update_values,get_state
    app=create_app(Settings(data_dir=tmp_path),run_worker=False);store=app.state.store
    with TestClient(app) as a:
        account(a,'alice')
        tid=a.post('/api/tasks',json={'text':'任务'}).json()['id']
        store.update(tid,status='partial')
        state={'revision':'r','analysis_key':analysis_key(store,tid),'stage':'generated','template_name':'申请.docx',
            'outputs':['docx'],'fields':[{'target':'c1','label':'姓名','required':True,'value':'姓名'}]}
        save_state(store,tid,state)
        assert a.post('/api/tasks/'+tid+'/complete',json={'confirmed':False}).status_code==400
        assert a.post('/api/tasks/'+tid+'/complete',json={'confirmed':True,'revision':'old'}).status_code==409
        assert a.post('/api/tasks/'+tid+'/complete',json={'confirmed':True,'revision':'r'}).status_code==200
        assert a.get('/api/tasks/'+tid).json()['display_status']=='confirmed'
        assert a.get('/api/tasks').json()[0]['display_status']=='confirmed'
        update_values(store,tid,'r',{'c1':'修改后'})
        assert a.get('/api/tasks/'+tid).json()['display_status']=='awaiting_confirmation'
        state=get_state(store,tid);state.update(stage='generated',sent_message_id='sent');save_state(store,tid,state)
        assert a.get('/api/tasks').json()[0]['display_status']=='confirmed'
        assert a.post('/api/tasks/'+tid+'/retry').status_code==200
        store.update(tid,status='partial')
        assert a.get('/api/tasks/'+tid).json()['display_status']!='confirmed'


def test_owner_profile_is_used_by_bound_feishu_task(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False);store=app.state.store
    with TestClient(app) as a,TestClient(app) as b:
        account(a,'alice');account(b,'bob')
        a.put('/api/profile',json={'姓名':'Alice'})
        b.put('/api/profile',json={'姓名':'Bob'})
        code=a.post('/api/auth/binding-code').json()['code'];app.state.auth.consume_binding(code,'ou_alice')
        tid,_=store.create_task('m','任务','feishu',{'sender_id':'ou_alice'})
        assert store.profile_for_task(store.get(tid))=={'姓名':'Alice'}


def test_binding_message_uses_real_sender_not_supplied_identity(tmp_path):
    from app.auth import binding_reply
    from app.feishu import normalize_message
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as a:
        account(a,'alice')
        code=a.post('/api/auth/binding-code').json()['code']
        payload={'event':{'sender':{'sender_type':'user','sender_id':{'open_id':'ou_real_sender'}},'message':{
            'message_id':'om_bind','chat_id':'oc_group','chat_type':'group','message_type':'text',
            'mentions':[{'key':'@_user_1','id':{'open_id':'ou_bot'}}],
            'content':__import__('json').dumps({'text':'@_user_1 绑定 '+code})}}}
        normalized=normalize_message(payload,'ou_bot',allow_replies=True)
        assert '成功' in binding_reply(app.state.store,normalized)
        with app.state.store.connect() as c:
            assert c.execute('SELECT sender_id FROM feishu_bindings').fetchone()['sender_id']=='ou_real_sender'


def test_stolen_task_link_never_serves_private_file(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as a,TestClient(app) as b:
        account(a,'alice');account(b,'bob')
        tid=a.post('/api/tasks',json={'text':'private'}).json()['id']
        uploaded=a.post('/api/tasks/'+tid+'/files',files={'file':('secret.csv',b'private,value')}).json()['id']
        url=f'/api/tasks/{tid}/files/{uploaded}'
        assert a.get(url).status_code==200
        assert b.get(url).status_code==404
        assert b.get(f'/api/tasks/{tid}/export?format=json').status_code==404
        assert b.get(f'/api/tasks/{tid}/documents/fake/preview').status_code==404
        assert b.get(f'/api/tasks/{tid}/outputs/pdf').status_code==404
        a.delete('/api/tasks/'+tid)
        assert b.get('/api/tasks?deleted=true').json()==[]
        assert b.post('/api/tasks/'+tid+'/restore').status_code==404


def test_logout_revokes_session_and_cookie_is_httponly(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as a:
        response=a.post('/api/auth/register',json={'username':'alice','password':'Strong-test-password-123'})
        cookie=response.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
        old=a.cookies.get('shiban_session')
        a.post('/api/auth/logout')
        assert a.get('/api/auth/me',headers={'Cookie':'shiban_session='+old}).status_code==401
        assert a.post('/api/auth/login',json={'username':'alice','password':'wrong-password'}).status_code==401
