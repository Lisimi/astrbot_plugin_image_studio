# AstrBot 生图工作室

当前版本：0.1.1。2026-10-29 已完成本地实现、33 项后端测试及一张本地 ComfyUI 实际出图。尚未部署 NAS，也未调用 AstrBot 配置的 LLM。详情见 [实际出图测试](docs/smoke-test-20261029.md)。

项目维护文档：[开发文档](DEVELOPMENT.md) · [更新日志](CHANGELOG.md) · [开发约定](AGENTS.md)。后续每次更新同步记录并创建本地提交，默认由用户推送和安装。

## 首版范围

以本机 ComfyUI 的 Anima 工作流为唯一生图后端，参考 DSH 生图工作室的交互思路和表情包管理器的 AstrBot 页面接入方式。当前不实现 65535 或远程 OpenAI 图片接口。

- `/画图 <画面描述>` 手动调用，`image_generate` 供对话模型自主调用。
- 画像绑定 AstrBot 实际人格 ID，支持例图、外观、画风、默认服饰与神态习惯。
- 看图 LLM 从例图和人格提取档案，提示词 LLM 结合人格及当前消息生成英文视觉描述，固定外观与画风由插件组合。
- 管理网页提供人格画像、提示词与规则、试生成、生成记录、设置五个页面。
- 所有账号、会话、人格和网页共用一个任务，完成后全局冷却默认 300 秒，可配置。
- 持久化任务 ID、图片与冷却；结果不明时保留占用，恢复时查询原任务，不自动重复提交。

LLM 使用 AstrBot 已配置的提供商；其推理费用取决于所选服务。本插件不新增模型账户或 API Key。

## 工作流

原始工作流及环境调查见 [ComfyUI 工作流记录](docs/comfyui-research.md)。原始 JSON 与本机配置均未修改。

新的 [API 工作流示例](workflows/anima_api.json) 来自原流程的模型与采样链；运行时由 `workflow.py` 填入参数：

- UNet：`anima_baseV10.safetensors`。
- 文本编码器：`qwen_3_06b_base.safetensors`，类型 `qwen_image`。
- VAE：`qwen_image_vae.safetensors`。
- 采样：36 步、CFG 4、`euler`、`simple`，每次新种子，单张输出。
- 默认 888 × 1184，约为原流程的 1 MP、3:4 比例，尺寸可调。
- 正面文本直接进入节点 218，绕过 PixAI、TIPO 和前端透传节点，避免重复扩写。

两种模式可按人格选择：

| 模式 | 参考图作用 |
| --- | --- |
| 文生图（默认） | 例图由看图模型解析为档案，Anima 接收文字特征，动作与构图更自由 |
| 参考图重绘 | 画像上传到 ComfyUI，居中裁剪至目标尺寸后经 VAE 编码，默认降噪 0.55；可能保留原图姿势与构图 |

当前工作流没有专门锁定人物身份的图像条件节点。两种方式都需用实际画像检查角色和画风一致性，不能仅凭接口接通判断达标。

## NAS 联调前提

此前只读验证的环境：

- NAS：`192.168.0.105`，容器 `astrbot`，宿主机插件目录 `/vol1/1000/Docker/astrbot/data/plugins`。
- 本机以太网地址：`192.168.0.203`，ComfyUI `0.30.2`，端口 8188。
- 2026-10-29 用户已改为 `--listen 0.0.0.0`，本机访问局域网地址成功。NAS 到本机的访问仍待单独验证。

本地已验证节点、模型与必要参数，并通过插件核心流程生成、下载一张图片；没有重新提交同一任务。

AstrBot 必须支持 Plugin Views、`astrbot.api.web`、`context.llm_generate` 和 `persona_manager.resolve_selected_persona`。Dashboard 已确认运行版本 `4.28.2`，该版本的官方源码具有公共 Web API 与统一人格解析接口；实际插件加载仍待 NAS 联调。缺少统一人格解析接口时插件会明确报错，避免绑定错人格。

## 安装后设置步骤

以下步骤在下一阶段完成 NAS 部署后使用：

1. 打开 AstrBot 插件页面/插件详情中的“生图工作室”。沿用 Dashboard 登录，不增加独立端口。
2. 在“设置”填写 NAS 可访问的 ComfyUI 地址。预填 `http://192.168.0.203:8188` 仅为已知内网地址，需先完成监听和连通验证。
3. 选择 AstrBot 中已配置的画像解析模型（需支持图片输入）与提示词模型。聊天触发可跟随会话模型，网页试生成需明确选择提示词模型。
4. 保存配置，再测试已保存地址的连接。该操作只查询 `/system_stats`，不生图。
5. 在“人格画像”选择人格并上传例图，点击解析；检查修正外观、画风和习惯后保存。也可手工填写档案；人格变化后必须重新保存确认。
6. 选择文生图或参考图重绘。需要自动发图时打开该人格的开关，并在 AstrBot 中允许该人格使用 `image_generate` 工具。
7. 在“试生成”输入画面需求，可先预览提示词再实际生成。预览有效 10 分钟，确认生成复用已预览文本；修改画面、画像或配置后预览失效。
8. 生成完成后网页中查看结果；聊天触发则发送到原会话。NapCat/OneBot 的实际发送仍需验证，暂不承诺所有平台适配器都支持后台发送。

