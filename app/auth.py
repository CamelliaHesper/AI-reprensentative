"""Local login and proof-of-possession Feishu account binding."""
import contextvars
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from fastapi import HTTPException,Request,Response
from pydantic import BaseModel,Field

principal=contextvars.ContextVar('principal',default=None)
COOKIE='shiban_session'


def user():
    current=principal.get()
    if not current:raise HTTPException(401,'请先登录')
    return current


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


class Credentials(BaseModel):
    username:str=Field(min_length=3,max_length=40)
    password:str=Field(min_length=10,max_length=200)


class Auth:
    def __init__(self,store):
        self.store=store
        with store.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,username TEXT UNIQUE,salt TEXT,password TEXT,created REAL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,account_id TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS feishu_bindings(sender_id TEXT PRIMARY KEY,account_id TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS binding_codes(code TEXT PRIMARY KEY,account_id TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER,until REAL);
            ''')

    def resolve(self,token):
        if not token:return None
        with self.store.connect() as c:
            row=c.execute('SELECT a.id,a.username FROM accounts a JOIN sessions s ON s.account_id=a.id WHERE s.token=? AND s.expires>?',(digest(token),time.time())).fetchone()
        return dict(row) if row else None

    def allows(self,account_id,task):
        meta=task['metadata']
        if task['source']=='feishu':
            with self.store.connect() as c:
                row=c.execute('SELECT account_id FROM feishu_bindings WHERE sender_id=?',(meta.get('sender_id',''),)).fetchone()
            return bool(row and row['account_id']==account_id)
        return meta.get('owner_id')==account_id

    def credentials(self,data,register=False,remote='local'):
        name=data.username.strip().lower()
        if not re.fullmatch(r'[a-z0-9_.-]{3,40}',name):raise HTTPException(400,'账号使用3—40位字母、数字、点、下划线或短横线')
        key=digest(name+'|'+remote)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            attempt=c.execute('SELECT count,until FROM login_attempts WHERE key=?',(key,)).fetchone()
            if attempt and attempt['until']>time.time() and attempt['count']>=8:raise HTTPException(429,'尝试次数过多，请15分钟后重试')
            row=c.execute('SELECT * FROM accounts WHERE username=?',(name,)).fetchone()
            salt=secrets.token_hex(16) if register or row is None else row['salt']
            password=hashlib.pbkdf2_hmac('sha256',data.password.encode(),bytes.fromhex(salt),600000).hex()
            if register:
                if row:raise HTTPException(409,'账号不可用，请选择其他账号')
                account_id=secrets.token_hex(16)
                c.execute('INSERT INTO accounts VALUES (?,?,?,?,?)',(account_id,name,salt,password,time.time()))
            elif row is None or not hmac.compare_digest(password,row['password']):
                count=attempt['count']+1 if attempt and attempt['until']>time.time() else 1
                c.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)',(key,count,time.time()+900))
                # 保留失败计数，退出事务后再返回错误。
                account_id=None
            else:
                account_id=row['id'];c.execute('DELETE FROM login_attempts WHERE key=?',(key,))
            if account_id:
                token=secrets.token_urlsafe(32)
                c.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
                c.execute('INSERT INTO sessions VALUES (?,?,?)',(digest(token),account_id,time.time()+7*86400))
        if not account_id:raise HTTPException(401,'账号或密码错误')
        return {'id':account_id,'username':name},token

    def issue_binding(self,account_id):
        code=secrets.token_hex(8).upper()
        with self.store.connect() as c:
            c.execute('DELETE FROM binding_codes WHERE account_id=? OR expires<?',(account_id,time.time()))
            c.execute('INSERT INTO binding_codes VALUES (?,?,?)',(digest(code),account_id,time.time()+600))
        return code

    def consume_binding(self,code,sender_id):
        if not sender_id:return '绑定失败：无法识别飞书身份。'
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT account_id FROM binding_codes WHERE code=? AND expires>?',(digest(code.upper()),time.time())).fetchone()
            if not row:return '验证码无效或已过期，请回网页重新获取。'
            existing=c.execute('SELECT account_id FROM feishu_bindings WHERE sender_id=?',(sender_id,)).fetchone()
            bound=c.execute('SELECT sender_id FROM feishu_bindings WHERE account_id=?',(row['account_id'],)).fetchone()
            if (existing and existing['account_id']!=row['account_id']) or (bound and bound['sender_id']!=sender_id):
                return '绑定失败：该飞书身份或网页账号已经绑定，不能覆盖已有绑定。'
            c.execute('INSERT OR IGNORE INTO feishu_bindings VALUES (?,?)',(sender_id,row['account_id']))
            c.execute('DELETE FROM binding_codes WHERE account_id=?',(row['account_id'],))
        return '飞书身份已绑定成功，请回网页刷新；只会显示你本人发起的任务。'


def is_binding_message(message):
    import unicodedata
    text=unicodedata.normalize('NFKC',message.get('text','')).strip()
    if len(text)>500:return False
    intent=bool(re.search(r'绑定|解绑|关联(?:账号|账户|飞书)|连接(?:账号|账户)|验证码',text))
    code_only=bool(re.fullmatch(r'[\s“”\"\'`：:]*[A-Fa-f0-9]{16}[\s“”\"\'`。！!]*',text))
    return intent or code_only


def binding_reply(store,message):
    import unicodedata
    if not (message.get('is_mentioned') or message.get('chat_type')=='p2p') or not is_binding_message(message):return None
    text=unicodedata.normalize('NFKC',message.get('text','')).strip()
    if re.search(r'(?:不要|不想|不需要|取消|别|暂不|无需).{0,8}(?:绑定|关联|连接)|解绑|解除绑定|不绑定',text):
        return '未执行绑定。如需绑定，请发送网页上的有效验证码；当前绑定关系保持不变。'
    codes=re.findall(r'(?<![A-Za-z0-9])[A-Fa-f0-9]{16}(?![A-Za-z0-9])',text)
    codes=list(dict.fromkeys(c.upper() for c in codes))
    if len(codes)>1:return '消息中有多个验证码，请只发送本次要使用的一个，避免绑定错误账号。'
    if not codes:return '可以直接发网页上的16位验证码，也可以说“帮我绑定账号，验证码是……”。仅说明绑定成功不会改变账号关系。'
    return Auth(store).consume_binding(codes[0],message.get('sender_id',''))


def register(app,auth):
    @app.post('/api/auth/register')
    def signup(data:Credentials,response:Response,request:Request):
        account,token=auth.credentials(data,True,request.client.host if request.client else 'local')
        response.set_cookie(COOKIE,token,httponly=True,samesite='strict',max_age=7*86400,secure=request.url.scheme=='https')
        return account

    @app.post('/api/auth/login')
    def login(data:Credentials,response:Response,request:Request):
        account,token=auth.credentials(data,False,request.client.host if request.client else 'local')
        response.set_cookie(COOKIE,token,httponly=True,samesite='strict',max_age=7*86400,secure=request.url.scheme=='https')
        return account

    @app.get('/api/auth/me')
    def me():
        current=user()
        with auth.store.connect() as c:
            row=c.execute('SELECT sender_id FROM feishu_bindings WHERE account_id=?',(current['id'],)).fetchone()
        sender=row['sender_id'] if row else ''
        name=auth.store.value('feishu_user_name:'+sender,'') if sender else ''
        return {**current,'feishu_bound':bool(sender),'feishu_name':name,
            'feishu_name_status':'available' if name else 'unavailable',
            'feishu_name_message':'飞书姓名暂未获取，不影响绑定和任务使用。' if sender and not name else ''}

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        token=request.cookies.get(COOKIE,'')
        with auth.store.connect() as c:c.execute('DELETE FROM sessions WHERE token=?',(digest(token),))
        response.delete_cookie(COOKIE)
        return {'ok':True}

    @app.post('/api/auth/binding-code')
    def code():return {'code':auth.issue_binding(user()['id']),'expires_in':600}
