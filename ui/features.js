'use strict';
// Additive controls: use the existing session API, dialogs, job lock and theme.
let updateSignature='', featureRequest=false, shortcutJob=0, appShortcutJob=0;
const updateLabels={unchecked:'待检测',available:'可更新',current:'无需更新',missing:'未安装',error:'需要处理',unsupported:'需手动更新'};
const channelLabels={npm:'npm',standalone:'官方独立安装器',msstore:'Microsoft Store',winget:'WinGet',scoop:'Scoop',brew:'Homebrew',apt:'APT',rpm:'RPM',pacman:'Pacman',manual:'手动安装'};
function featureSettings(){return {proxyMode:radio('proxy-mode'),proxyAddress:$('proxy-address').value};}
function renderFeatures(){
 if(!state)return;
 const job=state.job,locked=job.busy||featureRequest,updates=state.updates;
 const signature=JSON.stringify(updates);
 for(const component of ['cli','desktop']){
  const entry=updates?.[component]||{status:'unchecked'},box=$('update-'+component),badge=$('update-'+component+'-state');
  if(signature!==updateSignature)box.checked=entry.status==='available';
  box.disabled=locked||entry.status!=='available';
  badge.textContent=updateLabels[entry.status]||'待检测';badge.className='install-state '+(entry.status==='current'?'present':entry.status==='available'?'absent':'');
  const versions=entry.current?entry.current+(entry.latest&&entry.latest!==entry.current?' → '+entry.latest:''):'';
  $('update-'+component+'-detail').textContent=[channelLabels[entry.channel],versions,entry.message||'尚未检测'].filter(Boolean).join(' · ');
 }
 updateSignature=signature;
 $('updates-check').disabled=locked||!state.environment;
 $('updates-apply').disabled=locked||!['cli','desktop'].some(c=>$('update-'+c).checked&&!$('update-'+c).disabled);
 $('updates-cancel').hidden=!(job.busy&&['update','update-check'].includes(job.kind));
 $('updates-cancel').disabled=featureRequest;
 const windows=state.platform.system==='Windows';
 $('shortcut-create').disabled=locked||!windows;
 $('shortcut-hint').textContent=windows?'自动识别 Windows 应用注册信息，支持重定向或 OneDrive 桌面。':'一键创建桌面图标目前适用于 Windows。';
 if(job.kind==='shortcut'){
  $('shortcut-create').textContent=job.busy?'创建中…':'创建';
  if(!job.busy&&job.id!==shortcutJob){shortcutJob=job.id;fieldError('shortcut-error',job.status==='error'?job.message:'');$('shortcut-result').hidden=job.status!=='success';$('shortcut-result').textContent=[job.message,job.result?.path].filter(Boolean).join('\n');}
 }
 const appShortcutWindows=state.platform.system==='Windows';
 $('app-shortcut-create').disabled=locked||!appShortcutWindows;
 $('app-shortcut-hint').textContent=appShortcutWindows?'创建后可从桌面直接启动 ChatGPT Switch。':'一键创建桌面图标目前适用于 Windows。';
 if(job.kind==='app-shortcut'){
  $('app-shortcut-create').textContent=job.busy?'创建中…':'创建';
  if(!job.busy&&job.id!==appShortcutJob){appShortcutJob=job.id;fieldError('app-shortcut-error',job.status==='error'?job.message:'');$('app-shortcut-result').hidden=job.status!=='success';$('app-shortcut-result').textContent=[job.message,job.result?.path].filter(Boolean).join('\n');}
 }
 if(['update','update-check'].includes(job.kind)){
  $('updates-log-box').hidden=false;
  $('updates-log').textContent=[...job.logs,job.busy?'':job.message].filter(Boolean).join('\n');
  $('updates-progress').textContent=job.busy?'进行中':job.status==='error'?'需要处理':'已完成';
  if(!job.busy&&job.status==='error'&&!featureRequest)fieldError('updates-error',job.message);
 }
}
async function featureAction(path,body,errorId){
 if(featureRequest||state?.job.busy)return;
 featureRequest=true;fieldError(errorId,'');renderFeatures();
 try{await request(path,body);await refresh();}catch(error){fieldError(errorId,error.message);}
 finally{featureRequest=false;renderFeatures();}
}
async function openConfigFile(){
 const buttons=[$('open-config-footer'),$('open-config-file')];
 if(buttons.some(button=>button.disabled))return;
 buttons.forEach(button=>button.disabled=true);
 fieldError('config-file-error','');$('config-file-result').hidden=true;
 try{
  const result=await request('/api/open',{target:'config-file'});
  const message=result.message||'已请求系统默认应用打开 config.toml。';
  if($('convenience-dialog').open){$('config-file-result').textContent=message;$('config-file-result').hidden=false;}
  else toast(message);
 }catch(error){
  if($('convenience-dialog').open)fieldError('config-file-error',error.message);
  else toast(error.message);
 }finally{buttons.forEach(button=>button.disabled=false);}
}
$('convenience-open').addEventListener('click',()=>{fieldError('config-file-error','');$('config-file-result').hidden=true;showDialog('convenience-dialog');});
$('open-config-footer').addEventListener('click',openConfigFile);
$('open-config-file').addEventListener('click',openConfigFile);
$('shortcut-create').addEventListener('click',()=>{$('shortcut-result').hidden=true;featureAction('/api/shortcut',{},'shortcut-error');});
$('app-shortcut-create').addEventListener('click',()=>{$('app-shortcut-result').hidden=true;featureAction('/api/app-shortcut',{},'app-shortcut-error');});
$('updates-check').addEventListener('click',()=>featureAction('/api/updates/check',featureSettings(),'updates-error'));
$('updates-apply').addEventListener('click',()=>featureAction('/api/updates/apply',{...featureSettings(),selection:['cli','desktop'].filter(c=>$('update-'+c).checked&&!$('update-'+c).disabled)},'updates-error'));
// Cancelling a running job is deliberately allowed while the job lock is held.
$('updates-cancel').addEventListener('click',()=>act('/api/cancel'));
['cli','desktop'].forEach(c=>$('update-'+c).addEventListener('change',renderFeatures));
window.addEventListener('app-state',renderFeatures);
if(state)renderFeatures();
