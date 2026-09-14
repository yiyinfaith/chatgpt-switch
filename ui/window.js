'use strict';
// Keep window gestures out of the content layout and independent of pywebview's
// injected drag script. The existing authenticated local API controls the HWND.
let nativeWindow = false, windowFullscreen = false;
const titlebar = document.querySelector('.titlebar');
const windowEdges = [];
function updateWindowState(value){
 windowFullscreen=!!value.fullscreen;
 document.body.classList.toggle('window-fixed',value.maximized||windowFullscreen);
 $('window-maximize-icon').setAttribute('d',value.maximized?'M8 5V3h13v13h-2M3 8h13v13H3z':'M5 5h14v14H5z');
 const label=value.maximized?'还原':'最大化';
 $('window-maximize').title=label;$('window-maximize').setAttribute('aria-label',label);
 $('window-fullscreen').title=windowFullscreen?'退出全屏 (Esc / F11)':'全屏 (F11)';
 $('window-fullscreen').setAttribute('aria-label',windowFullscreen?'退出全屏':'全屏');
}
async function windowCommand(action,extra={}){
 if(!nativeWindow)return;
 try{const result=await request('/api/window',{action,...extra});updateWindowState(result);}
 catch(error){toast(error.message);}
}
function enableWindowControls(appState){
 nativeWindow=!!appState.nativeWindow;
 document.body.classList.toggle('native-window',nativeWindow);
 document.querySelectorAll('.native-control').forEach(button=>button.hidden=!nativeWindow);
}
window.addEventListener('app-state',event=>enableWindowControls(event.detail));
window.addEventListener('native-window-state',event=>updateWindowState(event.detail));
if(state)enableWindowControls(state);
titlebar.addEventListener('mousedown',event=>{
 if(!nativeWindow||event.button!==0||event.detail>1||event.target.closest('button,a,input,select'))return;
 event.preventDefault();windowCommand('drag');
});
titlebar.addEventListener('dblclick',event=>{
 if(event.button===0&&!event.target.closest('button,a,input,select'))windowCommand('maximize');
});
for(const edge of ['n','e','s','w','ne','nw','se','sw']){
 const handle=document.createElement('div');handle.className='window-resize edge-'+edge;
 handle.dataset.edge=edge;handle.setAttribute('aria-hidden','true');
 handle.addEventListener('mousedown',event=>{
  if(event.button!==0)return;
  event.preventDefault();event.stopPropagation();windowCommand('resize',{edge});
 });
 document.body.append(handle);windowEdges.push(handle);
}
$('window-minimize').addEventListener('click',()=>windowCommand('minimize'));
$('window-maximize').addEventListener('click',()=>windowCommand('maximize'));
$('window-fullscreen').addEventListener('click',()=>windowCommand('fullscreen'));
document.addEventListener('keydown',event=>{
 if(!nativeWindow)return;
 if(event.key==='F11'){event.preventDefault();windowCommand('fullscreen');}
 else if(event.key==='Escape'&&windowFullscreen){event.preventDefault();windowCommand('exit-fullscreen');}
});
$('quit').addEventListener('click',async()=>{
 if(closing)return;
 closing=true;$('quit').disabled=true;
 try{
  const result=await request('/api/close',{});
  if(result.hidden){
   closing=false;$('quit').disabled=false;
   if(nativeWindow)updateWindowState(result);else window.close();
  }else{
   clearInterval(pingTimer);clearInterval(refreshTimer);
   if(!nativeWindow)window.close();
  }
 }catch(error){
  closing=false;$('quit').disabled=false;toast(error.message);
 }
});
