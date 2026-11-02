"""飞书长连接入口：python -m app.feishu。密钥仅从本地 .env 读取。"""
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import httpx
from .config import Settings
from .store import Store

API = 'https://open.feishu.cn/open-apis'


def normalize_message(payload, bot_open_id='', allow_replies=False, bot_name=''):
    event = payload.get('event', payload)
    message = event.get('message', {})
    sender = event.get('sender', {})
    if sender.get('sender_type') != 'user': return None
    mentions = message.get('mentions') or []
    mentioned = bool(mentions) if not bot_open_id else any((x.get('id') or {}).get('open_id') == bot_open_id for x in mentions)
    if message.get('chat_type') == 'group':
        linked_reply = allow_replies and (message.get('parent_id') or message.get('root_id') or message.get('message_type')=='file')
        if not mentioned and not linked_reply: return None
    elif message.get('chat_type') != 'p2p': return None
    content = message.get('content', '{}')
    if isinstance(content, str):
        try: content = json.loads(content)
        except json.JSONDecodeError: content = {'text': content}
    texts, resources = [], []

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('text'), str): texts.append(value['text'])
            if isinstance(value.get('title'), str): texts.append(value['title'])
            if value.get('file_key'):
                resources.append({'message_id':message['message_id'], 'file_key':value['file_key'],
                    'name':value.get('file_name') or value.get('name') or '未命名附件'})
            for k,v in value.items():
                if isinstance(v,(list,dict)): walk(v)
        elif isinstance(value,list):
            for v in value: walk(v)
    walk(content)
    text = '\n'.join(texts)
    for m in mentions:
        if not bot_open_id or (m.get('id') or {}).get('open_id') == bot_open_id:
            text = text.replace(m.get('key','\0'), '')
    # 飞书的真实提及占位符与用户粘贴的可见名字可能同时存在。
    # 仅在整段是操作指令时清理前缀，不改写通知正文中的名字。
    names={bot_name} if bot_name else set()
    names.update(m.get('name','') for m in mentions if not bot_open_id or (m.get('id') or {}).get('open_id')==bot_open_id)
    command_text=text.strip()
    for name in names:
        if name:
            while command_text.startswith('@'+name):
                command_text=command_text[len(name)+1:].lstrip()
    if command_text.rstrip('。！! \n') in {'开始上传','开始解析'}:
        text=command_text
    return {'message_id':message.get('message_id',''), 'chat_id':message.get('chat_id',''),
        'text':text.strip(), 'resources':resources, 'parent_id':message.get('parent_id'),
        'sender_id':sender.get('sender_id',{}).get('open_id',''), 'create_time':message.get('create_time',''),
        'root_id':message.get('root_id'), 'chat_type':message.get('chat_type'), 'is_mentioned':mentioned}


class FeishuAPI:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = httpx.Client(base_url=API+'/', timeout=30, follow_redirects=False,trust_env=False)
        self.token = ''

    def __enter__(self):
        try:
            r = self.client.post('auth/v3/tenant_access_token/internal', json={
                'app_id':self.cfg.feishu_app_id, 'app_secret':self.cfg.feishu_app_secret})
            r.raise_for_status(); data = r.json()
            if data.get('code',0) != 0: raise ValueError(f'飞书凭证验证失败，错误码 {data.get("code")}')
            self.token = data['tenant_access_token']
            self.client.headers['Authorization'] = 'Bearer '+self.token
            return self
        except Exception:
            self.client.close(); raise

    def __exit__(self, *args): self.client.close()

    def message(self, mid):
        r = self.client.get('im/v1/messages/'+mid); r.raise_for_status()
        data = r.json()
        if data.get('code',0): raise ValueError('获取回复的消息失败，请检查消息读取权限，错误码 '+str(data['code']))
        return data.get('data',{}).get('items',[])

    def user_names(self,ids):
        r=self.client.post('contact/v3/users/basic_batch',params={'user_id_type':'open_id'},json={'user_ids':ids})
        r.raise_for_status();data=r.json()
        if data.get('code',0):
            # 部分应用已有“获取用户基本信息”，但未开通基本资料批量接口的独立权限。
            names={}
            for uid in ids:
                if not re.fullmatch(r'[\w-]+',uid):continue
                result=self.client.get('contact/v3/users/'+uid,params={'user_id_type':'open_id'})
                body=result.json()
                if result.is_success and not body.get('code',0):
                    name=body.get('data',{}).get('user',{}).get('name')
                    if name:names[uid]=name
            if not names:raise ValueError('无法读取发起人姓名，请检查获取用户基本信息权限与应用数据范围')
            return names
        return {x.get('open_id') or x.get('user_id'):x['name'] for x in data.get('data',{}).get('users',[]) if x.get('name')}

    def download(self, item):
        if not re.fullmatch(r'[\w-]+',item['message_id']) or not re.fullmatch(r'[\w-]+',item['file_key']):
            raise ValueError('附件标识不合法')
        with self.client.stream('GET',f'im/v1/messages/{item["message_id"]}/resources/{item["file_key"]}', params={'type':'file'}) as r:
            r.raise_for_status()
            if 'application/json' in r.headers.get('content-type',''): raise ValueError('附件下载失败，请检查资源权限或附件是否属于当前消息')
            limit = self.cfg.max_file_mb*1024**2
            content = bytearray()
            for chunk in r.iter_bytes(65536):
                content.extend(chunk)
                if len(content)>limit: raise ValueError('附件超过大小限制')
            return bytes(content)

    def reply(self, mid, text):
        r = self.client.post(f'im/v1/messages/{mid}/reply', json={'msg_type':'text', 'content':json.dumps({'text':text},ensure_ascii=False)})
        r.raise_for_status()
        data=r.json()
        if data.get('code',0): raise ValueError('状态回复失败')
        mid=data.get('data',{}).get('message_id')
        if not mid: raise ValueError('回复未返回消息标识')
        return mid

    def send_file(self,chat_id,path,name,request_id):
        with open(path,'rb') as stream:
            response=self.client.post('im/v1/files',data={'file_type':'stream','file_name':name},files={'file':(name,stream,'application/octet-stream')})
        response.raise_for_status();data=response.json()
        if data.get('code',0):raise ValueError('飞书文件上传失败，错误码 '+str(data['code']))
        key=data.get('data',{}).get('file_key')
        if not key:raise ValueError('飞书未返回文件标识')
        response=self.client.post('im/v1/messages',params={'receive_id_type':'chat_id'},json={
            'receive_id':chat_id,'msg_type':'file','content':json.dumps({'file_key':key}),'uuid':request_id})
        response.raise_for_status();data=response.json()
        if data.get('code',0):raise ValueError('飞书发送失败，错误码 '+str(data['code']))
        return data.get('data',{}).get('message_id','')


