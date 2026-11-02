/* 任务办理界面：独立文档 → 事项 → 填写 → 确认 → 结果。 */
let taskDocuments=[],applicationState=null,taskRenderVersion=0;
const taskBusy=t=>['queued','processing','receiving','collecting_files','preparing','generating'].includes(t.status);
const documentReady=d=>d.result.status==='ok'&&!d.result.warnings.some(w=>w.includes('未配置视觉模型'));
const basename=n=>n.replace(/\\/g,'/').split('/').pop();
const ext=n=>n.split('.').pop().toUpperCase();
const taskURL=()=>'/tasks/'+currentTask.id;

renderTask=async function(){
 const t=currentTask;if(!t)return;const version=++taskRenderVersion;
 try{
  const [docs,state]=await Promise.all([api('/tasks/'+t.id+'/documents'),api('/tasks/'+t.id+'/fulfillment')]);
  if(version!==taskRenderVersion||currentTask?.id!==t.id)return;
  taskDocuments=docs;applicationState=state;if(state?.stale){applicationState=null;if(step>=2)step=1;}if(step===4&&applicationState?.stage!=='generated')step=applicationState?3:0;
  const deliveryLabel=applicationState?.sent_message_id?'已确认 · 已发送到飞书':t.display_status==='confirmed'?'已确认':applicationState?.stage==='generated'?'文件已生成，待确认完成':null;
  const names=['浏览材料','选择事项','填写信息','确认生成','获取文件'];
  $('#app').innerHTML=`<a href="#tasks" class="back-link">← 我的任务</a><div class="task-heading"><div><span class="eyebrow">材料办理工作区</span><h1>${esc(t.analysis?.summary?.slice(0,42)||t.text.slice(0,42)||'附件办理任务')}</h1><p class="muted">发起人：${esc(t.initiator||'未知')} · ${sourceName(t.source)} · ${new Date(t.created*1000).toLocaleString('zh-CN')} · ${docs.length} 份文档</p></div><span class="badge ${deliveryLabel?'green':''}">${deliveryLabel||states[t.display_status||t.status]||({preparing:'正在识别表单',generating:'正在生成文件'}[t.status])||esc(t.status)}</span></div><div class="flow-steps">${names.map((n,i)=>`<button data-step="${i}" class="flow-step ${step===i?'selected':''}"><b>${i+1}</b><span>${n}</span></button>`).join('')}</div>${t.error?`<details class="notice warning"><summary>${deliveryLabel?'材料读取提示（办理结果已保留）':'部分材料需要检查，展开查看原因'}</summary><p>${esc(t.error)}</p></details>`:''}<div id="flow-body"></div>`;
  document.querySelectorAll('[data-step]').forEach(b=>b.onclick=()=>{const n=Number(b.dataset.step);if(n>=2&&!applicationState)return toast('请先选择办理事项和模板');if(n===4&&applicationState?.stage!=='generated')return toast('请先确认并生成文件');step=n;renderTask()});
  if(step===0)renderMaterials(t);else if(step===1)renderOptions(t);else if(step===2)renderFields();else if(step===3)renderReview();else renderOutputs();
 }catch(e){toast(e.message)}
};

