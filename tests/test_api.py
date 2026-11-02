from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.workflow import process_task


def test_offline_flow_upload_confirm_and_export(tmp_path):
    settings = Settings(data_dir=tmp_path, model_api_key='', model_name='', model_base_url='')
    app = create_app(settings, run_worker=False)
    with TestClient(app) as c:
        r = c.post('/api/tasks', json={'text': '请填写姓名并提交报名材料'})
        assert r.status_code == 201
        tid = r.json()['id']
        assert c.post(f'/api/tasks/{tid}/files', files={'file': ('报名.csv', '姓名,张三'.encode('utf8'))}).status_code == 200
        process_task(app.state.store, tid, settings)
        t = c.get(f'/api/tasks/{tid}').json()
        assert t['status'] == 'awaiting_model' and '张三' in t['files'][0]['result']['text']
        assert t['analysis']['mode'] == 'manual'
        assert c.put('/api/profile', json={'姓名': '测试用户'}).status_code == 200
        assert c.get('/api/profile').json()['姓名'] == '测试用户'
        assert c.put(f'/api/tasks/{tid}/analysis', json={'summary': '报名', 'confirmed': True}).status_code == 200
        assert c.get(f'/api/tasks/{tid}').json()['status'] == 'confirmed'
        export = c.get(f'/api/tasks/{tid}/export?format=md')
        assert export.status_code == 200 and '报名' in export.text
        fid = t['files'][0]['id']
        assert c.get(f'/api/tasks/{tid}/files/{fid}').status_code == 200
        assert 'path' not in t['files'][0]
        assert c.get('/api/tasks/unknown').status_code == 404
        assert c.get('/api/status').json()['model_ready'] is False


def test_upload_limits_and_cross_origin_rejection(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, max_file_mb=1), run_worker=False)
    with TestClient(app) as c:
        assert c.get('/', headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate'}).status_code == 200
        assert c.post('/api/tasks', json={'text': 'x'}, headers={'Origin': 'https://evil.example'}).status_code == 403
        tid = c.post('/api/tasks', json={'text':'test'}).json()['id']
        assert c.post(f'/api/tasks/{tid}/files', files={'file': ('a.csv', b'a'* (1024**2+1))}).status_code == 413
        assert c.post(f'/api/tasks/{tid}/files', files={'file': ('a.exe', b'a')}).status_code == 400


def test_queued_task_cannot_be_edited_or_removed_from_queue(tmp_path):
    app = create_app(Settings(data_dir=tmp_path), run_worker=False)
    with TestClient(app) as c:
        tid = c.post('/api/tasks', json={'text':'填写报名表'}).json()['id']
        assert c.post(f'/api/tasks/{tid}/retry').status_code == 200
        assert c.put(f'/api/tasks/{tid}/analysis', json={'summary':'中途编辑'}).status_code == 409
        assert c.post(f'/api/tasks/{tid}/files', files={'file':('a.csv',b'a,b')}).status_code == 409
        assert c.get(f'/api/tasks/{tid}').json()['status'] == 'queued'


def test_delete_task_hides_resources_and_can_be_restored(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as c:
        tid=c.post('/api/tasks',json={'text':'删除测试'}).json()['id']
        fid=c.post(f'/api/tasks/{tid}/files',files={'file':('a.csv',b'a,b')}).json()['id']
        assert c.delete(f'/api/tasks/{tid}').status_code==200
        assert c.get('/api/tasks').json()==[]
        assert c.get(f'/api/tasks/{tid}').status_code==404
        assert c.get(f'/api/tasks/{tid}/files/{fid}').status_code==404
        assert c.get('/api/tasks?deleted=true').json()[0]['id']==tid
        assert c.post(f'/api/tasks/{tid}/restore').status_code==200
        assert c.get(f'/api/tasks/{tid}').json()['status']=='draft'
        assert c.get(f'/api/tasks/{tid}/files/{fid}').status_code==200


def test_delete_does_not_interrupt_busy_task(tmp_path):
    app=create_app(Settings(data_dir=tmp_path),run_worker=False)
    with TestClient(app) as c:
        tid=c.post('/api/tasks',json={'text':'处理中'}).json()['id']
        for state in ['queued','processing','receiving','collecting_files']:
            app.state.store.update(tid,status=state)
            assert c.delete(f'/api/tasks/{tid}').status_code==409
            assert app.state.store.get(tid)['status']==state
