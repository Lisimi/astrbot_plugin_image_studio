# ComfyUI 工作流调查记录

状态：2026-10-29 用户决定首版专注本地 ComfyUI。本文件保留 2026-10-28 的环境调查结果，当前实现与验证情况以项目 README 为准。

## 当时的环境

- 本机地址：`192.168.0.203`，NAS 地址：`192.168.0.105`。
- ComfyUI `0.30.2`，Python `3.13.11`，RTX 4070 Ti SUPER，约 16 GB 显存。
- ComfyUI 当时仅监听 `127.0.0.1:8188`，NAS 无法直接访问。
- 已只读验证 `/system_stats`、关键节点 `/object_info` 和 `/queue`；未提交生图任务，未验证 NAS 到本机的 HTTP 请求。
- 未修改启动参数、防火墙、模型文件或原工作流。恢复本地接入工作时须重新检查状态。

## 原工作流

路径：`G:/ComfyUI-aki/ComfyUI-aki-v3.2/ComfyUI/user/default/workflows/Anima+本地简易反推流.json`。

这是前端工作流格式，不能原样提交给 `/prompt`，需要对应的 API 工作流。

| 节点 | 参数或作用 |
| --- | --- |
| `206 UNETLoader` | `anima_baseV10.safetensors` |
| `207 CLIPLoader` | `qwen_3_06b_base.safetensors`，类型 `qwen_image` |
| `205 VAELoader` | `qwen_image_vae.safetensors` |
| `398 LoadImage` | 参考图入口 |
| `416 PixAITaggerPlus` | 本地反推标签，相关模型已安装 |
| `400 TIPO` | 本地提示词扩写 |
| `218 / 214 CLIPTextEncode` | 正面 / 负面提示词 |
| `209 PrimitiveBoolean` | 当时为 `true`，选择文生图 |
| `211 easy float` | 图生图降噪 `0.55` |
| `220 KSampler` | 36 步、CFG 4、`euler`、`simple` |
| `215 RepeatLatentBatch` | 1 张 |
| `222 SaveImage` | 输出图像 |

## 扩展时需解决的问题

- NAS 不能使用本机 `G:` 路径；参考图经 `/upload/image` 上传，提交后以 `prompt_id` 查询 `/history/{prompt_id}`，通过 `/view` 下载。
- 需要调整监听方式并确认 NAS 可达。若修改 Windows 防火墙，限制来源为 NAS；操作前确认本机是否有运行中的任务。
- 当前工作流依赖反推标签，也有普通图生图分支；未发现专门保持人物身份的图像条件节点。存在 `IPAdapter_plus` 节点目录不代表存在兼容 Anima 的身份保持方案。
- LLM 已生成最终提示词时，应绕过 PixAI → TIPO → 文本透传链，将文本直接送入正面编码节点，避免再次扩写。
- 角色一致性须用实际画像验收；不能仅凭接口接通判定达标。
- 复用首版的生成任务和限流入口，额外处理本地队列忙碌、断网与未知任务状态，避免重复提交。

调查依据：本机 `ComfyUI/server.py`、`comfy/cli_args.py`、原工作流、自定义节点源码及当时运行的 `/object_info`。