def refresh_initiator_names(store,cfg,ids=None):
    if not cfg.feishu_app_id or not cfg.feishu_app_secret:return {'updated':0}
    if ids is None:
        with store.connect() as c:
            metas=[json.loads(r['metadata']) for r in c.execute("SELECT metadata FROM tasks WHERE source='feishu' AND status!='deleted'")]
        ids=list({m['sender_id'] for m in metas if m.get('sender_id')})
    ids=list(dict.fromkeys(ids))
    count=0
    try:
        with FeishuAPI(cfg) as api:
            for start in range(0,len(ids),50):
                for uid,name in api.user_names(ids[start:start+50]).items():
                    store.set_value('feishu_user_name:'+uid,name);count+=1
        store.set_value('initiator_name_error','')
        return {'updated':count}
    except Exception:
        message='暂未取得姓名，请检查应用身份权限 contact:user.basic_profile:readonly（获取用户的基本信息）及可访问的数据范围。'
        store.set_value('initiator_name_error',message)
        return {'updated':count,'warning':message}


def fetch_attachments(store, task, cfg):
    meta = task['metadata']; errors = []
    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        return ['飞书应用凭证未配置，暂时无法下载附件']
    resources = list(meta.get('resources',[]))
    parent = meta.get('parent_id')
    if meta.get('parent_resolved'): parent=None
    if not resources and not parent: return []
    try:
        with FeishuAPI(cfg) as api:
            if parent:
                try:
                    for m in api.message(parent):
                        content = m.get('body',{}).get('content','{}')
                        normalized = normalize_message({'event': {'sender':{'sender_type':'user'},'message':{
                            'chat_type':'p2p', 'message_id':m['message_id'], 'content':content}}})
                        if normalized:
                            resources.extend(normalized['resources'])
                    # 解析过的父消息资源持久化，后续不重复读取同一父消息。
                    latest=store.get(task['id'])['metadata']
                    latest['resources']=list({(r['message_id'],r['file_key']):r for r in resources}.values())
                    latest['parent_resolved']=True
                    store.update(task['id'],metadata=latest)
                except Exception as exc:
                    errors.append(str(exc) if isinstance(exc,ValueError) else '无法读取回复的消息，请检查权限或在网页补充附件')
            existing = {f['remote_key'] for f in store.get(task['id'])['files']}
            for item in resources:
                key = item['message_id']+':'+item['file_key']
                if key in existing: continue
                try:
                    content = api.download(item)
                    name = item['name'].replace('\\','/').split('/')[-1][:240]
                    store.add_file(task['id'],name,content,key); existing.add(key)
                except Exception as exc:
                    errors.append(f'{item["name"]}：'+(str(exc) if isinstance(exc,ValueError) else f'下载失败（{type(exc).__name__}），可在网页补充上传'))
    except Exception as exc:
        errors.append(str(exc) if isinstance(exc,ValueError) else f'飞书连接失败（{type(exc).__name__}），请检查本地凭证和网络')
    return errors


