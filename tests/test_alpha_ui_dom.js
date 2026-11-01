// Dependency-free behavioral checks for the text-safe UI renderer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Node {
  constructor(tag) { this.tagName=tag; this.children=[]; this.textContent=''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; }
  addEventListener(name, callback) { (this.events ||= {})[name]=callback; }
}
const context=vm.createContext({document:{createElement:tag=>new Node(tag),querySelector:()=>({content:'test-token'})},setTimeout,clearTimeout});
const source=fs.readFileSync(path.join(__dirname,'../xagon/alpha_static/app.js'),'utf8');
vm.runInContext(source.slice(0,source.indexOf("$('library-filter').addEventListener")),context);
vm.runInContext("current={id:'mat_abc',chunks:[{id:7,ordinal:2,material_id:'mat_abc',start_seconds:62.9}]}",context);
const target=new Node('div'); target.id='notes-content';context.target=target;
vm.runInContext("renderMarkdown(target, '### Code &lt;example&gt;\\n> &lt;script&gt;alert(1)&lt;/script&gt; &amp; \\\\[x\\\\]\\nИсточник: mat_abc#chunk-7 · 00:01:02\\nИсточник: mat_other#chunk-7 · 00:01:02')",context);
const section=target.children[0];
assert.equal(section.tagName,'details');
assert.equal(section.open,false);
assert.equal(section.children[0].textContent,'Code <example>');
assert.equal(section.children[1].textContent,'<script>alert(1)</script> & [x]');
assert.equal(section.children[2].tagName,'button');
assert.match(section.children[2].textContent,/00:01:02.*фрагмент 3/);
assert.equal(section.children[3].tagName,'p');
assert.equal(section.children.some(n=>n.tagName==='script'),false);
target.id='practice-content';vm.runInContext("renderMarkdown(target, '### Задание 1\\nМой ответ: …')",context);
assert.equal(target.children[0].open,true);
assert.equal(vm.runInContext("markdownPlain('&amp;lt;')",context),'&lt;');
assert.equal(vm.runInContext('time(3661.9)',context),'01:01:01');
assert.equal(vm.runInContext('time(null)',context),null);
console.log('12 renderer/time assertions passed; source links only for existing current-material chunks.');