function renderMaterials(t){
 const docs=taskDocuments;const ok=docs.filter(documentReady).length;
 $('#flow-body').innerHTML=`<div class="material-overview"><div><h2>每一份材料，单独看清楚</h2><p class="muted">展开文档查看提取内容，也可以打开原版预览核对。</p></div><span class="tag">${ok} / ${docs.length} 已读取</span></div><div class="document-list">${docs.map((d,i)=>`<details class="document-item"><summary><span class="file-type type-${ext(d.name).toLowerCase()}">${ext(d.name)}</span><span class="document-name"><strong>${esc(basename(d.name))}</strong><small>${esc(d.name.includes('/')?d.name.slice(0,d.name.lastIndexOf('/')):'独立附件')}</small></span><span class="document-status ${d.result.status==='error'?'problem':''}">${d.result.status==='error'?'需检查':documentReady(d)?'已读取':'待视觉读取'}</span><span class="chevron">⌄</span></summary><div class="document-content">${d.result.error?`<div class="notice warning">${esc(d.result.error)}</div>`:''}${d.result.warnings.length?`<details class="document-hints"><summary>${d.result.warnings.length} 条读取提示</summary>${d.result.warnings.map(w=>`<p class="muted">${esc(w)}</p>`).join('')}</details>`:''}<div class="doc-toolbar"><a href="/api/tasks/${t.id}/documents/${d.id}/download">下载原文档</a>${['DOC','DOCX','PDF'].includes(ext(d.name))?`<a target="_blank" rel="noopener" href="/api/tasks/${t.id}/documents/${d.id}/preview">打开原版预览 ↗</a>`:''}</div><pre class="document-text">${esc(d.result.text||'暂无可读取内容，请查看提示或原文件。')}</pre></div></details>`).join('')||'<div class="card empty">尚无独立文档，请先收齐附件并开始解析。</div>'}</div><details class="source-notice"><summary>原始通知与补充说明</summary><div class="message">${esc([t.text,...(t.metadata.supplements||[])].join('\n\n'))}</div></details><div class="card spaced"><div class="row"><div><h3>补充材料</h3><p class="muted">可上传 Word、PDF、Excel、ZIP；重新解析后纳入本次需求。</p></div><form id="more-files" class="inline-upload"><input type="file" name="file" multiple accept=".doc,.docx,.pdf,.xlsx,.xls,.csv,.zip,.txt" required><button ${taskBusy(t)?'disabled':''}>上传</button></form></div></div><div class="flow-actions"><button class="ghost" id="flow-reparse" ${taskBusy(t)?'disabled':''}>${taskBusy(t)?'正在处理…':'重新解析材料'}</button><button id="to-options" ${taskBusy(t)?'disabled':''}>下一步：选择办理事项 →</button></div>`;
 on('more-files','submit',async e=>{e.preventDefault();e.target.querySelector('button').disabled=true;try{for(const file of e.target.elements.file.files){const fd=new FormData();fd.append('file',file);await api(taskURL()+'/files',{method:'POST',body:fd})}await api(taskURL()+'/retry',{method:'POST'});currentTask=await api(taskURL());await renderTask();toast('附件已加入，正在重新解析')}finally{}});
 on('flow-reparse','click',async e=>{e.target.disabled=true;await api(taskURL()+'/retry',{method:'POST'});currentTask=await api(taskURL());renderTask()});
 on('to-options','click',()=>{step=1;renderTask()});
}

