'use strict';
let settingsRequest=false;
function openSettings(){
 setRadio('close-behavior',state?.settings.closeBehavior||'background');
 $('auto-start').checked=state?.settings.autoStart??true;
 $('auto-start-background').checked=state?.settings.autoStartBackground??true;
 fieldError('settings-error','');$('settings-saved').hidden=true;
 showDialog('settings-dialog');renderSettings();
}
function renderSettings(){
 if(!state)return;
 $('about-version').textContent='版本 '+state.version+' · 开源便携版';
 $('settings-api-summary').textContent=state.profiles.length?`已保存 ${state.profiles.length} 套配置 · 管理服务、模型和密钥`:'保存多套服务和模型，选择一套生效。';
 $('settings-save').disabled=settingsRequest;
 $('auto-start').disabled=settingsRequest||state.startup?.supported===false;
 $('auto-start-background').disabled=settingsRequest||state.startup?.supported===false||!$('auto-start').checked;
 $('startup-background-hint').textContent=$('auto-start').checked?'开启后只显示托盘图标，不弹出主面板；手动打开仍显示主面板。':'启用开机自启后生效，当前选择会保留。';
 $('startup-hint').textContent=state.startup?.supported===false?'当前系统暂不支持开机自启。':'首次使用默认开启，可随时关闭。';
 fieldError('startup-error',state.startup?.error||'');
 const ready=state.tray==='ready',background=state.settings.closeBehavior==='background';
 $('tray-setting-hint').textContent=ready?'托盘已就绪。右键图标可切换模式、打开设置和主面板，或退出程序。':state.tray==='starting'?'正在准备托盘图标…':'当前环境的托盘不可用；请使用直接退出，或重新启动程序后再试。';
 $('quit').title=background?'关闭主面板，在后台运行':'关闭窗口并退出';
 $('quit').setAttribute('aria-label',$('quit').title);
}
function navigatePanel(page){
 // Native tray navigation can arrive while a nested editor is open.
 profileFromSetup=false;
 if(page==='settings')openSettings();
 else if(page==='api')openSetup();
 else dialogs.forEach(d=>{if(d.open)d.close();});
}
$('settings-open').addEventListener('click',openSettings);
$('about-open').addEventListener('click',()=>showDialog('about-dialog'));
// Preserve unsaved settings when returning from About.
$('about-back').addEventListener('click',()=>showDialog('settings-dialog'));
$('about-project').addEventListener('click',()=>act('/api/project/open',{target:'project'}));
$('about-issues').addEventListener('click',()=>act('/api/project/open',{target:'issues'}));
$('setup-back').addEventListener('click',openSettings);
$('install-back').addEventListener('click',()=>showDialog('convenience-dialog'));
$('settings-form').addEventListener('submit',async event=>{
 event.preventDefault();if(settingsRequest)return;
 settingsRequest=true;fieldError('settings-error','');$('settings-saved').hidden=true;renderSettings();
 try{
  await request('/api/settings',{closeBehavior:radio('close-behavior'),autoStart:$('auto-start').checked,autoStartBackground:$('auto-start-background').checked});await refresh();
  $('settings-saved').textContent=state.settings.autoStart?'设置已保存，下次开机将'+(state.settings.autoStartBackground?'仅在托盘后台运行。':'显示主面板。'):'设置已保存，开机自启已关闭。';$('settings-saved').hidden=false;
 }catch(error){fieldError('settings-error',error.message);}
 finally{settingsRequest=false;renderSettings();}
});
document.querySelectorAll('input[name="close-behavior"]').forEach(input=>input.addEventListener('change',()=>{$('settings-saved').hidden=true;}));
['auto-start','auto-start-background'].forEach(id=>$(id).addEventListener('change',()=>{$('settings-saved').hidden=true;renderSettings();}));
window.addEventListener('app-state',renderSettings);
window.addEventListener('app-navigation',event=>navigatePanel(event.detail));
if(initialPage){
 const navigate=()=>{navigatePanel(initialPage);window.removeEventListener('app-state',navigate);};
 if(state)navigate();else window.addEventListener('app-state',navigate);
}
if(state)renderSettings();
