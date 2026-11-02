"""Legacy functional tests run with a real seeded login, never an auth bypass."""
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def legacy_test_login(request,monkeypatch):
    if request.path.name=='test_auth_isolation.py':return
    original=TestClient.__enter__
    def enter(client):
        result=original(client)
        auth=getattr(getattr(client.app,'state',None),'auth',None)
        if auth:
            response=client.post('/api/auth/register',json={'username':'legacy_test','password':'legacy-password-for-tests'})
            if response.status_code==409:response=client.post('/api/auth/login',json={'username':'legacy_test','password':'legacy-password-for-tests'})
            assert response.status_code==200,response.text
            uid=response.json()['id']
            # Existing fixtures create synthetic tasks directly in Store before opening a client.
            with auth.store.connect() as c:
                rows=list(c.execute('SELECT id,source,metadata FROM tasks'))
                for row in rows:
                    meta=json.loads(row['metadata'])
                    if row['source']=='feishu':
                        if not meta.get('sender_id'):
                            meta['sender_id']='ou_legacy_fixture'
                            c.execute('UPDATE tasks SET metadata=? WHERE id=?',(json.dumps(meta),row['id']))
                        c.execute('INSERT OR IGNORE INTO feishu_bindings VALUES (?,?)',(meta['sender_id'],uid))
                    else:
                        meta['owner_id']=uid;c.execute('UPDATE tasks SET metadata=? WHERE id=?',(json.dumps(meta),row['id']))
        return result
    monkeypatch.setattr(TestClient,'__enter__',enter)