function renderOptions(t){
 const options=t.analysis?.options||[];
 $('#flow-body').innerHTML=`<div class="section-intro"><span class="eyebrow">先选要办的事</span><h2>这次你想申请哪一项？</h2><p class="muted">以下选项由模型结合通知与附件整理，请核对条件和对应模板。</p></div>${options.length?`<div class="option-grid">${options.map((o,i)=>`<label class="application-option"><input type="radio" name="application-option" value="${i}" ${applicationState?.option.id===o.id?'checked':''}><span class="option-symbol">${String(i+1).padStart(2,'0')}</span><h3>${esc(o.title)}</h3><p>${esc(o.description)}</p><ul>${(o.requirements||[]).map(r=>`<li>${esc(r)}</li>`).join('')}</ul><details><summary>查看原文依据</summary><p class="muted">${esc(o.evidence||'请对照通知核实')}</p></details></label>`).join('')}</div><div class="card spaced"><label>使用的申请表模板<select id="template-choice"><option value="">先选择上方事项</option></select></label><p id="template-guidance" class="muted">只填原模板中的内容，不重新设计表格或改变页设置。</p><div id="template-missing"></div></div><div class="flow-actions"><button id="prepare-fields">识别表单字段，下一步 →</button></div>`:`<div class="card empty"><h3>暂未识别到可办理的事项</h3><p>请配置模型，或返回材料页重新解析。系统不会用预设的申报选项代替实际需求。</p><button id="back-materials">返回材料</button></div>`}`;
 const fillTemplates=()=>{const choice=$('input[name="application-option"]:checked');const button=$('#prepare-fields');if(!choice){button.disabled=true;return}const option=options[Number(choice.value)],ids=option.template_ids||[];const allWord=taskDocuments.filter(d=>['DOC','DOCX'].includes(ext(d.name)));const docs=allWord.filter(d=>ids.includes(d.id));const noticeOnly=option.document_mode==='none'||(option.document_mode!=='word'&&!ids.length);button.dataset.noticeOnly=noticeOnly?'true':'false';if(noticeOnly){$('#template-choice').parentElement.hidden=true;$('#template-guidance').textContent=option.template_note||'该选项不在本站生成 Word 附件，请查看后续办理提醒。';$('#template-missing').innerHTML='<div class="notice">本选项不进入附件填写流程。请按原通知确认是否需在指定系统申请、打印或另行提供材料。</div>';button.disabled=false;button.textContent='查看办理提醒 →';return}$('#template-choice').parentElement.hidden=false;button.textContent='识别表单字段，下一步 →';$('#template-choice').innerHTML=docs.length?docs.map(d=>`<option value="${d.id}">${esc(basename(d.name))}</option>`).join(''):'<option value="">尚未找到此事项对应的 Word 模板</option>';button.disabled=!docs.length;$('#template-guidance').textContent=option.template_note||'只填原模板中的内容，不重新设计表格或改变页设置。';$('#template-missing').innerHTML=docs.length?'':`<div class="notice warning"><strong>缺少“${esc(option.title)}”对应的申请表</strong><p>可能是本次未上传该表，或模型尚未确认关联。重新解析不能补回不存在的附件，系统不会套用其他事项的专用表格。</p><p>本次已有 Word 文档：${allWord.length?allWord.map(d=>esc(basename(d.name))).join('；'):'暂无'}。</p><p>请对照原通知：若要求在学校或在线系统申请，请先按该渠道办理，取得可编辑 Word 模板后再上传。PDF 不应仅修改扩展名充当 Word。</p><button type="button" class="ghost" id="upload-missing-template">返回材料页，补充正确模板</button></div>`;const upload=$('#upload-missing-template');if(upload)upload.onclick=()=>{step=0;renderTask()}};
 document.querySelectorAll('input[name="application-option"]').forEach(e=>e.onchange=fillTemplates);fillTemplates();
 on('back-materials','click',()=>{step=0;renderTask()});
 on('prepare-fields','click',async e=>{const selected=$('input[name="application-option"]:checked');if(selected&&e.target.dataset.noticeOnly==='true'){renderNoAttachment(options[Number(selected.value)],t);return}const did=$('#template-choice').value;if(!selected||!did)return toast('请选择事项及对应的Word模板');e.target.disabled=true;e.target.textContent='正在读取原模板并识别字段…';try{applicationState=await api(taskURL()+'/selection',{method:'POST',body:JSON.stringify({option_id:options[Number(selected.value)].id,template_id:did})});step=2;await renderTask()}finally{if(e.target.isConnected){e.target.disabled=false;e.target.textContent='识别表单字段，下一步 →'}}});
}

