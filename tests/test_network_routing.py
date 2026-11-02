import httpx
from types import SimpleNamespace
from app.config import Settings
from app.feishu import FeishuAPI


def test_feishu_http_ignores_dead_environment_proxy(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    api=FeishuAPI(Settings())
    try:assert api.client._trust_env is False
    finally:api.client.close()


def test_feishu_discovery_bypasses_proxy_only_in_receiver(monkeypatch):
    from app.network import configure_feishu_process
    from requests.utils import get_environ_proxies
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY','');monkeypatch.setenv('no_proxy','')
    configure_feishu_process()
    assert get_environ_proxies('https://open.feishu.cn/callback/ws/endpoint')=={}


def test_model_and_vision_proxy_are_separate():
    from app.network import model_http_options
    cfg=Settings(model_proxy_url='http://127.0.0.1:1111',vision_proxy_url='http://127.0.0.1:2222')
    assert model_http_options(cfg)=={'trust_env':False,'proxy':'http://127.0.0.1:1111'}
    assert model_http_options(cfg,True)=={'trust_env':False,'proxy':'http://127.0.0.1:2222'}
    assert model_http_options(Settings(model_proxy_url='',vision_proxy_url=''))=={'trust_env':False,'proxy':None}


def test_chat_clients_ignore_dead_proxy_and_are_closed(monkeypatch):
    from app.network import invoke_chat
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1')
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    clients=[]
    class Chat:
        def __init__(self,**options):
            clients.extend([options['http_client'],options['http_async_client']])
            assert all(c._trust_env is False for c in clients)
            assert all(not c._mounts for c in clients)
        def invoke(self,messages):return SimpleNamespace(content='test')
    monkeypatch.setattr('langchain_openai.ChatOpenAI',Chat)
    cfg=Settings(model_api_key='test',model_name='test',model_base_url='https://model.invalid',model_proxy_url='')
    assert invoke_chat(cfg,[]).content=='test'
    assert all(c.is_closed for c in clients)
