"""ComfyUI HTTP client; never retries prompt submission."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp

from .models import Json, Object, object_value


class ComfyError(RuntimeError):
    pass


class SubmissionRejected(ComfyError):
    pass


class ComfyUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("暂时无法连接本地 ComfyUI，本次未生成图片。请确认生图电脑和 ComfyUI 已启动，并检查 NAS 到 ComfyUI 的网络连接。")


async def read_limited(response: aiohttp.ClientResponse, limit: int) -> bytes:
    """Read to EOF while enforcing a bound; read(n) can return partial chunks."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(65536):
        if not isinstance(chunk, bytes):
            raise ComfyError("响应格式错误")
        total += len(chunk)
        if total > limit:
            raise ComfyError("ComfyUI 响应超过大小限制")
        chunks.append(chunk)
    return b"".join(chunks)


class ComfyClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def request(self, method: str, endpoint: str, *, payload: Object | None = None) -> Object:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as session:
            async with session.request(method, self.base_url + endpoint, json=payload, allow_redirects=False) as response:
                body = await read_limited(response, 4 * 1024 * 1024)
                if response.status >= 400:
                    if endpoint == "/prompt" and 400 <= response.status < 500:
                        raise SubmissionRejected(f"工作流被拒绝（HTTP {response.status}），请检查模型和节点")
                    raise ComfyError(f"ComfyUI 返回 HTTP {response.status}")
                if not 200 <= response.status < 300:
                    raise ComfyError("ComfyUI 不允许重定向")
                return object_value(json.loads(body))

    async def idle(self) -> bool:
        queue = await self.request("GET", "/queue")
        if not isinstance(queue.get("queue_running"), list) or not isinstance(queue.get("queue_pending"), list):
            raise ComfyError("ComfyUI 队列格式异常")
        return not queue["queue_running"] and not queue["queue_pending"]

    async def check_connection(self) -> bool:
        """Validate the live queue API before accepting work or calling an LLM."""
        try:
            return await asyncio.wait_for(self.idle(), timeout=5)
        except (aiohttp.ClientError, OSError, TimeoutError, ComfyError, ValueError) as error:
            raise ComfyUnavailable() from error

    async def upload(self, path: Path) -> str:
        form = aiohttp.FormData()
        form.add_field("image", path.read_bytes(), filename=path.name, content_type="image/png")
        form.add_field("subfolder", "astrbot_image_studio")
        form.add_field("overwrite", "false")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as session:
            async with session.post(self.base_url + "/upload/image", data=form, allow_redirects=False) as response:
                if response.status != 200:
                    raise ComfyError("ComfyUI 参考图上传失败")
                data = object_value(await response.json())
                name, folder = data.get("name"), data.get("subfolder", "")
                if not isinstance(name, str) or not isinstance(folder, str):
                    raise ComfyError("ComfyUI 上传响应异常")
                return f"{folder}/{name}" if folder else name

    async def submit(self, graph: Object, prompt_id: str) -> str:
        data = await self.request("POST", "/prompt", payload={"prompt": graph, "prompt_id": prompt_id, "client_id": prompt_id})
        returned = data.get("prompt_id")
        if not isinstance(returned, str) or not returned:
            raise ComfyError("ComfyUI 未返回任务 ID，请检查任务状态")
        return returned

    async def result(self, prompt_id: str) -> Object | None:
        from urllib.parse import quote
        data = await self.request("GET", "/history/" + quote(prompt_id, safe=""))
        value = data.get(prompt_id)
        if value is None:
            return None
        return object_value(value)

    async def download(self, image: Object) -> bytes:
        params: dict[str, str] = {}
        for key in ("filename", "subfolder", "type"):
            value: Json = image.get(key, "output" if key == "type" else "")
            if not isinstance(value, str):
                raise ComfyError("ComfyUI 图片信息异常")
            params[key] = value
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), trust_env=False) as session:
            async with session.get(self.base_url + "/view", params=params, allow_redirects=False) as response:
                if response.status != 200:
                    raise ComfyError("ComfyUI 结果下载失败")
                return await read_limited(response, 20 * 1024 * 1024)

    async def locate(self, client_id: str) -> str | None:
        """Find a submission whose response was lost, including older servers."""
        queue = await self.request("GET", "/queue")
        for key in ("queue_running", "queue_pending"):
            items = queue.get(key, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, list) and len(item) > 3 and isinstance(item[3], dict):
                        if item[3].get("client_id") == client_id and isinstance(item[1], str):
                            return item[1]
        history = await self.request("GET", "/history?max_items=100")
        for prompt_id, entry in history.items():
            if isinstance(entry, dict):
                prompt = entry.get("prompt")
                if isinstance(prompt, list) and len(prompt) > 3 and isinstance(prompt[3], dict):
                    if prompt[3].get("client_id") == client_id:
                        return prompt_id
        return None