function renderFields(){
 const s=applicationState;if(!s)return;$('#flow-body').innerHTML=`<div class="section-intro"><span class="eyebrow">${esc(s.option.title)}</span><h2>补齐这份申请表的信息</h2><p class="muted">模板：${esc(basename(s.template_name))}。已带入原有值和个人资料，修改后将替换对应位置。模板中的示例姓名、学号、电话等并非你的真实信息，请逐项核对。</p></div><div class="form-layout"><form id="application-form" class="card"><div class="fields">${compactFields(s.fields)}</div><div class="flow-actions"><button>保存并检查填写内容 →</button></div></form><div class="card form-aside"><div id="form-assistant"></div><h3>按原模板填写</h3><p class="muted">不生成虚构的经历、成绩或证明信息。学校审核、签字盖章等内容请保持原状。</p>${s.notes.map(n=>`<div class="notice">${esc(n)}</div>`).join('')}<a target="_blank" rel="noopener" href="/api/tasks/${currentTask.id}/documents/${s.document_id}/preview">查看原版申请表 ↗</a></div></div>`;
 mountFormAssistant(s);
 mountEditorLock(s);
 on('application-form','submit',async e=>{e.preventDefault();if(s.editor?.active){$('#editor-lock-notice')?.scrollIntoView({behavior:'smooth',block:'center'});return toast('请先返回在线编辑保存，或点击放弃在线编辑')}const button=e.submitter||e.target.querySelector('button');button.disabled=true;try{applicationState=await api(taskURL()+'/form',{method:'PUT',body:JSON.stringify({revision:s.revision,values:Object.fromEntries(new FormData(e.target))})});step=3;await renderTask()}finally{if(button.isConnected)button.disabled=false}});
}

function renderReview(){const s=applicationState;if(!s)return;
 $('#flow-body').innerHTML=`<div class="section-intro"><span class="eyebrow">生成前最后确认</span><h2>请核对这次的申请和填写内容</h2><p class="muted">确认前不会生成已填写文件。修改任一内容后，需要重新确认。</p></div><div class="card"><div class="review-title"><h3>${esc(s.option.title)}</h3><span class="tag">${esc(basename(s.template_name))}</span></div><div class="review-fields">${s.fields.map(f=>`<div class="review-field"><span>${esc(f.label)}</span><div><small class="muted">原值：${esc(f.original||'空白')}</small><p>${esc(f.value||'未填写')}</p></div></div>`).join('')}</div><div class="notice">保留原表格、页设置和目标位置的文字样式。长段内容可能影响分页，请生成后检查Word或PDF预览。</div><label class="confirm-line"><input id="generation-consent" type="checkbox"> 我已核对事项、模板和上述内容，确认生成填写后的 Word。</label><div class="flow-actions"><button id="edit-values" class="ghost">返回修改</button><button id="generate-word">确认并生成 Word →</button></div></div>`;
 on('edit-values','click',()=>{step=2;renderTask()});
 on('generate-word','click',async e=>{if(!$('#generation-consent').checked)return toast('请先勾选确认');if(s.stage==='filling')return toast('请先填写并保存表单');e.target.disabled=true;e.target.textContent='正在按原模板生成…';try{applicationState=await api(taskURL()+'/generate',{method:'POST',body:JSON.stringify({revision:s.revision,confirmed:true})});step=4;await renderTask()}finally{if(e.target.isConnected){e.target.disabled=false;e.target.textContent='确认并生成 Word →'}}});
}

