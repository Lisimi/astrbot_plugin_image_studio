const $ = (id) => document.getElementById(id);
const bridge = window.AstrBotPluginView || window.AstrBotPluginPage;
let state = null;
let formRevision = "";
let portraitImage = "";
let previewToken = "";
let busy = false;
let poll = null;
let workflowDraft = null;
let workflowRevision = "";
let connectedUrl = "";
let workflowControls = [];
let mappingControls = {};
let negativeControl = null;
let workflowGroups = [];
const settingsFields = ["comfy_url", "vision_provider", "prompt_provider", "cooldown_seconds", "task_timeout", "width", "height", "unet", "clip", "vae", "steps", "cfg", "negative", "prompt_rules", "auto_rules"];
const numeric = new Set(["cooldown_seconds", "task_timeout", "width", "height", "steps", "cfg"]);
const labels = {preparing:"生成提示词",submitting:"提交工作流",running:"正在生图",unknown:"结果未知，等待查询",downloaded:"图片已下载",sending:"发送图片",completed:"已完成",failed:"失败",send_failed:"已生成，发送失败",send_unknown:"已生成，发送待确认",rejected:"未提交",released:"已解除",utility:"处理画像/提示词"};

function notice(message, error = false) { $("notice").textContent = message; $("notice").classList.toggle("error", error); }
async function api(action, body) { return body === undefined ? bridge.apiGet(action) : bridge.apiPost(action, body); }
async function operation(fn) {
  if (busy) return;
  busy = true;
  const controls = [...document.querySelectorAll("button,input,select,textarea")];
  const disabled = controls.map((el) => el.disabled);
  controls.forEach((el) => { el.disabled = true; });
  try { await fn(); } catch (error) { notice(error.message || String(error), true); }
  finally { controls.forEach((el, i) => { el.disabled = disabled[i]; }); busy = false; }
}
function options(select, values, empty) {
  const previous = select.value;
  select.replaceChildren(new Option(empty, ""), ...values.map((value) => new Option(value.label || value.id, value.id)));
  if (values.some((value) => value.id === previous)) select.value = previous;
}
async function imagePreview(folder, name, image) {
  image.hidden = true;
  if (!name) return;
  const data = await bridge.apiGet("thumbnail", { folder, name });
  image.src = data.url;
  image.hidden = false;
}
async function loadPortrait() {
  previewToken = "";
  $("prompt-preview").value = "";
  const id = $("persona").value;
  const portrait = state.portraits[id] || {};
  portraitImage = portrait.image || "";
  for (const field of ["appearance", "style", "habits"]) $(field).value = portrait[field] || "";
  $("mode").value = portrait.mode || "text2img";
  $("denoise").value = portrait.denoise ?? 0.55;
  $("auto").checked = portrait.auto === true;
  $("empty-image").hidden = Boolean(portraitImage);
  $("create-persona").textContent = id ? `当前人格：${id}。画像变更后请先保存。` : "请先在“人格画像”中选择人格并保存画像。";
  await imagePreview("portraits", portraitImage, $("portrait-preview"));
}
function taskStatus() {
  const active = state.task.active;
  if (active) $("task").textContent = `任务：${labels[active.stage] || active.stage} · ${active.prompt_id || active.id} ${active.error || ""}`;
  else { const left = Math.max(0, Math.ceil((state.task.cooldown_until || 0) - Date.now() / 1000)); $("task").textContent = left ? `全局冷却：剩余 ${left} 秒` : "当前空闲 · 所有聊天和网页共用一个生成任务"; }
}
async function history() {
  $("history-list").replaceChildren();
  const items = state.history.items || [];
  if (!items.length) { $("history-list").textContent = "还没有生成记录。"; return; }
  for (const job of items.slice(0, 24)) {
    const card = document.createElement("article"); card.className = "history-card";
    const heading = document.createElement("h3"); heading.textContent = `${job.persona_id} · ${labels[job.stage] || job.stage}`; card.append(heading);
    const meta = document.createElement("p"); meta.textContent = `${new Date(job.started * 1000).toLocaleString()} · seed ${job.seed}`; card.append(meta);
    if (job.file) {
      const image = document.createElement("img"); image.alt = `人格 ${job.persona_id} 的生成图片`; card.append(image);
      imagePreview("outputs", job.file, image).catch(() => { image.alt = "预览读取失败，可尝试下载"; });
      const download = document.createElement("button"); download.textContent = "下载图片";
      download.onclick = () => operation(() => bridge.download("download", {folder:"outputs",name:job.file}, job.file)); card.append(download);
    }
    const details = document.createElement("details"); const title = document.createElement("summary"); title.textContent = "最终提示词与任务信息";
    const text = document.createElement("pre"); text.textContent = [job.prompt,job.error,`任务 ID：${job.prompt_id || job.id}`].filter(Boolean).join("\n\n"); details.append(title,text); card.append(details);
    $("history-list").append(card);
  }
}
async function refresh(full = false) {
  state = await api("state");
  if (full) {
    formRevision = state.revision;
    options($("persona"), state.personas, "选择人格");
    const providers = state.providers.map((p) => ({id:p.id,label:`${p.id} · ${p.model || ""}`}));
    options($("vision_provider"), providers, "请选择支持看图的模型");
    options($("prompt_provider"), providers, "聊天时跟随会话；网页需指定");
    for (const field of settingsFields) $(field).value = state.settings[field] ?? "";
    workflowRevision = state.workflow_revision;
    showWorkflow();
    await loadPortrait();
  }
  taskStatus();
  if (!$("history").hidden) await history();
}
function selected() { const id = $("persona").value; if (!id) throw new Error("请先选择人格"); return id; }
async function saveSettings() {
  const settings = Object.fromEntries(settingsFields.map((field) => [field,numeric.has(field) ? Number($(field).value) : $(field).value]));
  await api("settings", {revision:formRevision,settings});
  previewToken = ""; $("prompt-preview").value = "";
  await refresh(false); formRevision = state.revision; notice("设置已保存，后续任务使用新设置。");
}
document.querySelectorAll("nav button").forEach((button) => {
  button.onclick = () => operation(async () => {
    document.querySelectorAll("main>section").forEach((section) => { section.hidden = section.id !== button.dataset.tab; });
    document.querySelectorAll("nav button").forEach((tab) => tab.setAttribute("aria-selected", String(tab === button)));
    if (button.dataset.tab === "history") { await refresh(); }
  });
});
$("persona").onchange = () => operation(loadPortrait);
$("refresh").onclick = () => operation(async () => { await refresh(false); notice("状态已刷新；编辑中的表单保留。"); });
$("portrait-file").onchange = () => operation(async () => {
  const file = $("portrait-file").files[0]; if (!file) return;
  if (file.size > 20 * 1024 * 1024) throw new Error("图片须小于 20 MB");
  const result = await bridge.upload("upload", file); portraitImage = result.image;
  await imagePreview("portraits", portraitImage, $("portrait-preview")); $("empty-image").hidden = true;
  notice("参考图已上传，请解析或编辑后保存画像。");
});
$("analyze").onclick = () => operation(async () => {
  if (!portraitImage) throw new Error("请先上传参考图");
  notice("正在解析画像…");
  const result = await api("analyze", {persona_id:selected(),image:portraitImage});
  for (const field of ["appearance","style","habits"]) $(field).value = result[field];
  notice("解析完成，请检查外观与画风后保存。");
});
$("save-portrait").onclick = () => operation(async () => {
  const id = selected();
  const portrait = {persona_id:id,image:portraitImage,appearance:$("appearance").value,style:$("style").value,habits:$("habits").value,mode:$("mode").value,denoise:Number($("denoise").value),auto:$("auto").checked};
  await api("portrait", {persona_id:id,portrait}); await refresh(false); notice("人格画像已保存并确认。");
  previewToken = ""; $("prompt-preview").value = "";
});
$("save-rules").onclick = () => operation(saveSettings);
$("save-models").onclick = () => operation(saveSettings);
$("save-settings").onclick = () => operation(saveSettings);
$("connection").onclick = () => operation(async () => {
  connectedUrl = "";
  $("workflow-list").replaceChildren(new Option("连接失败或尚未检测", ""));
  const url = $("comfy_url").value.replace(/\/+$/, "");
  const result = await api("connection", {url});
  connectedUrl = url;
  notice(`ComfyUI 已连接 · 版本 ${result.system.comfyui_version || "未知"}`);
  const listing = await api("workflows", {url});
  $("workflow-list").replaceChildren(new Option("选择已保存的 JSON", ""), ...listing.items.map((item) => new Option(`${item.path} · ${new Date(item.modified).toLocaleString()}`, item.path)));
  $("workflow-info").textContent = `发现 ${listing.items.length} 个 JSON。请选择并加载；文件格式会在加载时校验。`;
});

