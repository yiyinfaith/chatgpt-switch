'use strict';
const $ = id => document.getElementById(id);
const initialPage=new URLSearchParams(location.search).get('view');
const token = location.hash.slice(1) || sessionStorage.getItem('switch-token') || '';
if(token){sessionStorage.setItem('switch-token', token);history.replaceState(null,'',location.pathname);}
let state = null, initial = true, envSignature = '', profileSignature = '', busy = false, lastJob = 0, toastTimer, closing = false;
let selectedProfile = null, setupSignature = '', profileFromSetup = false, profileRequest = false, setupRequest = false;
const modeNames = {account:'账号额度',thirdparty:'第三方 API',mixed:'配置状态不一致',setup:'等待配置',error:'配置需要检查'};
const westClock = new Intl.DateTimeFormat('zh-CN',{timeZone:'America/Los_Angeles',year:'numeric',month:'long',day:'numeric',weekday:'long',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
const dialogs = [...document.querySelectorAll('dialog')];
function renderWestClock(){
 const parts=Object.fromEntries(westClock.formatToParts(new Date()).filter(part=>part.type!=='literal').map(part=>[part.type,part.value]));
 $('west-time').textContent=`${parts.hour}:${parts.minute}:${parts.second}`;
 $('west-date').textContent=`${parts.year}年${parts.month}月${parts.day}日 ${parts.weekday}`;
}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4200);}
function fieldError(id,text){$(id).textContent=text;$(id).hidden=!text;}
function showDialog(id){dialogs.forEach(d=>{if(d.open)d.close();});$(id).showModal();}
function radio(name){return document.querySelector(`input[name="${name}"]:checked`)?.value;}
function setRadio(name,value){document.querySelectorAll(`input[name="${name}"]`).forEach(e=>e.checked=e.value===value);}
async function request(path,body){
 const options={headers:{Authorization:'Bearer '+token}};
 if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);const value=await response.json();
 if(!response.ok){const error=new Error(value.error||'操作未完成');error.code=value.code;throw error;}return value;
}
async function act(path,body={}){try{const result=await request(path,body);await refresh();return result;}catch(e){toast(e.message);return null;}}
function pending(yes){
 busy=yes;
 ['account-action','thirdparty-action','profile-add','setup-open','refresh','detect-again','quit','save-proxy'].forEach(id=>$(id).disabled=yes);
 ['profile-save','profile-save-apply','profile-delete'].forEach(id=>$(id).disabled=yes||profileRequest);
 $('setup-add').disabled=yes||setupRequest;
 $('setup-save').disabled=yes||setupRequest||!state?.profiles.some(p=>p.id===selectedProfile)||!!state?.profileError;
 document.querySelectorAll('.apply-profile,.edit-profile,input[name="selected-profile"],input[name="setup-mode"]').forEach(e=>e.disabled=yes||setupRequest);
 $('main-progress').hidden=!yes;
 updateInstallButton();
}
function renderProfiles(){
 const signature=JSON.stringify([state.profiles,state.activeProfile]);if(signature===profileSignature)return;profileSignature=signature;
 const container=$('profiles-list');container.replaceChildren();$('profiles-empty').hidden=state.profiles.length>0;
 for(const profile of state.profiles){
  const row=document.createElement('div');row.className='profile-row'+(profile.id===state.activeProfile?' current':'');row.dataset.id=profile.id;
  const avatar=document.createElement('div');avatar.className='profile-avatar';avatar.textContent=profile.name.slice(0,1).toUpperCase();
  const info=document.createElement('div');info.className='profile-info';const title=document.createElement('strong');title.textContent=profile.name;
  const summary=document.createElement('small');summary.textContent=profile.model+' · '+profile.baseUrl;summary.title=summary.textContent;info.append(title,summary);row.append(avatar,info);
  if(profile.id===state.activeProfile){const badge=document.createElement('span');badge.className='current-tag';badge.textContent='✓ 当前使用';row.append(badge);}
  const edit=document.createElement('button');edit.type='button';edit.className='quiet edit-profile';edit.textContent='编辑';edit.addEventListener('click',()=>openProfile(profile));
  const apply=document.createElement('button');apply.type='button';apply.className='apply-profile';apply.textContent='应用';apply.addEventListener('click',()=>applyProfile(profile.id));row.append(edit,apply);container.append(row);
 }
}
function renderSetupProfiles(){
 if(!$('setup-dialog').open)return;
 if(!state.profiles.some(p=>p.id===selectedProfile))selectedProfile=null;
 const signature=JSON.stringify([state.profiles,state.activeProfile,selectedProfile,state.profileError]);
 if(signature===setupSignature)return;setupSignature=signature;
 const container=$('setup-profiles');container.replaceChildren();
 $('setup-count').textContent=`（${state.profiles.length}）`;
 $('setup-empty').hidden=state.profiles.length>0||!!state.profileError;
 for(const profile of state.profiles){
  const row=document.createElement('div');row.className='profile-row setup-profile'+(profile.id===selectedProfile?' selected':'');
  const label=document.createElement('label');label.className='profile-choice';
  const choice=document.createElement('input');choice.type='radio';choice.name='selected-profile';choice.value=profile.id;choice.checked=profile.id===selectedProfile;
  choice.addEventListener('change',()=>{selectedProfile=profile.id;fieldError('setup-error','');renderSetupProfiles();pending(busy);});
  const info=document.createElement('span');info.className='profile-info';
  const title=document.createElement('strong');title.textContent=profile.name;
  const model=document.createElement('small');model.textContent='模型：'+profile.model;
  const address=document.createElement('small');address.textContent=profile.baseUrl;address.title=profile.baseUrl;
  const key=document.createElement('small');key.textContent=(profile.envKey||'沿用当前变量名')+' · '+(profile.hasSecret?'已保存密钥':'沿用 .env 中的密钥');
  info.append(title,model,address,key);label.append(choice,info);row.append(label);
  if(profile.id===state.activeProfile){const badge=document.createElement('span');badge.className='current-tag';badge.textContent='当前生效';row.append(badge);}
  const edit=document.createElement('button');edit.type='button';edit.className='quiet edit-profile';edit.textContent='编辑';edit.addEventListener('click',()=>openProfile(profile,true));row.append(edit);container.append(row);
 }
 const selected=state.profiles.find(p=>p.id===selectedProfile);
 $('setup-selection').textContent=selected?'已勾选「'+selected.name+'」，点击下方按钮保存生效。':state.profiles.length?'请勾选一套配置；只有点击保存后才会生效。':'';
 if(state.profileError)fieldError('setup-error',state.profileError);
}
function renderEnvironment(){
 const env=state.environment;
 $('platform-label').textContent=state.platform.label;
 $('install-platform').textContent=state.platform.label;
 $('install-platform-detail').textContent=[state.platform.arch==='arm64'?'ARM64 架构':'x64 架构',state.platform.libc||'',state.platform.desktopSupport==='supported'?'官方桌面包可用':'按当前系统提供可用组件'].filter(Boolean).join(' · ');
 if(!env){$('install-label').textContent='环境检测中';return;}
 const signature=JSON.stringify(env);if(signature===envSignature)return;envSignature=signature;
 for(const component of ['cli','desktop']){
  const installed=!!env[component], available=component==='cli'?env.platform.cliSupported:env.platform.desktopSupport!=='unavailable'&&env.desktopKnown;
  const box=$('install-'+component);box.checked=!installed&&available;box.disabled=installed||!available;
  const badge=$(component+'-state');badge.textContent=installed?'已安装':!available?'暂无适用方案':'未安装';badge.className='install-state '+(installed?'present':available?'absent':'');
 }
 $('cli-description').textContent=env.cli?'已检测到可用 CLI':'使用 OpenAI 官方独立安装脚本';
 $('desktop-description').textContent=env.desktop?'已检测到官方桌面应用':state.platform.system==='Windows'?'Microsoft Store 官方包':state.platform.system==='Darwin'?'官方签名 DMG · 安装到 ~/Applications':state.platform.family==='debian'?'官方 DEB · 由 APT 安装':['fedora','suse'].includes(state.platform.family)?'官方 RPM · 由系统包管理器安装':'此系列未提供官方桌面安装包';
 $('support-note').textContent=env.warning||env.platform.reason;$('support-note').hidden=!$('support-note').textContent;
 $('compatible-row').hidden=env.platform.desktopSupport!=='compatible'||!!env.desktop;
 $('winget-row').hidden=state.platform.system!=='Windows'||!!env.winget||!!env.desktop;
 $('install-label').textContent=(!env.cli||!env.desktop)?'安装与代理 · 待配置':'安装与代理';
 updateInstallButton();
}
function updateInstallButton(){
 const selected=['cli','desktop'].some(k=>$('install-'+k).checked&&!$('install-'+k).disabled);
 $('install-start').disabled=busy||!state?.environment||!selected;
 $('install-cancel').hidden=!(busy&&state?.job.kind==='install');
 $('install-start').hidden=busy&&state?.job.kind==='install';
}
function render(){
 const cfg=state.config, job=state.job;
 const active=state.profiles.find(p=>p.id===state.activeProfile);
 $('current-mode').textContent=active?'当前 · '+active.name:cfg.mode==='account'||cfg.mode==='thirdparty'?'当前 · '+modeNames[cfg.mode]:modeNames[cfg.mode];
 $('mode-pill').classList.toggle('attention',['setup','mixed','error'].includes(cfg.mode)&&!active);
 for(const mode of ['account','thirdparty']){const selected=cfg.mode===mode||(mode==='thirdparty'&&!!active);$(mode+'-badge').hidden=!selected;$(mode+'-card').classList.toggle('active',selected);}
 const showJob=job.status!=='idle';
 $('status-title').textContent=job.busy?(job.kind==='install'?'正在安装，请稍候':'正在处理，请稍候'):job.status==='error'?'操作需要处理':showJob?(job.result?.partial?'配置已保存，重启需要处理':'操作已完成'):state.configError?'无法读取配置':cfg.needsSetup&&!active?'连接信息尚未完整':'一键切换，自动接续';
 $('status-message').textContent=showJob?job.message:state.configError||state.warning||((cfg.needsSetup&&!active)?'打开 API 配置补齐信息，或添加一套自己的 API 和模型。':'切换后会自动重启 ChatGPT，请先保存正在进行的工作。');
 $('status-panel').classList.toggle('error',job.status==='error'||!!job.result?.partial||!!state.configError);
 $('backup-note').textContent=state.tray==='ready'?(state.settings.closeBehavior==='background'?'关闭后后台运行':'关闭后退出 · 托盘已就绪'):'备份最多 5 份 / 文件';
 $('backup-note').title=state.tray==='unavailable'?'当前环境未启用托盘；页面切换功能可用。':'每个文件保留最近 5 份备份，均在软件 backups 目录中。';
 $('detail-config').textContent=cfg.path||'';$('detail-env').textContent=cfg.envPath||'';$('detail-backups').textContent=state.backups.path;
 $('backup-counts').textContent=`config.toml：${state.backups.configCount} / 5 份　.env：${state.backups.envCount} / 5 份`;
 $('detail-rules').textContent='base_url = '+JSON.stringify(cfg.baseUrl||'未配置')+'\nenv_key = '+JSON.stringify(cfg.envKey||'未配置')+'\nmodel = '+JSON.stringify(cfg.model||'未配置');
 renderProfiles();renderSetupProfiles();renderEnvironment();pending(job.busy);
 if(job.kind==='install'&&job.status!=='idle'){
  $('install-log-box').hidden=false;$('install-log').textContent=[...job.logs,job.busy?'':job.message].filter(Boolean).join('\n');$('install-log').scrollTop=$('install-log').scrollHeight;
  $('install-progress-label').textContent=job.busy?'进行中':job.status==='error'?'需要处理':'已完成';
 }
 if(job.id!==lastJob&&!job.busy){lastJob=job.id;if(job.code==='desktop_missing'&&!$('install-dialog').open)toast('未检测到桌面应用，请打开「安装与代理」。');}
 if(initial){initial=false;setRadio('proxy-mode',state.preferences.mode);$('proxy-address').value=state.preferences.address;proxyChanged();if(cfg.needsSetup&&!state.profiles.length)openSetup('account');}
}
async function refresh(){if(closing)return;try{state=await request('/api/state');if(!closing){render();window.dispatchEvent(new CustomEvent('app-state',{detail:state}));}}catch(e){if(closing)return;$('status-title').textContent='工具连接已断开';$('status-message').textContent='请重新运行 ChatGPT Switch 启动器。';}}
function openSetup(mode='thirdparty',preserveSelection=false){
 if(!preserveSelection){selectedProfile=state?.activeProfile||state?.selectedProfile||null;setRadio('setup-mode',mode);}
 fieldError('setup-error','');setupSignature='';showDialog('setup-dialog');renderSetupProfiles();pending(busy);
}
async function triggerSwitch(mode){
 try{await request('/api/switch',{mode});await refresh();}catch(e){if(e.code==='setup_required')openSetup(mode);else toast(e.message);}
}
function openProfile(profile=null,fromSetup=false){
 profileFromSetup=fromSetup;
 const values=profile||{}, map={id:'id',name:'name',url:'baseUrl',model:'model',key:'envKey',review:'reviewModel',effort:'reasoningEffort',wire:'wireApi',auth:'requiresAuth'};
 Object.entries(map).forEach(([id,key])=>$('profile-'+id).value=values[key]||'');
 if(!profile){$('profile-key').value='MY_API_KEY';}
 $('profile-key').required=!profile;$('profile-secret').required=!profile;
 $('profile-save').textContent=fromSetup?'保存配置':'仅保存';$('profile-save-apply').hidden=fromSetup;
 $('profile-form').querySelector('details').open=false;
 $('profile-secret').value='';$('profile-heading').textContent=profile?'编辑 API 配置':'新增 API 配置';$('profile-delete').hidden=!profile;
 $('profile-secret-help').textContent=profile?.hasSecret?'本套配置已保存密钥；编辑时留空保留本套密钥。':profile?'这套旧配置未保存密钥；填写后可随配置一起切换。':'请填写服务地址、模型名和密钥。环境变量名已填入 MY_API_KEY，可自行修改。';
 fieldError('profile-error','');showDialog('profile-dialog');
}
async function applyProfile(id){try{await request('/api/profile/apply',{id});await refresh();}catch(e){toast(e.message);}}
function proxyChanged(){$('proxy-address').hidden=radio('proxy-mode')!=='custom';$('proxy-saved').hidden=true;}
dialogs.forEach(dialog=>{dialog.querySelector('.dismiss').addEventListener('click',()=>dialog.close());dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});});
$('account-action').addEventListener('click',()=>triggerSwitch('account'));
$('thirdparty-action').addEventListener('click',()=>triggerSwitch('thirdparty'));
$('setup-open').addEventListener('click',()=>openSetup());
$('setup-add').addEventListener('click',()=>openProfile(null,true));
$('profile-dialog').addEventListener('close',()=>{if(profileFromSetup){profileFromSetup=false;openSetup(undefined,true);}});
$('profile-add').addEventListener('click',()=>openProfile());
$('install-open').addEventListener('click',()=>{fieldError('install-error','');showDialog('install-dialog');});
$('details-open').addEventListener('click',()=>showDialog('details-dialog'));
$('refresh').addEventListener('click',async()=>{await refresh();toast('已重新读取配置。');});
$('detect-again').addEventListener('click',()=>act('/api/detect'));
$('open-config').addEventListener('click',()=>act('/api/open',{target:'config'}));
$('open-backups').addEventListener('click',()=>act('/api/open',{target:'backups'}));
$('get-winget').addEventListener('click',async()=>{const r=await act('/api/winget');if(r)toast(r.message);});
$('setup-form').addEventListener('submit',async event=>{
 event.preventDefault();fieldError('setup-error','');
 if(busy||setupRequest)return;
 if(!selectedProfile){fieldError('setup-error','请先新增并勾选一套 API 配置。');return;}
 setupRequest=true;pending(busy);
 try{await request('/api/profile/apply',{id:selectedProfile,mode:radio('setup-mode')});$('setup-dialog').close();await refresh();}catch(e){fieldError('setup-error',e.message);}finally{setupRequest=false;pending(busy);}
});
$('profile-form').addEventListener('submit',async event=>{
 event.preventDefault();fieldError('profile-error','');
 if(busy||profileRequest)return;
 const fromSetup=profileFromSetup,apply=event.submitter?.id==='profile-save-apply';
 const body={id:$('profile-id').value||null,name:$('profile-name').value,baseUrl:$('profile-url').value,model:$('profile-model').value,envKey:$('profile-key').value,secret:$('profile-secret').value,reviewModel:$('profile-review').value,reasoningEffort:$('profile-effort').value,wireApi:$('profile-wire').value,requiresAuth:$('profile-auth').value};
 profileRequest=true;pending(busy);
 try{const saved=await request('/api/profile/save',body);$('profile-secret').value='';await refresh();if(fromSetup)selectedProfile=saved.id;$('profile-dialog').close();if(apply)await applyProfile(saved.id);else toast(fromSetup?'配置已保存并勾选，点击「保存所选配置并生效」完成切换。':'API 配置已保存，可从页面或托盘应用。');}catch(e){fieldError('profile-error',e.message);}finally{profileRequest=false;pending(busy);}
});
$('profile-delete').addEventListener('click',async()=>{if(busy||profileRequest)return;profileRequest=true;pending(busy);try{await request('/api/profile/delete',{id:$('profile-id').value});await refresh();$('profile-dialog').close();toast('已从配置库删除；当前 ChatGPT 配置保持原样。');}catch(e){fieldError('profile-error',e.message);}finally{profileRequest=false;pending(busy);}});
document.querySelectorAll('input[name="proxy-mode"]').forEach(e=>e.addEventListener('change',proxyChanged));
['cli','desktop'].forEach(c=>$('install-'+c).addEventListener('change',updateInstallButton));
$('save-proxy').addEventListener('click',async()=>{fieldError('install-error','');$('proxy-saved').hidden=true;try{await request('/api/preferences',{mode:radio('proxy-mode'),address:$('proxy-address').value});$('proxy-saved').textContent='代理设置已保存';$('proxy-saved').hidden=false;toast('代理偏好已保存到软件目录。');}catch(e){fieldError('install-error',e.message);}});
$('install-start').addEventListener('click',async()=>{fieldError('install-error','');try{await request('/api/install',{selection:['cli','desktop'].filter(c=>$('install-'+c).checked&&!$('install-'+c).disabled),proxyMode:radio('proxy-mode'),proxyAddress:$('proxy-address').value,allowCompatible:$('allow-compatible').checked});await refresh();}catch(e){fieldError('install-error',e.message);}});
$('install-cancel').addEventListener('click',()=>act('/api/cancel'));
document.addEventListener('keydown',e=>{if(e.key==='F5'){e.preventDefault();refresh();}});
const pingTimer=setInterval(()=>{if(!closing)request('/api/ping',{}).catch(()=>{});},8000);
const refreshTimer=setInterval(()=>refresh(),1800);
renderWestClock();
setInterval(renderWestClock,1000);
refresh();