function renderOutputs(){const s=applicationState;if(!s)return;
 $('#flow-body').innerHTML=`<div class="result-banner"><div class="success-circle">✓</div><h2>申请材料已生成到本地</h2><p>原模板：${esc(basename(s.template_name))}</p><p>结果文件名：<strong>${esc(s.output_name||'按模板名称保存')}</strong></p><div class="flow-actions"><a class="button" href="/api/tasks/${currentTask.id}/outputs/docx">下载已填写 Word</a>${(s.outputs.includes('pdf')||s.edited_path)?`<a class="button ghost" target="_blank" rel="noopener" href="/api/tasks/${currentTask.id}/outputs/pdf?v=${encodeURIComponent(s.revision)}">查看最新版本预览 ↗</a>`:''}<a class="button" href="/editor.html?task=${encodeURIComponent(currentTask.id)}">在线编辑整份 Word</a><button class="ghost" id="revise-output">修改后重新生成</button></div>${(s.warnings||[]).filter(w=>!s.edited_path||!w.includes('原 PDF 预览已停用')).map(w=>`<div class="notice warning">${esc(w)}</div>`).join('')}<p class="muted">文件先保存在本项目 data/outputs/${esc(currentTask.id)}/${esc(s.revision)}/ 文件夹。可选择另存位置；检查预览后，确认发送到本任务来源群。</p><div class="flow-actions"><button id="save-word-as" class="ghost">选择位置另存 Word</button><button id="complete-task" class="ghost" ${currentTask.display_status==='confirmed'?'disabled':''}>${currentTask.display_status==='confirmed'?'已确认完成':'检查完毕，确认完成'}</button><button id="send-word-feishu" ${s.sent_message_id?'disabled':''}>${s.sent_message_id?'已发送到飞书':'检查完毕，发送到原群聊'}</button></div><p id="output-feedback" aria-live="polite"></p></div>${(s.outputs.includes('pdf')||s.edited_path)?`<iframe class="pdf-preview" title="最新 Word 版本预览" src="/api/tasks/${currentTask.id}/outputs/pdf?v=${encodeURIComponent(s.revision)}"></iframe>`:''}`;
 on('complete-task','click',()=>confirmTaskCompletion(s.revision));on('revise-output','click',()=>{step=2;renderTask()});
 on('save-word-as','click',async()=>{
  const name=s.output_name||basename(s.template_name).replace(/\.docx?$/i,'')+'_已填写.'+(s.word_format||'docx');
  if(!window.showSaveFilePicker){$('#output-feedback').textContent='当前浏览器不支持位置选择，请点击下载 Word，并在浏览器下载设置中开启每次询问保存位置。';return}
  try{
   const handle=await window.showSaveFilePicker({suggestedName:name});
   const response=await fetch('/api/tasks/'+currentTask.id+'/outputs/docx');if(!response.ok)throw Error('读取文件失败');
   const blob=await response.blob(), stream=await handle.createWritable();await stream.write(blob);await stream.close();
   $('#output-feedback').textContent='已另存为 '+handle.name+'，保存在你刚才选择的文件夹。';
  }catch(error){if(error.name!=='AbortError')toast(error.message)}
 });
 on('send-word-feishu','click',async e=>{
  if(!confirm('确认已检查 Word 内容和版式，并将这份文件发送到任务来源飞书群聊？'))return;
  const b=e.target;b.disabled=true;b.textContent='正在发送…';
  try{applicationState=await api(taskURL()+'/send-feishu',{method:'POST',body:JSON.stringify({revision:s.revision,confirmed:true})});b.textContent='已发送到飞书';$('#output-feedback').textContent='机器人已将 Word 文件发送到原群聊。'}
  catch(error){b.disabled=false;b.textContent='重试发送到原群聊';toast(error.message)}
 });
}

const baseSettings=settings;
settings=async function(v){await baseSettings(v);if(v!==routeVersion)return;const s=await api('/status');if(v!==routeVersion)return;
 $('#app').insertAdjacentHTML('beforeend',`${s.initiator_name_error?`<div class="notice warning">${esc(s.initiator_name_error)}</div>`:''}<div class="card spaced"><h2>扫描 PDF 的视觉模型</h2><p>当前状态：${s.vision_ready?'已配置，待实际图片调用验证':'未配置；扫描页会提示尚未读取'}</p><p class="muted">不使用 OCR。系统把扫描页转成图片，发送给你配置的支持图片输入的视觉模型。</p><pre>VISION_BASE_URL=支持图片的兼容API地址\nVISION_API_KEY=视觉模型密钥\nVISION_MODEL=视觉模型名称\nMAX_PDF_PAGES=30</pre><p class="muted">在 .env 填写后重启网页，再点击重新解析。不会默认用文字模型处理图片。</p></div>`);
};