上传图片最多 20 MB、2400 万像素；生成尺寸为 8 的倍数，最多 160 万像素，避免意外请求大图。配置通过 `_conf_schema.json` 定义，网页与普通配置入口共用 AstrBot 配置。

画像、输出图、历史与任务状态保存在容器 `data/plugin_data/astrbot_plugin_image_studio/`，不混入源码。历史最多显示/保留最近 200 条记录，网页一次显示最近 24 条；生成图片文件目前不自动清理。

## 限流和故障处理

- 手动、自动及网页生图共用限制，模型无法通过参数绕过。忙碌或冷却时拒绝，不排长队。
- 提交前检查 ComfyUI 队列；本机已有任务时不调用提示词 LLM 或继续堆积。检查队列不能阻止用户之后在 ComfyUI 手动提交任务。
- 生图全程占用任务；例图解析和提示词预览也使用同一互斥入口，避免连点模型调用。
- 已受理任务失败后进入冷却；检测到 ComfyUI 已忙、尚未提交的任务不重置冷却。
- 超时、断网或重载不会自动重新提交。使用“查询/恢复原任务”跟踪保存的 ID；兼容通过 client_id 查找丢失提交响应的旧服务任务。
- 结果未知时暂停受理新任务。核查 ComfyUI 队列与历史后，可手动“确认解除未知任务”；队列不为空时禁止解除。
- 图片已生成但发送失败时保留文件，从生成记录下载，不重新生图。发送结果因重载而不明时不自动重复发送。
- 配置与画像变更对后续任务生效，已受理任务使用快照。配置页有并发修改检测。

## 本地验证

后端测试需要 pytest、pytest-asyncio、aiohttp、Pillow、Starlette；AstrBot SDK 通过测试替身隔离。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q -p pytest_asyncio.plugin --basetemp=.test-tmp-suite
node tests/frontend.mjs
```

33 项后端测试覆盖并发、冷却、人格解析与隔离、档案失效、预览确认、重载恢复、路径及图片校验、配置冲突，以及模拟 ComfyUI 的 HTTP 上传/提交/轮询/下载全流程，包含延迟分块下载回归测试。自动化测试未调用真实模型；另已完成一张实际出图。

核心模块通过严格 mypy 检查；入口适配层在未安装 SDK 的本机环境下额外忽略 SDK 基类/装饰器相关检查。Python 编译和前端脚本语法检查通过。

管理页面通过模拟 DOM/bridge 交互测试；浏览器验证通道不可用，尚未验证真实 Dashboard 页面显示、登录转发与移动端视觉效果。

### 页面入口兼容性修复

AstrBot 4.28.2 的源码仅发现 `pages/<页面名>/index.html`，桥接对象为 `window.AstrBotPluginPage`。初版误用新版文档的 `views/` 和 `AstrBotPluginView`，导致已实现的页面无法被该版本发现。现已改为 `pages/studio/`，并兼容两种桥接名称；页面标题元信息使用 `pages.studio`。

更新插件并重载后，在插件详情中打开“生图工作室”；侧栏未更新时刷新 Dashboard。该修复尚未在 NAS 上完成实际页面验收。

### 更新 ComfyUI 工作流的影响

当前生成流程由 `workflow.py` 构建，`workflows/anima_api.json` 只是对应的 API 格式示例，不会读取 ComfyUI 编辑器中当前打开或保存的工作流文件。设置中的模型名称、尺寸、步数和 CFG 只控制内置 Anima 流程。

- 仅编辑 ComfyUI 中的工作流：插件仍运行自己的内置流程，修改不会自动同步。
- 模型文件更名或采样参数调整：可在插件设置中修改已提供的字段。
- 新增 LoRA/ControlNet、替换节点或切换模型架构：当前版本需要修改工作流构建代码，暂不支持网页导入任意工作流。

后续若需要独立维护工作流，可增加“导入 API 格式 JSON + 映射正面提示词、参考图、种子和输出节点”的功能；节点变化时重新映射和校验，不承诺任意工作流自动兼容。该扩展尚未实现。

## 下一阶段

按用户要求，本地实现完成后确认再部署。下一阶段需要：确认 NAS AstrBot 版本、验证页面与 SDK、调整 ComfyUI 内网访问、选择现有 LLM、配置至少两个人格例图并进行真实出图验收。

## 参考

- [DSH 生图工作室](https://github.com/aa1355721445-lgtm/dsh-image-studio)
- [AstrBot 表情包管理器](https://github.com/anka-afk/astrbot_plugin_meme_manager)
- [AstrBot 插件可视化视图](https://docs.astrbot.app/dev/star/guides/plugin-pages.html)
- [AstrBot AI 接口](https://docs.astrbot.app/dev/star/guides/ai.html)

