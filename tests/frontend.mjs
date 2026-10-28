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
const html = await fs.readFile(new URL('views/studio/index.html', root), 'utf8');
const elements = new Map([...html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)].map((match) => [match[2], new Element(match[2], match[1].toUpperCase())]));
const tabs = ['portraits','rules','create','history','settings'].map((name) => { const element = new Element('', 'BUTTON'); element.dataset.tab = name; return element; });
const sections = tabs.map((tab) => elements.get(tab.dataset.tab));
sections.forEach((section) => { section.hidden = section.id !== 'portraits'; });
const settings = Object.fromEntries(Object.entries(JSON.parse(await fs.readFile(new URL('_conf_schema.json', root), 'utf8'))).map(([key,value]) => [key,value.default]));
let modelCalls = 0;
let generationCalls = 0;
const state = { settings, revision:'r1', personas:[{id:'alice'},{id:'bob'}], providers:[{id:'local-vl',model:'vision'}], portraits:{alice:{appearance:'blue eyes',style:'watercolor',image:'',auto:true},bob:{appearance:'green eyes',style:'ink',image:''}}, task:{active:null,cooldown_until:0},history:{items:[]} };
const bridge = {
  async ready() {},
  async apiGet(action) { if (action === 'state') return structuredClone(state); throw new Error('unexpected GET'); },
  async apiPost(action, body) {
    if (action === 'settings') { assert.equal(body.revision, state.revision, 'stale form must not overwrite server changes'); state.settings = body.settings; state.revision = 'r2'; return {}; }
    if (action === 'portrait') { state.portraits[body.persona_id] = body.portrait; return {}; }
    if (action === 'preview') { modelCalls++; return {prompt:'a character in a sunlit garden'}; }
    if (action === 'generate') { generationCalls++; return {id:'test-generation'}; }
    if (action === 'connection') return {system:{comfyui_version:'test'}};
    throw new Error('unexpected POST');
  },
};
let tick;
const document = { getElementById(id) { assert(elements.has(id), `missing element ${id}`); return elements.get(id); }, createElement(tag) { return new Element('', tag.toUpperCase()); }, querySelectorAll(selector) { if (selector === 'nav button') return tabs; if (selector === 'main>section') return sections; if (selector === 'button,input,select,textarea') return [...elements.values(),...tabs]; throw new Error(selector); } };
const window = { AstrBotPluginView:bridge, setInterval(fn) { tick = fn; return 1; }, clearInterval() {}, addEventListener() {}, confirm() { return false; } };
vm.runInNewContext(await fs.readFile(new URL('views/studio/app.js', root), 'utf8'), {document,window,Option:class {constructor(text,value) { this.text=text;this.value=value; }},console});
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
console.log('Frontend checks passed: persona isolation, tabs, prompt preview, submit, draft preservation, revision conflict, controls restored.');