function mountFormAssistant(state){
 const host=$('#form-assistant');
 host.innerHTML=`<div class="assistant-heading"><span class="eyebrow">填写助手</span><h3>用一句话，帮你填好</h3></div><p class="muted">起草总结、润色内容，或计算排名占比。</p><div class="assistant-examples"><button type="button" class="ghost" data-prompt="根据我已填写的真实信息，起草150字以内的个人总结，并填入对应字段。信息不足请先问我。">起草个人总结</button><button type="button" class="ghost" data-prompt="请帮我填写排名百分比。我需要提供哪些排名信息？">计算排名百分比</button></div><div id="assistant-reply" class="assistant-reply" aria-live="polite">例如：我的排名是第2名，共30人，请填写排名百分比。</div><textarea id="assistant-prompt" rows="4" maxlength="2000" placeholder="告诉助手你想填写或修改什么…" aria-label="填写助手要求"></textarea><small class="muted">点击发送，将把本页已填内容及要求发送给你配置的模型。建议回填后仍可修改。</small><div class="flow-actions"><button type="button" id="assistant-undo" class="ghost" hidden>撤销回填</button><button type="button" id="assistant-send">发送给助手</button></div><hr>`;
 const form=$('#application-form'), prompt=$('#assistant-prompt'), reply=$('#assistant-reply');
 let undo=null;
 host.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{prompt.value=b.dataset.prompt;prompt.focus()});
 on('assistant-undo','click',()=>{if(!undo)return;for(const [key,value] of Object.entries(undo)){const el=form.elements.namedItem(key);if(el)el.value=value}undo=null;$('#assistant-undo').hidden=true;reply.textContent='已撤销上次回填。'});
 on('assistant-send','click',async e=>{
  if(!prompt.value.trim())return toast('请先输入你的要求');
  const values=Object.fromEntries(new FormData(form)), button=e.target;
  button.disabled=true;button.textContent='正在思考…';
  try{
   const result=await api(taskURL()+'/form-assistant',{method:'POST',body:JSON.stringify({revision:state.revision,values,message:prompt.value})});
   if(!host.isConnected||applicationState.revision!==result.revision)return;
   const changed=result.updates.some(p=>form.elements.namedItem(p.target)?.value!==values[p.target]);
   if(changed){reply.textContent='你刚刚修改了目标字段，为保留你的输入，本次建议未回填。请再次发送。';return}
   undo={};
   for(const patch of result.updates){const el=form.elements.namedItem(patch.target);if(!el)continue;undo[patch.target]=el.value;el.value=patch.value;el.classList.add('assistant-filled');const folded=el.closest('details');if(folded)folded.open=true;el.dispatchEvent(new Event('input',{bubbles:true}));}
   $('#assistant-undo').hidden=!Object.keys(undo).length;
   reply.textContent=result.reply+(result.updates.length?'（已回填，尚未保存）':'');
  }catch(error){reply.textContent=error.message||'助手暂时无法处理，请重试。'}
  finally{button.disabled=false;button.textContent='发送给助手'}
 });
}


function renderNoAttachment(option,task){
 $('#flow-body').innerHTML=`<div class="card"><span class="eyebrow">办理提醒</span><h2>${esc(option.title)}</h2><div class="notice"><strong>该选项无需在本站生成附件</strong><p>${esc(option.template_note||'本次没有关联的本地 Word 填写模板，不进入附件填写和生成流程。')}</p></div><h3>接下来怎么办</h3><p>${esc(task.analysis?.submission||'请按原通知要求到指定渠道办理。')}</p><p class="muted">这不表示申请已提交，也不表示原通知要求的证明材料或系统打印表可以省略。</p><ul>${(option.requirements||[]).map(r=>`<li>${esc(r)}</li>`).join('')}</ul><button id="complete-no-attachment">已检查，确认办理要求</button> <button class="ghost" id="back-option-choice">返回选择事项</button></div>`;
 on('complete-no-attachment','click',()=>confirmTaskCompletion(''));on('back-option-choice','click',()=>{step=1;renderTask()});
}


