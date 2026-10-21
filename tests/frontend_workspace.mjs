// Run with Chainlit started: node tests/frontend_workspace.mjs http://127.0.0.1:8011
// Uses an isolated Chrome profile and explicit DOM fixtures; never calls destructive backend actions.
import fs from 'node:fs/promises';
import path from 'node:path';
import { spawn } from 'node:child_process';

const root = process.cwd();
const appURL = process.argv[2] || 'http://127.0.0.1:8011';
const profile = path.join(root, 'release_checks', `chrome-ui-preview-${Date.now()}`);
await fs.mkdir(profile, { recursive: true });
const chrome = spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'
], { windowsHide: true, stdio: 'ignore' });
let socket;
try {
  let port;
  for (let attempt = 0; attempt < 150; attempt++) {
    try { port = (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split('\n')[0]; if (port) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!port) throw new Error('Isolated headless renderer did not start');
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let nextId = 0;
  const requests = new Map();
  socket.addEventListener('message', event => {
    const result = JSON.parse(event.data);
    if (requests.has(result.id)) {
      const { resolve, reject, timer } = requests.get(result.id);
      clearTimeout(timer); requests.delete(result.id);
      result.error ? reject(new Error(JSON.stringify(result.error))) : resolve(result.result);
    }
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { requests.delete(id); reject(new Error(`Timed out: ${method}`)); }, 30000);
    requests.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
﻿  const assert = (condition, message) => { if(!condition) throw new Error(message); };
  const pause = ms => new Promise(resolve=>setTimeout(resolve,ms));
  const check = async (expression, label) => assert(await evaluate(expression),label);
  const capture = async name => {
    await pause(400);
    const shot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await fs.writeFile(path.join(root,'release_checks',`workspace-${name}.png`),Buffer.from(shot.data,'base64'));
  };
  await call('Page.enable');
  await call('Network.enable');
  await call('Network.setBlockedURLs',{urls:['https://*']});
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:appURL});
  for(let i=0;i<150;i++) {
    if(await evaluate("Boolean(document.querySelector('#library-function-bar'))")) break;
    await pause(300);
  }
  await check("Boolean(document.querySelector('#library-chat-history'))",'History sidebar mounted');
  await pause(1200);
  await capture('desktop-empty');
  await check("document.querySelectorAll('.function-action').length===4",'Four toolbar functions');
  await check("getComputedStyle(document.querySelector('.library-main')).display==='grid'",'Workspace grid wins native utility cascade');
  await check("document.documentElement.scrollWidth<=innerWidth",'Desktop no overflow');
  await evaluate("document.querySelector('.history-drawer-close').click()");
  await check("getComputedStyle(document.querySelector('#library-chat-history')).display==='none' && document.querySelector('.history-toggle').getAttribute('aria-expanded')==='false'",'Desktop history closes');
  await capture('desktop-history-collapsed');
  await evaluate("document.querySelector('.history-toggle').click()");
  await check("getComputedStyle(document.querySelector('#library-chat-history')).display==='flex' && !document.querySelector('.library-main').inert",'Desktop history reopens without blocking live chat');
  if (process.argv.includes('--uploads')) {
    await check("document.body.textContent.includes('Kiểm thử tải tài liệu, không dùng kho dữ liệu thật.')",'Upload QA requires the isolated fixture, never production ingestion');
    const until = async (expression, label) => {
      for (let i=0;i<100;i++) { if (await evaluate(expression)) return; await pause(100); }
      console.log(await evaluate("({rootInert:document.querySelector('#root').inert,active:document.activeElement.outerHTML,dialogs:[...document.querySelectorAll('[role=dialog]')].map(e=>[e.id,e.getAttribute('data-state')]),uploadHidden:document.querySelector('.document-upload-backdrop')?.hidden})"));
      throw new Error(label);
    };
    const openUpload = async () => {
      await evaluate("document.querySelector('.function-action').click()");
      await evaluate("document.querySelector('.document-upload-browse').click()");
      await until("!!document.querySelector('#ask-button-input') && !!document.querySelector('.document-upload-dropzone button')",'Native file picker ready');
    };
    const key = async (name, code, modifiers=0) => {
      await call('Input.dispatchKeyEvent',{type:'keyDown',key:name,code:name,windowsVirtualKeyCode:code,modifiers,...(name==='Enter'?{text:'\r'}:{})});
      await call('Input.dispatchKeyEvent',{type:'keyUp',key:name,code:name,windowsVirtualKeyCode:code,modifiers});
    };
    await check("getComputedStyle(document.querySelector('#upload-button')).display==='none'",'Composer paperclip hidden');
    await evaluate("document.querySelector('.function-action').click()");
    await check("document.querySelector('#root').inert && document.querySelector('.document-upload-note').textContent.includes('font chữ')",'Upload modal protects focus and explains font checks');
    await capture('upload-desktop');
    await key('Tab',9,8);
    await check("document.activeElement.classList.contains('document-upload-cancel')",'Upload Shift-Tab wraps focus');
    await evaluate("document.querySelector('.document-upload-settings').click()");
    await until("!!document.querySelector('#chat-settings')",'Native document settings open');
    await check("document.querySelector('.document-upload-backdrop').hidden && !document.querySelector('#root').inert",'Settings remain usable from upload');
    await evaluate("document.querySelector('#chat-settings #confirm').focus()");
    await key('Enter',13);
    await until("!document.querySelector('.document-upload-backdrop').hidden && document.querySelector('#root').inert",'Upload resumes after native settings close');
    await evaluate("document.querySelector('.document-upload-browse').click()");
    await until("!!document.querySelector('#ask-button-input') && !!document.querySelector('.document-upload-dropzone button')",'Native picker starts after settings');
    await check("getComputedStyle(document.querySelector('#ask-button-input').closest('[data-step-type]')).display==='none'",'Native upload prompt stays out of chat');
    await capture('upload-native-picker');
    await evaluate("(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['invalid'],'invalid.exe',{type:'application/octet-stream'}));document.querySelector('.document-upload-dropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));})()");
    await until("!document.querySelector('.document-upload-errors').hidden",'Native file validation error stays in upload dialog');
    await check("document.querySelector('.document-upload-errors').textContent.includes('Định dạng chưa được hỗ trợ')",'Native validation gets readable Vietnamese copy');
    await capture('upload-validation-error');
    await key('Escape',27);
    await until("!document.querySelector('.document-upload-dialog') && !!document.querySelector('.document-upload-toast')",'Cancel acknowledgement closes modal and shows toast');
    await check("!document.querySelector('#root').inert && document.activeElement.dataset.nativeAction==='Thêm tài liệu'",'Cancel releases chat and restores toolbar focus');
    await capture('upload-cancel-toast');
    await pause(4200);
    await check("!document.querySelector('.document-upload-toast')",'Cancel toast disappears');
    await evaluate("document.querySelector('#chat-input').focus()");
    await call('Input.insertText',{text:'Kiểm thử trò chuyện sau hủy'});
    await key('Enter',13);
    await until("[...document.querySelectorAll('.message-content')].some(e=>e.textContent.includes('Có thể tiếp tục trò chuyện sau khi hủy.'))",'Chat works after actual native cancel');
    const uploadFile = async (name, drop=false) => {
      await openUpload();
      const filename=path.join(root,'release_checks',name);
      await fs.writeFile(filename,'Du lieu kiem thu frontend, khong nap vao Chroma.');
      if(drop) {
        await evaluate(`(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['UI fixture'],'${name}',{type:'text/plain'}));document.querySelector('.document-upload-dropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));})()`);
      } else {
        await evaluate("window.qaPickerClicks=0;document.querySelector('#ask-button-input').addEventListener('click',()=>window.qaPickerClicks++)");
        await evaluate("document.querySelector('.document-upload-dropzone button').click()");
        await check("window.qaPickerClicks===1",'Browse forwards to the real file input');
        const dom=await call('DOM.getDocument');
        const input=await call('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'#ask-button-input'});
        await call('DOM.setFileInputFiles',{nodeId:input.nodeId,files:[filename]});
      }
      await until("document.querySelector('.document-upload-content')?.textContent.includes('Đang nạp')",'Native upload progress stays in modal');
      await check("document.querySelector('.document-upload-close').disabled && document.querySelector('.document-upload-cancel').disabled",'Processing cannot falsely cancel committed work');
      await evaluate("document.querySelector('.document-upload-backdrop').click()");
      await key('Escape',27);
      const protectedState=await evaluate("({text:document.querySelector('.document-upload-cancel')?.textContent,inert:document.querySelector('#root').inert,content:document.querySelector('.document-upload-content')?.textContent})");
      assert(protectedState.inert && ['Đang xử lý…','Đóng'].includes(protectedState.text),`Backdrop and Escape cannot arm cancellation during processing: ${JSON.stringify(protectedState)}`);
      await capture(drop?'upload-mobile-progress':'upload-desktop-progress');
      await until("document.querySelector('.document-upload-cancel')?.textContent==='Đóng'",'Native processing result appears');
      await check("!document.querySelector('.document-upload-close').disabled && !document.querySelector('.document-upload-cancel').disabled",'Result stays dismissible after protected backdrop activation');
    };
    await uploadFile('frontend-upload.txt');
    await check("document.querySelector('.document-upload-content').textContent.includes('Đã nạp')",'Success remains inside upload dialog');
    await capture('upload-success');
    await evaluate("document.querySelector('.document-upload-cancel').click()");
    await pause(600);
    await check("!JSON.parse(localStorage.getItem('scripture-chat-history-v1')).some(e=>e.messages.some(m=>/Chọn loại tài liệu|Đã hủy thêm tài liệu|Đang nạp|Đã nạp/.test(m.text)))",'Upload management does not enter chat history');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await openUpload();
    await check("document.querySelector('.document-upload-dialog').getBoundingClientRect().right<=innerWidth && document.documentElement.scrollWidth<=innerWidth",'Upload fits mobile');
    await capture('upload-mobile');
    await evaluate("document.documentElement.classList.add('dark')");
    await capture('upload-mobile-dark');
    await evaluate("document.querySelector('.document-upload-cancel').click()");
    await until("!document.querySelector('.document-upload-dialog')",'Mobile cancellation closes dialog');
    await uploadFile('frontend-upload-error.txt',true);
    await check("document.querySelector('.document-upload-content').textContent.includes('Không thể nạp')",'Processing error remains inside mobile dialog');
    await capture('upload-mobile-error');
    await evaluate("document.querySelector('.document-upload-close').click()");
    await check("!document.querySelector('#root').inert",'Error dismissal releases chat');
    await evaluate("document.querySelector('.function-action').click();(()=>{const transfer=new DataTransfer();transfer.items.add(new File(['UI fixture'],'frontend-preparation-drop.txt',{type:'text/plain'}));document.querySelector('.document-upload-dropzone').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));})()");
    await until("document.querySelector('.document-upload-cancel')?.textContent==='Đóng'",'Dropping into preparation starts native upload and completes');
    await evaluate("document.querySelector('.document-upload-close').click()");
    console.log('PASS: native upload browse/drop, settings, progress/success/error, desktop/mobile dialog, cancellation acknowledgement/toast, focus and chat/history isolation. No production ingestion.');
  } else {
  const inject = async id => evaluate(`(()=>{
    document.querySelector('#workspace-qa-fixture')?.remove();
    const fixture=document.createElement('div');fixture.id='workspace-qa-fixture';
    const parent=document.querySelector('[data-step-type]')?.parentElement || document.querySelector('[data-panel]');
    for(const [role,text] of [['user','Câu hỏi kiểm tra lịch sử ${id}'],['assistant','Bản thử frontend, không phải lời giải Kinh Thánh.\\n\\nĐoạn văn này dùng để kiểm tra việc lưu toàn bộ câu trả lời và hiển thị an toàn: <img src=x onerror=alert(1)>']]) {
      const step=document.createElement('div');step.dataset.stepType=role+'_message';
      const content=document.createElement('div');content.className='message-content';content.textContent=text;
      const wrapper=document.createElement('div');wrapper.id='step-qa-${id}-'+role;wrapper.append(content);step.append(wrapper);fixture.append(step);
      if(role==='assistant') for(const label of ['Thêm tài liệu','Kho tài liệu','Đánh giá RAG','Xóa toàn bộ']) {
        const control=document.createElement('button');control.textContent=label;control.dataset.qaAction=label;
        control.addEventListener('click',()=>{window.qaActions??={};window.qaActions[label]=(window.qaActions[label]||0)+1;});step.append(control);
      }
    }
    parent.append(fixture);
  })()`);
  await inject('one');
  await pause(900);
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1')).length===1",'Conversation auto-saved');
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1'))[0].messages.length===2",'Both message roles saved');
  await evaluate("document.querySelector('.history-toggle').click();document.querySelector('.history-toggle').click()");
  await check("document.querySelectorAll('.history-open').length===1",'Collapsing history preserves saved conversations');
  await check("[...document.querySelectorAll('[data-qa-action]')].every(b=>getComputedStyle(b).display==='none')",'Original actions hidden');
  await evaluate("document.querySelector('.function-action').click();document.querySelector('.document-upload-browse').click()");
  await evaluate(`(()=>{
    const reply=document.createElement('div');reply.dataset.stepType='assistant_message';
    const content=document.createElement('div');content.className='message-content';content.textContent='Đã hủy thêm tài liệu. Bạn có thể tiếp tục trò chuyện.';
    reply.append(content);document.querySelector('#workspace-qa-fixture').append(reply);
  })()`);
  await pause(100);
  await evaluate("[...document.querySelectorAll('.function-action')].slice(1).forEach(b=>b.click())");
  await check("Object.values(window.qaActions).length===4 && Object.values(window.qaActions).every(n=>n===1)",'All functions forward once to native handlers');
  await evaluate(`(()=>{
    const reply=document.createElement('div');reply.dataset.stepType='assistant_message';
    const content=document.createElement('div');content.className='message-content';content.textContent='Kho tài liệu đang trống.';
    reply.append(content);document.querySelector('#workspace-qa-fixture').append(reply);
  })()`);
  await pause(100);
  await evaluate("document.querySelector('.document-library-close')?.click()");
  await evaluate(`(()=>{
    const home=document.createElement('div');home.dataset.stepType='assistant_message';home.id='qa-library-home';
    const content=document.createElement('div');content.className='message-content';
    content.innerHTML='<p>Chào bạn. Hãy đặt câu hỏi về tài liệu hoặc dùng các nút bên dưới để quản lý tài liệu PDF, Word và TXT.</p><p>Kho hiện có <strong>41849</strong> vector.</p>';
    home.append(content);document.querySelector('#workspace-qa-fixture').append(home);
    const source=[...document.querySelectorAll('[data-qa-action]')].find(b=>b.dataset.qaAction==='Kho tài liệu');
    source.addEventListener('click',()=>setTimeout(()=>{
      const step=document.createElement('div');step.dataset.stepType='assistant_message';step.dataset.libraryFixture='true';
      const content=document.createElement('div');content.className='message-content';
      content.innerHTML=window.qaLibraryReply==='empty'?'Kho tài liệu đang trống.':window.qaLibraryReply==='busy'?'Kho dữ liệu đang bận xử lý. Vui lòng thử lại sau.':'<p>Các tài liệu trong kho:</p><p><strong>Bản văn Kinh Thánh</strong></p><ul><li><code>kiem-thu-frontend.pdf</code></li></ul><p><strong>Giáo lý</strong></p><ul><li><code>kiem-thu-frontend-giao-ly.docx</code></li></ul>';
      step.append(content);
      if(!window.qaLibraryReply)for(const label of ['Tóm tắt: kiem-thu-frontend.pdf','Xóa: kiem-thu-frontend.pdf']){
        const action=document.createElement('button');action.textContent=label;action.dataset.libraryFixtureAction=label;
        action.addEventListener('click',()=>{window.qaDocumentAction=label;});step.append(action);
      }
      document.querySelector('#workspace-qa-fixture').append(step);
    },700));
  })()`);
  await pause(100);
  const openLibrary = () => evaluate("[...document.querySelectorAll('.function-action')].find(b=>b.dataset.nativeAction==='Kho tài liệu').click()");
  await openLibrary();
  await check("document.querySelector('#root').inert && document.querySelector('.document-library-status').textContent.includes('Đang tải')",'Library opens immediately with loading and protected focus');
  await capture('library-loading');
  await pause(500);
  await check("document.querySelector('.document-library-body').textContent.includes('41849 vector') && document.querySelectorAll('.document-library-action').length===2",'Library contains vector count, document groups and actions');
  await check("!document.querySelector('#root').innerText.includes('41849') && document.querySelector('.library-home-copy').textContent.includes('Thanh công cụ')",'Welcome replaces old introduction and hides vector count from chat');
  await check("[...document.querySelectorAll('[data-library-fixture]')].every(s=>getComputedStyle(s).display==='none')",'Catalog results are hidden from chat');
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1'))[0].messages.length===2",'Catalog management does not enter chat history');
  await capture('library-desktop');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8});
  await check("document.activeElement===document.querySelectorAll('.document-library-action')[1]",'Library Shift-Tab wraps focus');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await capture('library-mobile');
  await check("document.querySelector('.document-library-dialog').getBoundingClientRect().right<=innerWidth",'Library fits mobile');
  await evaluate("document.documentElement.classList.add('dark')");
  await capture('library-mobile-dark');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await check("!document.querySelector('.document-library-dialog') && !document.querySelector('#root').inert && document.activeElement.dataset.nativeAction==='Kho tài liệu'",'Library Escape restores toolbar focus');
  await openLibrary();await pause(900);
  await evaluate("document.querySelector('.document-library-action').click()");
  await check("window.qaDocumentAction.startsWith('Tóm tắt:') && !document.querySelector('#root').inert",'Summary forwards to original native callback outside modal');
  await openLibrary();await pause(900);
  await evaluate("[...document.querySelectorAll('.document-library-action')].find(b=>b.textContent.startsWith('Xóa:')).click()");
  await check("window.qaDocumentAction.startsWith('Xóa:') && !document.querySelector('.document-library-dialog')",'Delete forwards without bypassing original confirmation callback');
  await evaluate("window.qaLibraryReply='empty';document.querySelector('#qa-library-home .message-content').innerHTML='<p>Chào bạn. Hãy đặt câu hỏi về tài liệu hoặc dùng các nút bên dưới để quản lý tài liệu PDF, Word và TXT.</p><p>Kho hiện có <strong>0</strong> vector.</p>'");
  await pause(100);await openLibrary();await pause(900);
  await check("document.querySelector('.document-library-body').textContent.includes('0 vector') && document.querySelector('.document-library-body').textContent.includes('Kho tài liệu đang trống.')",'Empty library preserves zero vector count');
  await capture('library-empty');
  await evaluate("document.querySelector('.document-library-close').click();window.qaLibraryReply='busy'");
  await openLibrary();await pause(900);
  await check("document.querySelector('.document-library-body').textContent.includes('Kho dữ liệu đang bận')",'Backend busy response stays in library dialog');
  await capture('library-busy');
  await evaluate("document.querySelector('.document-library-close').click();document.documentElement.classList.remove('dark');window.qaLibraryReply=undefined");
  await openLibrary();
  await evaluate("document.querySelector('.document-library-close').click()");
  await pause(900);
  await check("!document.querySelector('.document-library-dialog') && !document.querySelector('#root').inert",'Late library response never reopens a dismissed modal');
  for (const reply of ['empty','busy']) {
    await evaluate(`window.qaLibraryReply='${reply}'`);
    await openLibrary();
    await evaluate("document.querySelector('.document-library-close').click()");
    await pause(900);
    await check("getComputedStyle([...document.querySelectorAll('[data-library-fixture]')].at(-1)).display==='none' && !document.querySelector('.document-library-dialog')",`Late ${reply} response is isolated without reopening`);
    await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1'))[0].messages.length===2",`Late ${reply} response is not saved in chat history`);
  }
  await evaluate(`(()=>{
    const standalone=document.createElement('div');standalone.dataset.stepType='assistant_message';standalone.id='qa-standalone-busy';
    const content=document.createElement('div');content.className='message-content';content.textContent='Kho dữ liệu đang bận xử lý. Vui lòng thử lại sau.';
    standalone.append(content);document.querySelector('#workspace-qa-fixture').append(standalone);
  })()`);
  await pause(100);
  await check("!document.querySelector('#qa-standalone-busy').classList.contains('library-documents-result')",'Standalone busy status is not hidden as a library response');
  await evaluate("document.querySelector('#qa-standalone-busy').remove();window.qaLibraryReply=undefined");
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await evaluate("document.querySelector('[data-qa-action]').disabled=true");
  await pause(100);
  await check("document.querySelector('.function-action').disabled",'Native disabled state mirrored');
  await evaluate("document.querySelector('[data-qa-action]').disabled=false");
  await pause(100);
  await capture('desktop-history');
  await evaluate("document.querySelector('.history-open').click()");
  await check("!document.querySelector('.library-history-reader').hidden && document.querySelector('.library-main > [data-panel-group]').inert",'Read-only archive opens and native chat is inert');
  await check("document.querySelector('.library-history-reader').textContent.includes('<img src=x onerror=alert(1)>') && !document.querySelector('.library-history-reader img')",'Archive content rendered as safe text');
  await check("document.querySelector('.library-history-reader').getBoundingClientRect().top===document.querySelector('.library-function-bar').getBoundingClientRect().bottom",'Reader begins directly below function bar');
  await capture('desktop-reader');
  await evaluate("document.querySelector('.workspace-back').click()");
  await check("!document.querySelector('.library-main > [data-panel-group]').inert && document.querySelector('.library-history-reader').hidden",'Return to current live conversation');
  await check("document.activeElement.id==='chat-input'",'Back restores focus to native composer');
  await call('Page.reload');
  await pause(1800);
  await check("document.querySelectorAll('.history-open').length===1",'History persists after reload');
  await inject('two');
  await pause(800);
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1')).length===2",'Second conversation preserves first');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await check("[...document.querySelectorAll('.function-action')].every(b=>b.getBoundingClientRect().bottom<=document.querySelector('.library-function-bar').getBoundingClientRect().bottom)",'All mobile toolbar actions fit in bar');
  await capture('mobile');
  await check("document.documentElement.scrollWidth<=innerWidth",'Mobile no horizontal overflow');
  await evaluate("document.querySelector('.history-toggle').click()");
  await check("document.querySelector('.library-main').inert && document.querySelector('#library-chat-history').getAttribute('aria-modal')==='true'",'Mobile history drawer protects focus');
  await capture('mobile-drawer');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9,modifiers:8});
  await check("document.querySelector('#library-chat-history').contains(document.activeElement)",'Drawer retains keyboard focus');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await check("!document.querySelector('.library-main').inert && document.activeElement.classList.contains('history-toggle')",'Escape restores focus');
  await evaluate("document.querySelector('.history-toggle').click();document.querySelector('.history-open').click()");
  await check("!document.querySelector('#library-chat-history').classList.contains('is-open') && !document.querySelector('.library-history-reader').hidden",'Choosing history closes drawer and opens reader');
  await check("Math.abs(document.querySelector('#header').getBoundingClientRect().top)<1",'History focus preserves mobile header');
  await check("document.querySelector('.library-history-reader').getBoundingClientRect().top===document.querySelector('.library-function-bar').getBoundingClientRect().bottom",'Mobile reader starts after toolbar');
  await capture('mobile-reader');
  await evaluate("document.querySelector('.workspace-back').click();document.documentElement.classList.add('dark')");
  await check("document.activeElement.id==='chat-input'",'Mobile Back restores composer focus');
  await capture('mobile-dark');
  await call('Emulation.setDeviceMetricsOverride',{width:320,height:740,deviceScaleFactor:1,mobile:true});
  await capture('small');
  await check("document.documentElement.scrollWidth<=innerWidth",'320px no horizontal overflow');
  await evaluate("document.querySelector('.history-toggle').click();document.querySelectorAll('.history-remove').forEach(b=>b.click())");
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1')).length===0",'Delete removes only local archives');
  await evaluate("document.querySelector('#workspace-qa-fixture .message-content').append(document.createTextNode(' thêm nội dung'))");
  await pause(800);
  await check("JSON.parse(localStorage.getItem('scripture-chat-history-v1')).length===0",'Deleted current archive stays deleted');
  await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
  await evaluate("window.originalStorageSetItem=Storage.prototype.setItem; Storage.prototype.setItem=function(){throw new DOMException('Quota exceeded','QuotaExceededError')}");
  await inject('three');
  await pause(800);
  await check("document.querySelector('.history-storage-note').textContent.includes('Không thể lưu lịch sử')",'Quota error is visible');
  await check("!document.querySelector('.workspace-storage-error').hidden && document.querySelector('.workspace-storage-error').getClientRects().length>0",'Storage failure visible outside collapsed history');
  await check("document.querySelector('.workspace-storage-error').getBoundingClientRect().right<=innerWidth && document.querySelector('.workspace-storage-error').getBoundingClientRect().bottom<=document.querySelector('.library-function-bar').getBoundingClientRect().bottom",'Storage warning fits narrow toolbar');
  await capture('storage-error');
  await evaluate("Storage.prototype.setItem=window.originalStorageSetItem;localStorage.setItem('scripture-chat-history-v1','[{\"id\":\"bad\",\"title\":\"bad date\",\"updatedAt\":1e99,\"messages\":[]}]')");
  await call('Page.reload');
  await pause(1800);
  await check("document.querySelector('.history-storage-note').textContent.includes('Không thể đọc lịch sử')",'Corrupt history handled without crashing');
  await check("localStorage.getItem('scripture-chat-history-v1').includes('bad date')",'Corrupt original data preserved');
  await evaluate("localStorage.removeItem('scripture-chat-history-v1')");
  if (process.argv.includes('--sources')) {
    await call('Page.reload');
    await pause(1800);
    await check("document.body.innerText.includes('Môi trường kiểm thử giao diện nguồn')",'Native source fixture is isolated from production RAG');
    const until = async (expression, label) => {
      for(let i=0;i<100;i++) { if(await evaluate(expression)) return; await pause(100); }
      throw new Error(label);
    };
    const send = async turn => {
      await evaluate("document.querySelector('#chat-input').focus({preventScroll:true})");
      await call('Input.insertText',{text:`Kiểm thử giao diện nguồn ${turn}`});
      await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
      await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
      await until(`document.body.innerText.includes('Câu trả lời kiểm thử ${turn}.') && document.querySelectorAll('.element-link').length>=${turn*2}`,'Native fixture answer and sources arrive');
      await pause(500);
      await check("!document.querySelector('#side-view-title') && document.body.style.pointerEvents!=='none'",'New sources never open a side panel or lock mobile input');
    };
    const clickCitation = async name => {
      const position=await evaluate(`(()=>{
        const link=[...document.querySelectorAll('.element-link')].filter(a=>a.textContent.trim()==='${name}').at(-1);
        link.scrollIntoView({block:'center'});const rect=link.getBoundingClientRect();return{x:rect.x+rect.width/2,y:rect.y+rect.height/2};
      })()`);
      await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...position});
      await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...position});
      await until(`document.querySelector('#side-view-title')?.textContent.trim()==='${name}'`,'Clicked native source opens');
      await pause(400);
      await check("!document.querySelector('#side-view-content').closest('.library-source-suppressed')",'Explicitly requested source is visible');
    };
    await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await send(1);
    await capture('sources-default-closed');
    await clickCitation('Nguồn 1');
    await until("document.querySelector('#side-view-content')?.innerText.includes('Nguồn kiểm thử thứ nhất, lượt 1.')",'Native source body loaded');
    await check("!document.querySelector('#side-view-content').innerText.includes('thứ hai')",'Click opens only the selected source');
    await capture('source-desktop-open');
    await send(2);
    await capture('sources-second-answer-closed');
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
    await pause(300);
    await clickCitation('Nguồn 2');
    await until("document.querySelector('#side-view-content')?.innerText.includes('Nguồn kiểm thử thứ hai, lượt 2.')",'Native mobile source body loaded');
    await capture('source-mobile-open');
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    await pause(500);
    await check("!document.querySelector('#side-view-title')",'Native mobile source closes normally');
    await send(3);
    await capture('sources-mobile-default-closed');
    await evaluate("[...document.querySelectorAll('.element-link')].filter(a=>a.textContent.trim()==='Nguồn 1').at(-1).focus()");
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
    await until("document.querySelector('#side-view-title')?.textContent.trim()==='Nguồn 1'",'Keyboard activation opens native source');
    await pause(400);
    await check("!document.querySelector('#side-view-content').closest('.library-source-suppressed')",'Keyboard-selected source remains visible');
    await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
    console.log('PASS: native Chainlit source auto-open suppressed on desktop/mobile, explicit mouse/keyboard opens selected source, new answers close old source intent.');
  }
  console.log('PASS: library modal/counts/intro/loading/empty/busy/late-response/summary/delete; toolbar forwarding/disabled; history save/reload/safe rendering/deletion; responsive focus; quota and corrupt data. Layout screenshots contain explicit QA fixtures only.');
  }
} finally {
  socket?.close();
  chrome.kill();
}