def main():
    from .network import configure_feishu_process
    configure_feishu_process()
    cfg = Settings(); store = Store(cfg.data_dir)
    if not cfg.feishu_app_id or not cfg.feishu_app_secret:
        print('请先在 .env 中填写 FEISHU_APP_ID 和 FEISHU_APP_SECRET，再启动飞书连接。'); return
    # 文件锁由操作系统在进程退出时释放；防止两个接收进程互相恢复对方的下载状态。
    receiver_lock=(store.root/'feishu-receiver.lock').open('a+b')
    try:
        if __import__('os').name=='nt':
            import msvcrt
            receiver_lock.seek(0)
            if not receiver_lock.read(1):
                receiver_lock.write(b'0'); receiver_lock.flush()
            receiver_lock.seek(0)
            msvcrt.locking(receiver_lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(receiver_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        receiver_lock.close()
        raise SystemExit('本目录已有飞书接收进程，请勿重复启动。')
    store.recover_collections()
    import lark_oapi as lark
    # SDK 使用主线程事件循环；独立于 Web 进程，避免与 FastAPI 的循环冲突。
    with FeishuAPI(cfg) as api:
        response = api.client.get('bot/v3/info'); response.raise_for_status()
        data = response.json()
        bot_id = cfg.feishu_bot_open_id or data.get('bot',{}).get('open_id','')
        bot_name = data.get('bot',{}).get('app_name','')
        if not bot_id:
            raise SystemExit('无法取得机器人身份，请检查机器人能力或配置 FEISHU_BOT_OPEN_ID，避免误处理 @其他人的消息。')

    executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='feishu-events')

    def handle_payload(payload):
        try:
            event = payload.get('event', payload)
            message = event.get('message', {})
            mentions = message.get('mentions') or []
            store.set_value('feishu_last_event', {
                'received_at':time.time(), 'chat_type':message.get('chat_type'),
                'message_type':message.get('message_type'),
                'sender_type':event.get('sender',{}).get('sender_type'),
                'mentions_count':len(mentions),
                'mentions_this_bot':any((m.get('id') or {}).get('open_id')==bot_id for m in mentions),
            })
            normalized = normalize_message(payload, bot_id, allow_replies=True, bot_name=bot_name)
            if not normalized or not normalized['message_id']: return
            from .auth import binding_reply
            text=binding_reply(store,normalized)
            if text is not None:
                refresh_initiator_names(store,cfg,[normalized['sender_id']])
                with FeishuAPI(cfg) as api:api.reply(normalized['message_id'],text)
                return
            from .conversation import accept_message, response_text
            result=accept_message(store,normalized)
            if not result or result['action']=='duplicate': return
            tid=result['task_id']
            if result['action']=='created':
                refresh_initiator_names(store,cfg,[normalized['sender_id']])
            task=store.get(tid) if tid else None
            if task and result['action'] in {'created','attachments'}:
                # 单线程事件队列使下载先于下一条“开始解析”。Web 端也禁止在下载中操作。
                if store.transition(tid,'collecting_files'):
                    try:
                        errors=fetch_attachments(store,store.get(tid),cfg)
                        store.update(tid,error='\n'.join(errors))
                    finally:
                        store.finish_collection(tid)
                task=store.get(tid)
            if cfg.feishu_reply:
                try:
                    text=response_text(result,task,cfg.web_url)
                    if task and task['error'] and result['action'] in {'created','attachments'}:
                        text+='\n部分材料下载失败，请到网页检查提示，必要时重发或补充上传。'
                    with FeishuAPI(cfg) as api:
                        reply_id=api.reply(normalized['message_id'],text)
                    if tid: store.link_message(reply_id,tid)
                except Exception:
                    store.set_value('feishu_reply_error','任务已接收，但回复失败，请检查发消息权限；可回复原始通知或使用网页继续。')
        except Exception as exc:
            store.set_value('feishu_status',{'state':'事件处理失败','error':type(exc).__name__,'updated':time.time()})

    def receive(data):
        executor.submit(handle_payload,json.loads(lark.JSON.marshal(data)))

    handler = lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(receive).build()
    client = lark.ws.Client(cfg.feishu_app_id,cfg.feishu_app_secret,event_handler=handler, log_level=lark.LogLevel.WARNING)
    def heartbeat():
        while True:
            connected = getattr(client, '_conn', None) is not None
            store.set_value('feishu_status', {'state':'已连接' if connected else '正在连接或重连','updated':time.time()})
            time.sleep(10)
    threading.Thread(target=heartbeat,daemon=True).start()
    print('飞书连接进程已启动。请保持此窗口开启；在开放平台保存长连接订阅并添加“接收消息”事件。')
    try: client.start()
    except KeyboardInterrupt: pass
    finally:
        executor.shutdown(wait=True)
        store.set_value('feishu_status',{'state':'已停止','updated':time.time()})


if __name__ == '__main__': main()
