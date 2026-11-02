import {withTimeout} from './conversion-timeout.ts';
let engine:any;
let initializing:Promise<void>|undefined;
async function initialize(report:(text:string)=>void){
 if(!window.crossOriginIsolated||typeof SharedArrayBuffer==='undefined')throw Error('DOC转换需要跨源隔离支持，请刷新页面并使用新版桌面Chrome或Edge');
 if(!('serviceWorker' in navigator))throw Error('当前浏览器不支持DOC转换，请使用桌面Chrome或Edge');
 report('正在启动 DOC 下载服务…');
 const registration=await withTimeout(navigator.serviceWorker.register('/office-sw.js'),20000,'DOC下载服务注册超时，请刷新后重试');
 const activation=registration.installing||registration.waiting;
 if(activation)activation.addEventListener('statechange',()=>{if(activation.state==='redundant')report('DOC下载服务启动失败，请刷新后重试');});
 await withTimeout(navigator.serviceWorker.ready,25000,'DOC下载服务未启动，请刷新后重试；若仍失败，请使用桌面Chrome/Edge');
 if(!navigator.serviceWorker.controller)await new Promise<void>((resolve,reject)=>{
  const done=()=>{clearTimeout(timer);navigator.serviceWorker.removeEventListener('controllerchange',done);resolve();};
  const timer=setTimeout(()=>{navigator.serviceWorker.removeEventListener('controllerchange',done);reject(Error('转换引擎启动超时，请刷新重试'));},20000);
  navigator.serviceWorker.addEventListener('controllerchange',done);
  if(navigator.serviceWorker.controller)done();
 });
 const scope=window as unknown as {ShibanOfficeEngine?:any};
 report('正在加载 DOC 转换模块…');
 if(!scope.ShibanOfficeEngine)await withTimeout(new Promise<void>((resolve,reject)=>{
  const script=document.createElement('script');script.type='module';script.src='/office/bridge.js';
  script.onload=()=>resolve();script.onerror=()=>{script.remove();reject(Error('DOC转换模块加载失败'));};document.head.appendChild(script);
 }),20000,'DOC转换模块下载超时，请检查网络后重试');
 if(!scope.ShibanOfficeEngine)throw Error('DOC转换模块不可用');
 engine=new scope.ShibanOfficeEngine({base:'/office/',timeoutMs:90000});
 report('正在下载和初始化 DOC 引擎，首次约30MB…');
 await new Promise<void>((resolve,reject)=>{
  const timer=setTimeout(()=>{engine.stop('初始化超时');reject(Error('DOC引擎下载或初始化超过3分钟，请检查网络后重试；已完成的资源会保留缓存'));},180000);
  engine.start().then(()=>{clearTimeout(timer);resolve();},()=>{clearTimeout(timer);reject(Error('DOC转换引擎加载失败，请刷新重试'));});
 });
}
export async function convertLegacyDoc(name:string,bytes:Uint8Array,onProgress:(text:string)=>void){
 const started=Date.now();let phase='正在准备 DOC 转换引擎…',lastUpdate=Date.now();
 const report=(text:string)=>{phase=text;lastUpdate=Date.now();onProgress(text);};
 const channel=typeof BroadcastChannel==='undefined'?undefined:new BroadcastChannel('lo-assets');
 const loaded=new Map<string,number>();
 if(channel)channel.onmessage=event=>{const data=event.data;if(typeof data?.file!=='string'||!Number.isFinite(data.loaded))return;loaded.set(data.file,data.loaded);const bytesLoaded=Array.from(loaded.values()).reduce((a,b)=>a+b,0),total=29703608;report(`DOC引擎：已下载 ${(bytesLoaded/1000000).toFixed(1)} / 29.7 MB（${Math.min(100,Math.round(bytesLoaded/total*100))}%），随后解压和初始化…`);};
 const ticker=setInterval(()=>{const seconds=Math.floor((Date.now()-started)/1000);onProgress(phase+` 已等待${seconds}秒。`+(Date.now()-lastUpdate>25000?' 当前阶段暂无新进度，网络可能较慢；超时会自动结束。':''));},1000);
 try{
 report(phase);
 if(!initializing)initializing=initialize(report).catch(e=>{initializing=undefined;throw e;});
 await initializing;
 }finally{clearInterval(ticker);channel?.close();}
 onProgress('正在转换 '+name+'，保留原表格和页面设置…');
 const file=new File([bytes as BlobPart],name,{type:'application/msword'});
 const result=await engine.convertFile(file,'docx');
 if(!result.blob||result.blob.size>12000000)throw Error('DOC转换结果过大或无效');
 return new Uint8Array(await result.blob.arrayBuffer());
}
