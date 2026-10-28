# 开发文档

本文描述当前实现与维护流程。使用步骤见 [README](README.md)，逐次更新记录见 [CHANGELOG](CHANGELOG.md)。

## 1. 范围与兼容基线

插件运行于 NAS 的 AstrBot，调用用户本机的 ComfyUI。支持本地 ComfyUI 的 API 工作流发现、导入、显式节点映射与动态参数，保留内置 Anima 文生图/参考图重绘作为兼容入口。

已确认 Dashboard 显示 AstrBot `4.28.2`，本机 ComfyUI 为 `0.30.2`。后端使用 `astrbot.api.web`、`context.llm_generate`、`persona_manager.resolve_selected_persona` 等接口，NAS 实际加载与完整链路仍需验收。`metadata.yaml` 的最低版本声明不是对所有中间发行版的验证承诺。

AstrBot `4.28.2` 只扫描 `pages/<页面名>/index.html`，桥接对象为 `AstrBotPluginPage`。页面采用 `pages/studio/`，脚本同时兼容较新版桥接名称 `AstrBotPluginView`；标题使用 `.astrbot-plugin/i18n/zh-CN.json` 的 `pages.studio`。

## 2. 文件职责

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期、指令、LLM 工具、人格解析、提示词调用与管理 API |
| `models.py` | 配置和画像的数据结构、字段与数值验证 |
| `engine.py` | 全局互斥、后台任务、恢复查询、发送状态与冷却 |
| `comfy.py` | ComfyUI 工作流列表/读取、节点定义、上传、提交、轮询及有大小限制的下载 |
| `workflow.py` | 兼容旧配置的内置 Anima 构建器 |
| `workflow_config.py` | API 格式识别、输入元数据、图依赖裁剪、映射与覆盖校验、种子范围及图渲染 |
| `workflows/anima_api.json` | 当前文生图 API 示例，不是运行时加载的工作流文件 |
| `store.py` | 原子 JSON 写入、画像与结果图片校验和历史记录 |
| `pages/studio/` | 双设置区域、试生成和记录的 HTML、CSS 和前端逻辑 |
| `_conf_schema.json` | AstrBot 配置定义，默认值须与 `Settings` 一致 |
| `metadata.yaml` | 插件元信息与版本 |
| `tests/` | 后端、HTTP 分块响应、SDK 替身和前端交互测试 |
| `docs/` | 工作流调查及实际出图测试记录 |

## 3. 运行链路

1. 手动指令或工具调用使用 AstrBot 的统一人格解析器，解析会话规则、当前对话与默认人格。明确取消人格时不借用其他画像。
2. 读取对应画像，核对人格内容哈希。人格变化后要求管理员重新确认画像。
3. `Engine.start` 在互斥区内检查活动任务和冷却，以最长 5 秒查询真实 ComfyUI 队列。连接失败时直接拒绝，已有其他任务时提示忙碌；两者均不创建任务或新冷却。自定义工作流还须校验地址与实时节点定义，缺少模型/映射则拒绝；检查通过才持久化包括完整工作流在内的快照并后台处理。
4. LLM 根据人格、档案、画面需求和当前消息生成动态画面描述；插件组合固定外观和画风。内部请求不提供生图工具，避免递归。
5. 内置图生图或自定义图有参考图映射时上传当前人格画像。仅替换映射/覆盖输入，裁剪到所选输出依赖链；再次检查队列后提交一次，持久化任务 ID。
6. 轮询原任务并下载保存图片。聊天任务发送至原会话；网页任务在生成记录中查看。
7. 结束后保存历史并进入冷却。异常状态保留原任务，恢复时查询，不盲目重新提交。

网页画像解析和提示词预览也使用互斥入口。预览生成的提示词通过短期 token 确认；人格、画像、设置、工作流或画面需求变化后失效，避免确认时使用另一份提示词。

`ComfyClient.check_connection` 将网络错误、超时、HTTP 失败及不合法响应转换为可直接反馈的 `ComfyUnavailable`。提示词调用前再次检查连接，覆盖预览和受理后断线的情况。手动指令返回明确的中文失败信息，LLM 工具返回 `ok=false`、`reason=comfy_unavailable` 及回复指导，网页返回 HTTP 503。正常聊天仍可进行；离线不自动切换生图方式。

`TOOL_REPLY_RULE` 统一约束 LLM 工具的失败表达：保留当前人格语气/称呼，工具 message 是事实而非逐字回复模板，不主动复述技术诊断或任务编号。冷却/忙碌等 ValueError 同样返回 ok=false/message/instruction，离线保留 comfy_unavailable 分类。guide 保留原 system_prompt 后追加规则，受理成功时可自然说明正在生成；失败时不得虚构成功或承诺自动重试。手动指令及后台失败通知仍直接发送状态，不新增一次 LLM 调用，不声称所有通知都已拟人化。