function fieldControl(f){
 const common=`name="${esc(f.target)}" ${f.required?'required':''} maxlength="15000"`;
 const control=f.kind==='textarea'?`<textarea ${common}>${esc(f.value)}</textarea>`:f.kind==='select'&&f.choices?.length?`<select ${common}><option value="">请选择</option>${[...new Set([f.value,...f.choices].filter(Boolean))].map(c=>`<option ${c===f.value?'selected':''}>${esc(c)}</option>`).join('')}</select>`:`<input ${common} type="text" value="${esc(f.value)}">`;
 return `<label class="${f.kind==='textarea'?'wide-field':''}">${esc(f.label)}${f.required?' <span class="required-mark">*</span>':''}${control}<small class="muted">${esc(f.help||'')}</small></label>`;
}
function compactFields(fields){
 const groups=new Map(),plain=[];
 for(const field of fields){
  let group=field.repeat_group||'',row=field.repeat_row||1;
  if(!group){const match=field.label.match(/^(获奖情况|获奖经历|社会活动|社会工作|学生工作)[-—：:]/);if(match){group=match[1];const n=field.label.match(/[（(]第([一二三四五六七八九十\d]+)行[）)]/);if(n)row=Number(n[1])||'一二三四五六七八九十'.indexOf(n[1])+1}}
  if(!group){plain.push(fieldControl(field));continue}
  if(!groups.has(group))groups.set(group,new Map());const rows=groups.get(group);if(!rows.has(row))rows.set(row,[]);rows.get(row).push(field);
 }
 for(const [group,rows] of groups){
  const entries=[...rows.entries()].sort((a,b)=>a[0]-b[0]);
  const renderRow=(row,items)=>`<div class="experience-row"><small>第${esc(row)}条</small><div class="experience-fields">${items.map(f=>fieldControl({...f,label:f.label.replace(/^[^-—：:]+[-—：:]/,'').replace(/[（(]第[^）)]+行[）)]/,'').trim()})).join('')}</div></div>`;
  const first=entries.shift();const extra=entries.map(([row,items])=>{const filled=items.some(f=>String(f.value||'').trim()||f.required);return filled?renderRow(row,items):`<details class="experience-extra"><summary>＋ 添加第${esc(row)}条${esc(group)}</summary>${renderRow(row,items)}</details>`}).join('');
  plain.push(`<section class="experience-group wide-field"><h3>${esc(group)}</h3>${renderRow(first[0],first[1])}${extra}</section>`);
 }
 return plain.join('');
}


async function confirmTaskCompletion(revision){
 if(!confirm('确认已核对当前办理要求和结果？这只记录你的确认，不代表已向学校或在线表单提交。'))return;
 await api(taskURL()+'/complete',{method:'POST',body:JSON.stringify({confirmed:true,revision})});
 currentTask=await api(taskURL());await renderTask();toast('任务已确认');
}


function mountEditorLock(state){
 if(!state.editor?.active)return;
 const form=$('#application-form');
 form.closest('.form-layout').insertAdjacentHTML('beforebegin',`<div id="editor-lock-notice" class="notice warning wide-field"><strong>有一份在线编辑尚未结束</strong><p>如需保留 Word 中的修改，请返回在线编辑并保存。若要继续填写本页表单，可以放弃本次在线编辑；本页当前输入会保留。</p><div class="flow-actions"><a class="button" href="/editor.html?task=${encodeURIComponent(currentTask.id)}">返回在线编辑并保存</a><button type="button" class="ghost" id="cancel-editor-lock">放弃在线编辑，继续填写</button></div></div>`);
 on('cancel-editor-lock','click',async e=>{
  if(!confirm('确认放弃尚未完成的在线 Word 编辑？此前已完成的 Word 版本和当前网页表单输入会保留。'))return;
  const button=e.target;button.disabled=true;
  try{
   await api(taskURL()+'/editor/cancel',{method:'POST'});
   state.editor.active=false;
   $('#editor-lock-notice')?.remove();
   toast('已取消在线编辑，可以继续保存表单；当前输入已保留');
  }finally{if(button.isConnected)button.disabled=false}
 });
}
