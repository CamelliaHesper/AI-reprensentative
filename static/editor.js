const tid=new URLSearchParams(location.search).get('task');
const base='/api/tasks/'+encodeURIComponent(tid);
const statusEl=document.querySelector('#editor-status'), finish=document.querySelector('#finish');
const returnLink=document.querySelector('#return-link');returnLink.href='/#task/'+encodeURIComponent(tid)+'/results';
let editor,changed=false,pending=Promise.resolve(),exportWaiter=null;
async function call(path,body={}){const r=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(r.status===401){location.replace('/login.html?next='+encodeURIComponent(location.pathname+location.search));throw Error('请先登录')}if(!r.ok)throw Error(d.detail||'操作失败');return d}
function error(e){document.querySelector('#error').textContent=e.message}
async function open(){
 try{
  const data=await call('/editor/start');
  const script=document.createElement('script');script.src=data.server+'/web-apps/apps/api/documents/api.js';
  script.onerror=()=>error(Error('无法加载本地在线编辑器，请检查文档服务。'));
  script.onload=()=>{
   data.config.events={onAppReady:()=>{statusEl.textContent='请修改文档，完成后点击编辑器左上角保存，再返回结果页。';finish.disabled=false},
    onDownloadAs:e=>{if(exportWaiter){const waiting=exportWaiter;exportWaiter=null;waiting.resolve(e.data.url)}},
    onDocumentStateChange:e=>{if(e.data){changed=true;statusEl.textContent='有修改，请点击编辑器左上角保存';pending=pending.then(()=>call('/editor/dirty')).catch(error)}},
    onError:e=>error(Error('编辑器错误：'+(e.data?.errorDescription||'请检查文档服务连接')))};
   editor=new DocsAPI.DocEditor('document-editor',data.config);
  };document.head.append(script);
 }catch(e){error(e);statusEl.textContent='在线编辑暂未就绪'}
}
async function exportCurrentDocument(){
 return new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>{exportWaiter=null;reject(Error('导出等待超时，请保持页面打开后重试'))},60000);
  exportWaiter={resolve:url=>{clearTimeout(timer);resolve(url)}};
  try{editor.downloadAs('docx')}catch(e){clearTimeout(timer);exportWaiter=null;reject(e)}
 });
}
finish.onclick=async()=>{finish.disabled=true;document.querySelector('#error').textContent='';try{await pending;statusEl.textContent='正在保存最新修改，请保持页面打开…';const saving=await call('/editor/save');if(saving.export_required){statusEl.textContent='正在导出当前编辑版本…';const url=await exportCurrentDocument();await pending;await call('/editor/export',{nonce:saving.nonce,url})}let done=false;for(let i=0;i<20;i++){try{await call('/editor/finish');done=true;break}catch(e){if(i===19)throw e;await new Promise(r=>setTimeout(r,1000))}}if(done){changed=false;editor?.destroyEditor();location.href=returnLink.href}}catch(e){error(e);statusEl.textContent='未完成保存，修改仍保留在编辑器中，请重试'}finally{finish.disabled=false}};
document.querySelector('#cancel').onclick=async()=>{if(!confirm('放弃本次在线编辑，继续使用之前的文件？'))return;try{await pending;await call('/editor/cancel');changed=false;editor?.destroyEditor();location.href=returnLink.href}catch(e){error(e)}};
window.addEventListener('beforeunload',e=>{if(changed){e.preventDefault();e.returnValue=''}});
open();

window.addEventListener('storage',e=>{if(e.key==='shiban_auth_event'){changed=false;editor?.destroyEditor();location.replace('/login.html')}});