提交后断线不等于生成失败：保留原任务为 unknown，告知无法确认结果，不宣称没有生成，也不自动重提。后端发送通道和 LLM 回复遵循这一差异。

## 4. 页面与后端接口

前端通过 Dashboard 注入的 bridge 调用接口，不直接读取 Dashboard Token，不另开 Web 服务。`main.py` 注册 `/{插件名}/<path:action>`，所有管理请求要求 Dashboard 登录身份。

| 方法 / action | 用途 |
| --- | --- |
| `GET state` | 配置及版本指纹、人格和提供商列表、画像、任务与历史 |
| `GET thumbnail` / `download` | 安全目录内的缩略图和文件下载 |
| `POST settings` | 校验配置及并发版本，保存至 AstrBot 配置 |
| `POST connection` | 验证当前填写地址的队列 API，再查询 `/system_stats`，不生成 |
| `POST workflows` | 连接检查后列出 `workflows/` 下的 JSON |
| `POST workflow-inspect` | 读取列表中的 API 文件或导入对象，返回图、版本指纹、输入字段与建议映射；不保存 |
| `POST workflow-save` | 根据实时节点定义校验图、映射及覆盖，并核对 workflow revision；空对象切回内置图 |
| `POST upload` | 上传参考图，返回服务器生成的文件名 |
| `POST portrait` | 保存与确认指定人格画像 |
| `POST analyze` | 使用已配置的视觉模型解析例图 |
| `POST preview` / `generate` | 预览提示词或受理实际生图 |
| `POST recover` | 查询/恢复原任务 |
| `POST release` | 显式确认后解除未知任务，要求 ComfyUI 队列空闲 |

前端状态刷新不得覆盖用户正在编辑的表单，也不能将轮询返回的新配置指纹用于提交旧表单。错误信息与模型输出按文本呈现。

页面分为 ComfyUI 设置与人格画像设置。前者管理地址、全局限制和图；后者管理共享 LLM/规则及每个人格的独立档案。自定义图的动态字段不加入 AstrBot `_conf_schema.json`；原字段保持兼容。文件必须保存到 ComfyUI 用户目录，不读取编辑器未保存状态。

## 5. 工作流与存储约束

没有 `workflow.json` 或内容为空时使用原 Anima 构建器，节点 218/214/222 保持兼容。自定义图保留用户原图，只替换显式映射的正面/负面、种子、参考图和强度，以及勾选的字面量覆盖。图复制后按指定 SaveImage/PreviewImage 输出递归裁剪依赖，不执行无关输出节点；映射不在依赖链上则拒绝。

内置模式的文生图使用空 latent，参考图重绘使用 LoadImage → 缩放 → VAEEncode。自定义流程的模式由原图开关和参数决定，不会因人格旧 mode 字段自动改图；映射 image 时总是上传该人格参考图，映射 denoise 时注入其强度。原图姿势/构图是否保留及身份一致性由节点能力决定。

输入定义逐 class 请求 `/object_info/{编码类型名}`，并发上限 4、总等待 30 秒。支持旧枚举数组与新 COMBO/options，连接类型兼容通用 `*` 与 ComfyUI 的 `COMFY_MATCHTYPE_V3`。普通覆盖不允许连线；明确的提示词/种子等映射允许替换相应连线。校验节点存在、必填字段、连接序号/类型、数值/枚举、映射冲突与死节点，拒绝远程 `api_node`。任意自定义字面量、子图及远程服务不在承诺范围。

API 图限制 1 MB、500 节点。种子按实时 INT 范围生成，至多 2^50-1，避免窄范围 seed 被随机大整数破坏。参数表按节点折叠，只有用户勾选的可见字段持久化覆盖；原默认值与连接保留。

ComfyUI 队列检查不能阻止用户稍后手动提交任务；本插件只约束自己的入口，不取消、清空或重排其他任务。

持久数据位于 AstrBot 的 `data/plugin_data/astrbot_plugin_image_studio/`：

- `portraits.json` / `portraits/`：人格画像档案及图片。
- `state.json`：活动任务、配置快照与冷却截止时间。
- `history.json` / `outputs/`：最近 200 条任务记录与输出图片；输出文件暂不自动清理。
- `preview.json`：最近一次提示词预览及有效性信息，包含 workflow revision。
- `workflow.json`：`name/source/url/graph/mapping/overrides/negative/fingerprint`，自定义图完整快照；与 AstrBot 配置分开保存。保存有独立 revision 并发检查，重新加载同名来源保留映射，失效字段提示用户修复。旧任务没有 workflow 字段时仍从输出 222 恢复。

