import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

class Element {
  constructor(id = '', tagName = 'INPUT') { this.id = id; this.tagName = tagName; this.value = ''; this.hidden = false; this.disabled = false; this.checked = false; this.children = []; this.dataset = {}; this.classList = { toggle() {} }; this.files = []; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this[key] = value; }
}
const root = new URL('../', import.meta.url);
const html = await fs.readFile(new URL('pages/studio/index.html', root), 'utf8');
const elements = new Map([...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)].map((match) => [match[2], new Element(match[2], match[1].toUpperCase())]));
const tabs = ['portraits','create','history','settings'].map((name) => { const element = new Element('', 'BUTTON'); element.dataset.tab = name; return element; });
const sections = tabs.map((tab) => elements.get(tab.dataset.tab));
sections.forEach((section) => { section.hidden = section.id !== 'portraits'; });
const settings = Object.fromEntries(Object.entries(JSON.parse(await fs.readFile(new URL('_conf_schema.json', root), 'utf8'))).map(([key,value]) => [key,value.default]));
let modelCalls = 0;
let generationCalls = 0;
let connectionFails = false;
const graph = {p:{class_type:'CLIPTextEncode',inputs:{text:'original'}},s:{class_type:'KSampler',inputs:{steps:20,positive:['p',0]}},o:{class_type:'SaveImage',inputs:{images:['s',0]}}};
const inspected = {name:'api/example.json',source:'api/example.json',url:settings.comfy_url,graph,fingerprint:'abc123',nodes:3,fields:[{node:'p',field:'text',label:'Positive text',type:'STRING',linked:false,value:'original',options:{},choices:[]},{node:'s',field:'steps',label:'Sampler steps',type:'INT',linked:false,value:20,options:{min:1,max:100},choices:[]}],outputs:[{id:'o',label:'SaveImage'}],suggested:{positive:['p','text'],output:'o'}};
const state = { settings, revision:'r1', workflow:{}, workflow_revision:'w1', personas:[{id:'alice'},{id:'bob'}], providers:[{id:'local-vl',model:'vision'}], portraits:{alice:{appearance:'blue eyes',style:'watercolor',image:'',auto:true},bob:{appearance:'green eyes',style:'ink',image:''}}, task:{active:null,cooldown_until:0},history:{items:[]} };
const bridge = {
  async ready() {},
  async apiGet(action) { if (action === 'state') return structuredClone(state); throw new Error('unexpected GET'); },
  async apiPost(action, body) {
    if (action === 'settings') { assert.equal(body.revision, state.revision, 'stale form must not overwrite server changes'); state.settings = body.settings; state.revision = 'r2'; return {}; }
    if (action === 'portrait') { state.portraits[body.persona_id] = body.portrait; return {}; }
    if (action === 'preview') { modelCalls++; return {prompt:'a character in a sunlit garden'}; }
    if (action === 'generate') { generationCalls++; return {id:'test-generation'}; }
    if (action === 'connection') { if (connectionFails) throw new Error('offline'); return {system:{comfyui_version:'test'}}; }
    if (action === 'workflows') return {items:[{path:'api/example.json',modified:Date.now()}]};
    if (action === 'workflow-inspect') return structuredClone(inspected);
    if (action === 'workflow-save') { assert.equal(body.revision,state.workflow_revision,'stale workflow must not overwrite server changes'); state.workflow = {...body.workflow,fingerprint:'abc123'};state.workflow_revision='w2';return {revision:'w2'}; }
    throw new Error('unexpected POST');
  },
};
let tick;
const document = { getElementById(id) { assert(elements.has(id), `missing element ${id}`); return elements.get(id); }, createElement(tag) { return new Element('', tag.toUpperCase()); }, querySelectorAll(selector) { if (selector === 'nav button') return tabs; if (selector === 'main>section') return sections; if (selector === 'button,input,select,textarea') return [...elements.values(),...tabs]; throw new Error(selector); } };
const bridgeName = process.argv.includes('--modern') ? 'AstrBotPluginView' : 'AstrBotPluginPage';
const window = { [bridgeName]:bridge, setInterval(fn) { tick = fn; return 1; }, clearInterval() {}, addEventListener() {}, confirm() { return false; } };
vm.runInNewContext(await fs.readFile(new URL('pages/studio/app.js', root), 'utf8'), {document,window,Option:class {constructor(text,value) { this.text=text;this.value=value; }},console});
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const el = (id) => elements.get(id);
await settle();
assert.match(el('notice').textContent, /已连接/);
el('persona').value='alice'; await el('persona').onchange();
assert.equal(el('appearance').value,'blue eyes');
el('persona').value='bob'; await el('persona').onchange();
assert.equal(el('appearance').value,'green eyes');
await tabs.find((tab) => tab.dataset.tab === 'create').onclick();
assert.equal(el('create').hidden,false);
el('idea').value='sitting by a window'; await el('preview').onclick();
assert.equal(modelCalls,1); assert.equal(generationCalls,0); assert.match(el('prompt-preview').value,/sunlit/);
await el('generate').onclick(); assert.equal(generationCalls,1);
el('width').value='896'; await tick(); await settle(); assert.equal(el('width').value,'896');
await el('save-settings').onclick(); assert.equal(state.settings.width,896);
state.revision='external-change'; await tick(); await settle();
el('width').value='904'; await el('save-settings').onclick();
assert.match(el('notice').textContent,/stale form/); assert.equal(state.settings.width,896);
await el('release').onclick();
assert.equal(el('release').disabled,false);
await el('connection').onclick();
assert.equal(el('workflow-list').children[1].value,'api/example.json');
el('workflow-list').value='api/example.json'; await el('workflow-load').onclick();
assert.equal(el('workflow-editor').hidden,false);
const fields = el('workflow-fields').children.flatMap((group) => group.children.slice(1));
assert.equal(fields[0].hidden,true,'LLM mapped fields cannot also be scalar overrides');
const enableSteps = fields[1].children[0].children[0];
const steps = fields[1].children[1];
assert.equal(steps.disabled,true); enableSteps.checked = true; enableSteps.onchange(); steps.value='30';
await el('workflow-save').onclick();
assert.equal(state.workflow.mapping.positive[0],'p');
assert.equal(state.workflow.overrides[0].value,30);
assert.equal(el('legacy-settings').hidden,true);
state.workflow_revision='someone-else-changed'; await tick(); await settle();
await el('workflow-save').onclick(); assert.match(el('notice').textContent,/stale workflow/);
connectionFails=true; await el('connection').onclick(); assert.match(el('notice').textContent,/offline/);
await el('workflow-save').onclick(); assert.match(el('notice').textContent,/先检测/);
assert.equal(el('workflow-save').disabled,false);
console.log(`Frontend checks passed (${bridgeName}): persona isolation, draft/revision safety, workflow discovery, dynamic fields, mapping, save, offline gate.`);