function showWorkflow() {
  const current = state.workflow;
  $("workflow-current").textContent = current?.graph ? `当前工作流：${current.name} · 指纹 ${current.fingerprint.slice(0,12)}` : "当前工作流：内置 Anima（兼容原配置）";
  $("legacy-settings").hidden = Boolean(current?.graph);
  $("mode-label").hidden = Boolean(current?.graph);
  $("denoise-label").hidden = Boolean(current?.graph) && !current.mapping.denoise;
}
function requireConnection() {
  if (!connectedUrl || connectedUrl !== $("comfy_url").value.replace(/\/+$/, "")) throw new Error("请先检测当前 ComfyUI 地址的连接");
  return connectedUrl;
}
function bindingValue(field) { return JSON.stringify([field.node, field.field]); }
function drawWorkflow(draft) {
  workflowDraft = draft;
  workflowRevision = state.workflow_revision;
  const saved = state.workflow;
  const matching = saved?.graph && (saved.source === draft.source && saved.name === draft.name);
  const mapping = matching ? saved.mapping : draft.suggested;
  const overrides = matching ? saved.overrides : [];
  $("workflow-info").textContent = `${draft.name} · ${draft.nodes} 个节点 · 指纹 ${draft.fingerprint.slice(0,12)}。${matching && saved.fingerprint !== draft.fingerprint ? "文件已更新，请检查原映射及参数后重新保存。" : "请确认映射与参数后保存。"}`;
  $("workflow-editor").hidden = false;
  $("workflow-mappings").replaceChildren();
  $("workflow-fields").replaceChildren();
  mappingControls = {};
  const roles = {positive:["正面提示词（LLM 输出，必填）","STRING"],negative:["负面提示词（可选）","STRING"],image:["人格参考图（可选）","STRING"],seed:["每次随机种子（建议设置）","INT"],denoise:["人格重绘强度（可选）","FLOAT"]};
  for (const [role,[title,type]] of Object.entries(roles)) {
    const label = document.createElement("label"); label.textContent = title;
    const select = document.createElement("select");
    const candidates = draft.fields.filter((field) => role === "image"
      ? (field.options.image_upload === true || (draft.graph[field.node].class_type === "LoadImage" && field.field === "image"))
      : field.type === type);
    select.replaceChildren(new Option("不映射，保留工作流值", ""), ...candidates.map((field) => new Option(`${field.label}${field.linked ? "（将替换连线）" : ""}`, bindingValue(field))));
    if (mapping[role]) {
      const selected = JSON.stringify(mapping[role]);
      if (!candidates.some((field) => bindingValue(field) === selected)) select.append(new Option(`已失效：${mapping[role].join(".")}`, selected));
      select.value = selected;
    }
    mappingControls[role] = select;
    label.append(select); $("workflow-mappings").append(label);
    select.onchange = () => updateMappedFields();
  }
  const outputLabel = document.createElement("label"); outputLabel.textContent = "图片输出（必填）";
  const output = document.createElement("select");
  output.replaceChildren(new Option("选择输出节点", ""), ...draft.outputs.map((item) => new Option(item.label,item.id)));
  if (mapping.output) {
    if (!draft.outputs.some((item) => item.id === mapping.output)) output.append(new Option(`已失效：${mapping.output}`,mapping.output));
    output.value = mapping.output;
  }
  mappingControls.output = output; outputLabel.append(output); $("workflow-mappings").append(outputLabel);
  output.onchange = () => updateMappedFields();
  const negativeLabel = document.createElement("label"); negativeLabel.textContent = "映射使用的负面提示词";
  negativeControl = document.createElement("textarea");
  const negTarget = mapping.negative;
  const originalNegative = negTarget ? draft.graph[negTarget[0]]?.inputs?.[negTarget[1]] : "";
  negativeControl.value = matching ? saved.negative : (typeof originalNegative === "string" ? originalNegative : state.settings.negative);
  negativeLabel.append(negativeControl); $("workflow-mappings").append(negativeLabel);
  workflowControls = [];
  workflowGroups = [];
  const groups = new Map();
  for (const field of draft.fields.filter((item) => !item.linked)) {
    const row = document.createElement("div"); row.className = "workflow-parameter";
    const label = document.createElement("label"); label.className = "check";
    const enabled = document.createElement("input"); enabled.type = "checkbox"; label.textContent = field.label; label.append(enabled);
    const control = document.createElement(field.type === "ENUM" ? "select" : field.type === "STRING" ? "textarea" : "input");
    if (field.type === "ENUM") control.replaceChildren(...field.choices.map((value) => new Option(String(value),String(value))));
    else if (field.type === "BOOLEAN") control.type = "checkbox";
    else if (field.type === "INT" || field.type === "FLOAT") {
      control.type = "number"; control.step = field.type === "INT" ? "1" : "any";
      if (field.options.min !== undefined) control.min = String(field.options.min);
      if (field.options.max !== undefined) control.max = String(field.options.max);
    }
    const override = overrides.find((item) => item.node === field.node && item.field === field.field);
    const value = override ? override.value : field.value;
    if (field.type === "BOOLEAN") control.checked = value === true; else control.value = String(value);
    enabled.checked = Boolean(override); control.disabled = !enabled.checked;
    enabled.onchange = () => { control.disabled = !enabled.checked; };
    if (!groups.has(field.node)) {
      const group = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = `${field.node} · ${draft.graph[field.node].class_type}`;
      group.append(summary); group.open = false;
      groups.set(field.node,group); workflowGroups.push({node:field.node,group});
      $("workflow-fields").append(group);
    }
    const group = groups.get(field.node); if (override) group.open = true;
    row.append(label,control); group.append(row);
    workflowControls.push({field,enabled,control,row});
  }
  const currentFields = new Set(draft.fields.map(bindingValue));
  if (overrides.some((item) => !currentFields.has(JSON.stringify([item.node,item.field])))) {
    $("workflow-info").textContent += " 已移除不存在的旧覆盖参数，请重新确认。";
  }
  updateMappedFields();
}
function updateMappedFields() {
  const mapped = new Set(Object.entries(mappingControls).filter(([role]) => role !== "output").map(([,control]) => control.value));
  const active = new Set();
  function visit(node) {
    if (active.has(node) || !workflowDraft.graph[node]) return;
    active.add(node);
    for (const [field,value] of Object.entries(workflowDraft.graph[node].inputs)) {
      if (!mapped.has(JSON.stringify([node,field])) && Array.isArray(value)) visit(value[0]);
    }
  }
  visit(mappingControls.output.value);
  for (const {field,row,enabled} of workflowControls) { row.hidden = mapped.has(bindingValue(field)) || !active.has(field.node); if (row.hidden) enabled.checked = false; }
  for (const {node,group} of workflowGroups) group.hidden = !workflowControls.some((item) => item.field.node === node && !item.row.hidden);
  if (negativeControl) negativeControl.disabled = !mappingControls.negative.value;
}
async function inspectWorkflow(body) {
  workflowDraft = null; $("workflow-editor").hidden = true;
  const draft = await api("workflow-inspect",{url:requireConnection(),...body});
  drawWorkflow(draft);
}
$("workflow-load").onclick = () => operation(async () => {
  const path = $("workflow-list").value; if (!path) throw new Error("请选择一个工作流文件");
  await inspectWorkflow({path});
});
$("workflow-file").onchange = () => operation(async () => {
  const file = $("workflow-file").files[0]; if (!file) return;
  if (file.size > 1024 * 1024) throw new Error("API 工作流文件须小于 1 MB");
  await inspectWorkflow({name:file.name,path:"",paired_source:$("workflow-list").value,graph:JSON.parse(await file.text())});
});
$("workflow-save").onclick = () => operation(async () => {
  const url = requireConnection();
  if (url !== state.settings.comfy_url) throw new Error("请先保存 ComfyUI 地址设置，再保存工作流");
  if (!workflowDraft || workflowDraft.url !== url) throw new Error("请重新加载工作流");
  const mapping = Object.fromEntries(Object.entries(mappingControls).filter(([,control]) => control.value).map(([role,control]) => [role,role === "output" ? control.value : JSON.parse(control.value)]));
  const overrides = workflowControls.filter((item) => item.enabled.checked && !item.row.hidden).map(({field,control}) => ({node:field.node,field:field.field,value:field.type === "BOOLEAN" ? control.checked : ["INT","FLOAT"].includes(field.type) ? Number(control.value) : field.type === "ENUM" ? field.choices.find((value) => String(value) === control.value) : control.value}));
  const workflow = {name:workflowDraft.name,source:workflowDraft.source,graph:workflowDraft.graph,mapping,overrides,negative:negativeControl.value};
  const result = await api("workflow-save",{url,revision:workflowRevision,workflow});
  workflowRevision = result.revision;
  previewToken = ""; $("prompt-preview").value = "";
  await refresh(false); showWorkflow(); notice("工作流已校验并保存，仅对新任务生效。");
});
$("workflow-builtin").onclick = () => operation(async () => {
  await api("workflow-save",{url:requireConnection(),revision:workflowRevision,workflow:{}});
  previewToken = ""; $("prompt-preview").value = "";
  await refresh(false); workflowRevision = state.workflow_revision; showWorkflow();
  workflowDraft = null; $("workflow-editor").hidden = true;
  notice("已切回内置 Anima，原有参数与人格画像保留。");
});
$("idea").oninput = () => { previewToken = ""; $("prompt-preview").value = ""; };
$("preview").onclick = () => operation(async () => { notice("正在生成提示词…"); const result = await api("preview", {persona_id:selected(),idea:$("idea").value}); $("prompt-preview").value = result.prompt; previewToken = result.token || ""; notice("预览完成，没有提交 ComfyUI。"); });
$("generate").onclick = () => operation(async () => { const result = await api("generate", {persona_id:selected(),idea:$("idea").value,preview_token:previewToken}); notice(`已受理 ${result.id.slice(0,8)}，请查看任务状态和生成记录。`); await refresh(); });
$("recover").onclick = () => operation(async () => { await api("recover", {}); await refresh(); notice("已查询或恢复原任务，没有重新提交生图。"); });
$("release").onclick = () => operation(async () => { if (!window.confirm("请先检查 ComfyUI 队列和历史记录，确认原任务无需继续等待。解除后进入冷却，不会取消或重新提交任务。是否解除？")) return; await api("release", {confirm:true}); await refresh(); notice("未知任务已解除，进入冷却。"); });
if (!bridge) notice("请从 AstrBot 的插件页面打开生图工作室。", true);
else {
  operation(async () => { await bridge.ready(); await refresh(true); notice("工作室已连接，请先选择人格并配置模型。"); });
  poll = window.setInterval(() => { if (!busy && state) refresh(false).catch((error) => notice(error.message, true)); }, 5000);
  window.addEventListener("beforeunload", () => window.clearInterval(poll));
}