上传限制为 20 MB、2400 万像素；内置输出请求限制为 160 万像素，自定义图仅按节点元数据验证，管理员负责检查分辨率和批量数。HTTP 响应按块读取至 EOF 并累计大小，不能将 `read(n)` 的单次返回视为完整文件。参考图、生成图和真实人格数据不得提交到源码仓库。

## 6. 本地测试

Python 建议 3.10 或更高版本。实际使用 Python 3.12 验证；AstrBot SDK 在单元测试中使用替身，不等同于在 NAS 上加载插件。

运行测试需要 pytest、pytest-asyncio、aiohttp、Pillow、Starlette；核心类型检查需要 mypy，前端测试需要 Node.js。测试依赖不加入插件生产依赖文件。

在子项目根目录执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q -p pytest_asyncio.plugin --basetemp=.test-tmp-suite
node tests/frontend.mjs
node tests/frontend.mjs --modern
python -m compileall -q main.py models.py store.py workflow.py workflow_config.py comfy.py engine.py
python -m mypy models.py store.py workflow.py workflow_config.py comfy.py engine.py --no-site-packages --ignore-missing-imports --follow-imports=skip --strict
python -m mypy main.py models.py store.py workflow.py workflow_config.py comfy.py engine.py --no-site-packages --ignore-missing-imports --follow-imports=skip --strict --disable-error-code=misc
```

本机环境的第三方 stubs 存在解析冲突，因此类型检查使用 `--no-site-packages`；入口检查忽略未安装 SDK 的基类与装饰器检查。此方式验证本项目逻辑，不能替代完整 SDK 类型检查。

Windows 受限环境可能阻止异步事件循环或回环模拟服务，需要在允许本地套接字的环境运行。失败时说明环境限制，不把挂起或未执行描述成通过。

真实生图需要用户授权，测试前检查队列，保存任务 ID，超时后恢复原任务。此前单张出图记录见 [实际测试报告](docs/smoke-test-20261029.md)。

## 7. 每次更新流程

1. 检查 Git 状态，区分当前任务与用户自行修改的内容。
2. 最小范围修改，并运行适合本次变更的验证。
3. 在 `CHANGELOG.md` 记录日期、原因、实际行为、验证结果和剩余限制；更新受影响的开发/使用文档。
4. 对可分发的功能或修复递增 `metadata.yaml` 版本，并同步 README 和更新日志；单纯文档更新可记录在“未发布”条目。
5. 检查 staged diff，排除真实数据、密钥、缓存与 ZIP，然后创建本地提交。
6. 检查工作区，向用户报告哈希。用户负责推送及从 GitHub 安装；不自动推送或部署。

本地 ZIP 仅为忽略的辅助产物。打包时只纳入源码、`pages/`、配置定义、元信息、必要静态资源和文档，排除 `.git/`、测试缓存、`artifacts/` 与真实持久数据。当前已将页面从 `views/` 移至 `pages/`，旧 ZIP 不能作为新版本来源；优先按用户流程从 GitHub 安装。

## 8. 尚未完成的验收

- NAS 实际加载插件、Dashboard 页面显示及 bridge 请求转发。
- NAS → 本机 ComfyUI 的网络访问。
- AstrBot 现有视觉/提示词模型调用与 NapCat/OneBot 实际发图。
- 不同人格连续场景的角色一致性与动作遵循。
- 动态工作流的实际生成与画像一致性；本次只读验证未提交生成。
- Chrome 调试未连接，双设置的真实页面视觉仍待检查。

## 9. 双设置实现验收记录（0.2.0）

实施范围见 [方案与边界](docs/workflow-configuration-plan.md)。2026-10-29 使用新客户端从本机局域网地址列出 3 个 JSON，其中 `api/Anima+本地简易反推流.json` 为 32 节点 API 图。读取对应节点定义后得到 89 个标量/可映射输入；默认映射正面 218.text、负面 214.text、种子 220.seed、参考图 398.image、输出 222，所选输出依赖链裁剪为 23 节点，通过校验。没有提交 prompt。

NAS 主机名只读检查成功，但未操作插件文件、配置或容器。63 项后端测试及新旧 bridge 前端交互检查通过。Chrome 调试未连接，本地预览服务已停止；不将模拟 DOM 测试描述为真实 Dashboard 验收。
