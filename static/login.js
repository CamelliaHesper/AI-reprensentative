const form=document.querySelector('#login-form'), errorBox=document.querySelector('#login-error');
async function authenticate(kind){
 if(!form.reportValidity())return;
 const buttons=form.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);errorBox.textContent='';
 try{
  const r=await fetch('/api/auth/'+kind,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
  const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'输入不符合要求，请检查账号和密码');
  const next=new URLSearchParams(location.search).get('next');
  let destination='/';
  if(next){const url=new URL(next,location.origin);if(url.origin===location.origin&&!url.pathname.startsWith('/login'))destination=url.pathname+url.search+url.hash}
  try{localStorage.setItem('shiban_auth_event',String(Date.now()))}catch{}location.replace(destination);
 }catch(e){errorBox.textContent=e.message}finally{buttons.forEach(b=>b.disabled=false)}
}
form.onsubmit=e=>{e.preventDefault();authenticate('login')};
document.querySelector('#register-account').onclick=()=>authenticate('register');
