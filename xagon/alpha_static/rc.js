'use strict';
async function loadRCStatus(){const result=await api('/api/diagnostics');const x=result.saved_xagon;const number=v=>v===null||v===undefined?'?':v;$('rc-readiness').textContent=`Сохранённый вебинар XAGON (${{partial:'частично',complete:'по отчётам завершён',unknown:'неизвестно'}[x.status]||'неизвестно'}): транскрипции ${number(x.transcribed)}/${number(x.total_segments)}, Vision ${number(x.vision_done)}/${number(x.total_segments)}; осталось Vision: ${number(x.remaining_vision)}, синтез: ${x.synthesis_pending?'не завершён':'см. диагностику'}. Это сохранённый снимок отчётов; облачная обработка не запускается.`;$('rc-diagnostic-result').replaceChildren(el('p','',`RC, не финальная1.0. SQLite: ${result.database.integrity||'не открыта'}, cloud: ${result.cloud_blocked?'заблокирован':'ОШИБКА'}.`),jsonSection('Полная самодиагностика без секретов',[result]));if(result.healthy===false)$('rc-diagnostic-result').prepend(el('p','error','Найдены ошибки или неполная проверка. Смотрите подробности и warnings.'));return result;}
$('rc-diagnose').addEventListener('click',()=>busy($('rc-diagnose'),loadRCStatus));
$('rc-backup').addEventListener('click',()=>busy($('rc-backup'),async()=>{const result=await api('/api/backup',{});$('rc-backup-result').textContent=`Проверенная копия: backups/${result.name}; файлов ${result.files}, кадров ${result.frames}. Restore выполняется CLI в новую папку.`;notify('SQLite и зарегистрированные кадры скопированы; integrity и SHA256 проверены.');}));
function addSynthesisTaskButton(box,synthesis,item){const button=el('button','button','Создать практическую задачу из принятого вывода');button.disabled=item.status!=='user_accepted'||item.citation_status!=='references_valid';button.title='Нужно личное принятие и валидные ссылки; это не независимая истинность.';button.addEventListener('click',()=>busy(button,async()=>{await api('/api/synthesis-task',{synthesis_id:synthesis.id,item_id:item.id});notify('Задача сохранена в Chat · поиск / mock и Обучении; оригинальный AI-вывод не заменён.');}));box.append(button);}


const originalRCStatus=loadRCStatus;
loadRCStatus=async function(){
  const result=await originalRCStatus();
  const registered=result.registered_xagon||{};
  $('rc-system-status').replaceChildren(
    el('p',result.healthy?'':'error','Offline '+(result.healthy?'Ready':'Needs Check')),
    el('p','','Manual AI Exchange Ready — ручная вставка текста/JSON; отправки нет.'),
    el('p','','Cloud AI Disabled — реальные модели не подключены.'),
    el('p','','XAGON Processing Status: '+(registered.registered?`${registered.transcriptions} транскрипций, ${registered.vision_done} Vision; отсутствуют: ${(registered.remaining_vision||[]).join(', ')}. Снимок импортирован, AI-наблюдения не подтверждены.`:'Есть только отчёты; результаты ещё не зарегистрированы в этой библиотеке.')),
    el('p','','Database / Backup Status: '+(result.database.integrity||'не открыта')+'. Backup проверяется при создании; исходные видео/документы сохраняйте отдельно.')
  );
  $('rc-import-saved').disabled=!result.saved_bundle_available;
  $('welcome-saved-bundle').disabled=!result.saved_bundle_available;
  return result;
};
async function importSavedResults(){
  const result=await api('/api/import-saved-xagon',{});
  $('rc-saved-result').textContent=`Зарегистрировано ${result.transcriptions} транскрипций и ${result.vision_done} Vision. Не завершены: ${result.remaining_vision.join(', ')}. Кадры не копировались; наблюдения доступны как отдельный материал.`;
  await refreshLibrary();await loadRCStatus();
  await openMaterial(result.material_id);
}
$('rc-import-saved').addEventListener('click',()=>busy($('rc-import-saved'),importSavedResults));
$('welcome-saved-bundle').addEventListener('click',()=>busy($('welcome-saved-bundle'),importSavedResults));

function addManualExchange(card,p){
  const text=p.prompt+'\n\nUNTRUSTED SOURCE CONTEXT (JSON):\n'+JSON.stringify(p,null,2);
  const bytes=new TextEncoder().encode(text).length;
  const box=el('section','note-section');box.append(el('h4','','Ручной обмен с ChatGPT'),el('p','small muted',`${bytes} байт; приблизительно ${Math.ceil(bytes/3)} токенов (не tokenizer). Перед передачей проверьте конфиденциальность. Приложение не открывает ChatGPT.`));
  const context=el('textarea');context.readOnly=true;context.rows=5;context.value=text;context.setAttribute('aria-label','Промпт и полный контекст для копирования');
  const copy=el('button','button','Копировать промпт и контекст');copy.type='button';copy.disabled=bytes>1024*1024;
  copy.addEventListener('click',()=>busy(copy,async()=>{try{await navigator.clipboard.writeText(text);notify('Скопировано локально. Передавайте самостоятельно после проверки.');}catch{context.focus();context.select();notify('Автокопирование недоступно. Текст выделен: нажмите Ctrl+C.');}}));
  box.append(context,copy);if(copy.disabled)box.append(el('p','error','Больше 1 МиБ: уточните тему/уменьшите источники перед копированием.'));
  const form=el('form','question-form'),input=el('textarea');input.rows=8;input.maxLength=768*1024;input.setAttribute('aria-label','Ответ ChatGPT — текст или JSON');
  const key='manual:'+p.id;input.value=fusionDrafts.get(key)||'';
  input.addEventListener('input',()=>{if(input.value)fusionDrafts.set(key,input.value);else fusionDrafts.delete(key);});
  const mock=el('input');mock.type='checkbox';const mockLabel=el('label','','Тестовый mock-ответ, не настоящий ChatGPT');mockLabel.prepend(mock);
  const save=el('button','button primary','Проверить и сохранить ручной ответ');save.type='submit';
  form.append(input,el('p','small muted','Текст: 1–30 абзацев, ссылки [cite_ID] из пакета. Абзацы без ссылок останутся неподтверждёнными. JSON допускается в ```json code fence. Оригинал ответа сохраняется.'),mockLabel,save);
  form.addEventListener('submit',event=>{event.preventDefault();busy(save,async()=>{
    const submitted=input.value;const result=await api('/api/manual-response',{package_id:p.id,text:submitted,mock:mock.checked});
    if(fusionDrafts.get(key)===submitted){fusionDrafts.delete(key);if(input.value===submitted)input.value='';}
    await loadFusion();await openFusionSynthesis(result.id);notify('Ручной ответ сохранён с оригиналом; проверены ссылки, не истинность.');
  });});box.append(form);card.append(box);
}

loadRCStatus().catch(e=>{$('rc-readiness').textContent='Самодиагностика недоступна. '+e.message;});
