# 开发文档

本文描述当前实现与维护流程。使用步骤见 [README](README.md)，逐次更新记录见 [CHANGELOG](CHANGELOG.md)。

## 1. 范围与兼容基线

插件运行于 NAS 的 AstrBot，调用用户本机的 ComfyUI。当前仅实现固定 Anima 文生图/参考图重绘；通用工作流上传和节点映射属于后续功能。

已确认 Dashboard 显示 AstrBot `4.28.2`，本机 ComfyUI 为 `0.30.2`。后端使用 `astrbot.api.web`、`context.llm_generate`、`persona_manager.resolve_selected_persona` 等接口，NAS 实际加载与完整链路仍需验收。`metadata.yaml` 的最低版本声明不是对所有中间发行版的验证承诺。

AstrBot `4.28.2` 只扫描 `pages/<页面名>/index.html`，桥接对象为 `AstrBotPluginPage`。页面采用 `pages/studio/`，脚本同时兼容较新版桥接名称 `AstrBotPluginView`；标题使用 `.astrbot-plugin/i18n/zh-CN.json` 的 `pages.studio`。

## 2. 文件职责

| 路径 | 职责 |
| --- | --- |
| `main.py` | AstrBot 生命周期、指令、LLM 工具、人格解析、提示词调用与管理 API |
| `models.py` | 配置和画像的数据结构、字段与数值验证 |
| `engine.py` | 全局互斥、后台任务、恢复查询、发送状态与冷却 |
| `comfy.py` | ComfyUI 上传、提交、轮询、定位任务与有大小限制的分块下载 |
| `workflow.py` | 运行时构建 Anima API 工作流 |
| `workflows/anima_api.json` | 当前文生图 API 示例，不是运行时加载的工作流文件 |
| `store.py` | 原子 JSON 写入、画像与结果图片校验和历史记录 |
| `pages/studio/` | 五个页签共用的 HTML、CSS 和前端逻辑 |
| `_conf_schema.json` | AstrBot 配置定义，默认值须与 `Settings` 一致 |
| `metadata.yaml` | 插件元信息与版本 |
| `tests/` | 后端、HTTP 分块响应、SDK 替身和前端交互测试 |
| `docs/` | 工作流调查及实际出图测试记录 |

## 3. 运行链路

1. 手动指令或工具调用使用 AstrBot 的统一人格解析器，解析会话规则、当前对话与默认人格。明确取消人格时不借用其他画像。
2. 读取对应画像，核对人格内容哈希。人格变化后要求管理员重新确认画像。
3. `Engine.start` 原子占用任务，检查冷却。查询 ComfyUI 队列，已有任务时拒绝本次请求。
4. LLM 根据人格、档案、画面需求和当前消息生成动态画面描述；插件组合固定外观和画风。内部请求不提供生图工具，避免递归。
5. 图生图模式先上传画像，构建 API 图；再次检查队列后提交一次，持久化任务 ID。
6. 轮询原任务并下载保存图片。聊天任务发送至原会话；网页任务在生成记录中查看。
7. 结束后保存历史并进入冷却。异常状态保留原任务，恢复时查询，不盲目重新提交。

网页画像解析和提示词预览也使用互斥入口。预览生成的提示词通过短期 token 确认；人格、画像、设置或画面需求变化后失效，避免确认时使用另一份提示词。

## 4. 页面与后端接口

前端通过 Dashboard 注入的 bridge 调用接口，不直接读取 Dashboard Token，不另开 Web 服务。`main.py` 注册 `/{插件名}/<path:action>`，所有管理请求要求 Dashboard 登录身份。

| 方法 / action | 用途 |
| --- | --- |
| `GET state` | 配置及版本指纹、人格和提供商列表、画像、任务与历史 |
| `GET thumbnail` / `download` | 安全目录内的缩略图和文件下载 |
| `POST settings` | 校验配置及并发版本，保存至 AstrBot 配置 |
| `POST connection` | 查询 ComfyUI `/system_stats`，不提交生成 |
| `POST upload` | 上传参考图，返回服务器生成的文件名 |
| `POST portrait` | 保存与确认指定人格画像 |
| `POST analyze` | 使用已配置的视觉模型解析例图 |
| `POST preview` / `generate` | 预览提示词或受理实际生图 |
| `POST recover` | 查询/恢复原任务 |
| `POST release` | 显式确认后解除未知任务，要求 ComfyUI 队列空闲 |

前端状态刷新不得覆盖用户正在编辑的表单，也不能将轮询返回的新配置指纹用于提交旧表单。错误信息与模型输出按文本呈现。

页面配置有模型、尺寸与采样参数；这不表示可读取 ComfyUI 编辑器中的当前工作流。修改页面字段时同步维护 schema、数据结构、验证、文档和相关测试。

## 5. 工作流与存储约束

运行时图保留 Anima 模型与采样链，正面文本输入节点 218，负面输入节点 214，输出节点固定为 222。原始前端工作流中的反推、TIPO 与透传节点不参与提交。

文生图使用空 latent；参考图重绘使用 LoadImage → 居中裁剪缩放 → VAEEncode。原图姿势与构图可能保留；当前不具有专门的角色身份锁定节点。

ComfyUI 队列检查不能阻止用户稍后手动提交任务；本插件只约束自己的入口，不取消、清空或重排其他任务。

持久数据位于 AstrBot 的 `data/plugin_data/astrbot_plugin_image_studio/`：

- `portraits.json` / `portraits/`：人格画像档案及图片。
- `state.json`：活动任务、配置快照与冷却截止时间。
- `history.json` / `outputs/`：最近 200 条任务记录与输出图片；输出文件暂不自动清理。
- `preview.json`：最近一次提示词预览及有效性信息。

上传限制为 20 MB、2400 万像素；输出请求限制为 160 万像素。HTTP 响应按块读取至 EOF 并累计大小，不能将 `read(n)` 的单次返回视为完整文件。参考图、生成图和真实人格数据不得提交到源码仓库。

## 6. 本地测试

Python 建议 3.10 或更高版本。实际使用 Python 3.12 验证；AstrBot SDK 在单元测试中使用替身，不等同于在 NAS 上加载插件。

运行测试需要 pytest、pytest-asyncio、aiohttp、Pillow、Starlette；核心类型检查需要 mypy，前端测试需要 Node.js。测试依赖不加入插件生产依赖文件。

在子项目根目录执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q -p pytest_asyncio.plugin --basetemp=.test-tmp-suite
node tests/frontend.mjs
node tests/frontend.mjs --modern
python -m compileall -q main.py models.py store.py workflow.py comfy.py engine.py
python -m mypy models.py store.py workflow.py comfy.py engine.py --no-site-packages --ignore-missing-imports --follow-imports=skip --strict
python -m mypy main.py models.py store.py workflow.py comfy.py engine.py --no-site-packages --ignore-missing-imports --follow-imports=skip --strict --disable-error-code=misc
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
- API 工作流导入及节点映射尚未实现。

## 9. 待确认的设置流程扩展

用户提出按连接检查、工作流发现/选择、节点信息加载和动态参数配置组织 ComfyUI 设置，并将共享 LLM 配置与独立人格画像集中到另一设置区域。可行性检查与实施边界见 [工作流驱动设置方案](docs/workflow-configuration-plan.md)。

已只读验证当前 ComfyUI 能列出并读取保存的工作流；现有 Anima 文件是编辑器格式，需要配对 API 格式 JSON 后才能可靠提交。方案待确认，当前运行逻辑尚未变更。
