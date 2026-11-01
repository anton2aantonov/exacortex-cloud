// Behavior checks for private, unsaved per-material drafts and in-flight saves.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const fields={'note-text':{value:''},'note-chunk':{value:''}};
const document={getElementById:id=>fields[id],querySelector:()=>({content:'test-token'})};
const context=vm.createContext({document,setTimeout,clearTimeout});
const source=fs.readFileSync(path.join(__dirname,'../xagon/alpha_static/app.js'),'utf8');
vm.runInContext(source.slice(0,source.indexOf("$('library-filter').addEventListener")),context);
const run=s=>vm.runInContext(s,context);
function select(id){context.selected=id;run('current={id:selected};restoreNoteDraft()');}
function type(text,chunk=''){fields['note-text'].value=text;fields['note-chunk'].value=chunk;run('captureNoteDraft()');}
select('a');type('Первый черновик','17');select('b');
assert.equal(fields['note-text'].value,'');
type('Second draft','25');select('a');
assert.equal(fields['note-text'].value,'Первый черновик');
assert.equal(fields['note-chunk'].value,'17');
// The request captured an earlier value; typing during it must not disappear.
type('Новый текст после отправки','17');
assert.equal(run("finishNoteSave('a','Первый черновик','17')"),false);
assert.equal(fields['note-text'].value,'Новый текст после отправки');
// A successful request clears only the matching draft.
assert.equal(run("finishNoteSave('a','Новый текст после отправки','17')"),true);
assert.equal(fields['note-text'].value,'');
select('b');assert.equal(fields['note-text'].value,'Second draft');
// Finish a save while another material is visible: preserve that visible draft.
select('a');type('A again','3');
assert.equal(run("finishNoteSave('b','Second draft','25')"),true);
assert.equal(fields['note-text'].value,'A again');
select('b');assert.equal(fields['note-text'].value,'');
// Changing the anchor while a save is in flight is also a new draft.
type('Same text','40');
assert.equal(run("finishNoteSave('b','Same text','39')"),false);
assert.equal(fields['note-chunk'].value,'40');
// Failed requests never call finishNoteSave; navigation retains the input.
select('a');select('b');assert.equal(fields['note-text'].value,'Same text');
assert.equal(fields['note-chunk'].value,'40');
type('');assert.equal(run('noteDrafts.has("b")'),false);
console.log('16 draft behavior assertions passed: switching, concurrent edits, captured save, anchor change, empty draft.');
