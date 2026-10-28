"""One durable generation gate shared by chat and the management page."""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiohttp import ClientError

from .comfy import ComfyClient, ComfyError, ComfyUnavailable, SubmissionRejected
from .models import Object, Portrait, Settings, object_value, string
from .store import Store
from .workflow import build_workflow

T = TypeVar("T")
_gates: dict[str, asyncio.Lock] = {}
_live: set[str] = set()
Prepare = Callable[[], Awaitable[str]]
Deliver = Callable[[Object], Awaitable[None]]


class Engine:
    def __init__(self, store: Store, deliver: Deliver):
        self.store = store
        self.deliver = deliver
        self.gate = _gates.setdefault(str(store.root.resolve()), asyncio.Lock())
        self.worker: asyncio.Task[None] | None = None

    def state(self) -> Object:
        return self.store.read("state.json", {"active": None, "cooldown_until": 0})

    def check(self) -> Object:
        state = self.state()
        if state.get("active"):
            raise ValueError("正在处理任务或等待确认，请查看生图状态")
        until = state.get("cooldown_until", 0)
        if isinstance(until, (int, float)) and until > time.time():
            raise ValueError(f"全局冷却中，剩余 {int(until - time.time()) + 1} 秒")
        return state

    async def exclusive(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self.gate:
            state = self.check()
            state["active"] = {"stage": "utility", "id": str(uuid.uuid4())}
            self.store.write("state.json", state)
            active_id = string(object_value(state["active"])["id"])
            _live.add(active_id)
        try:
            return await operation()
        finally:
            async with self.gate:
                state["active"] = None
                self.store.write("state.json", state)
                _live.discard(active_id)

    async def start(self, settings: Settings, portrait: Portrait, prepare: Prepare, origin: str = "") -> str:
        if not portrait.appearance or not portrait.style:
            raise ValueError("请先保存画像的外观与画风")
        if portrait.mode == "img2img":
            self.store.path("portraits", portrait.image)
        async with self.gate:
            state = self.check()
            if not await ComfyClient(settings.comfy_url).check_connection():
                raise ValueError("ComfyUI 已有其他任务，本次没有提交")
            job: Object = {"id": str(uuid.uuid4()), "stage": "preparing", "started": time.time(),
                           "persona_id": portrait.persona_id, "origin": origin,
                           "settings": settings.to_dict(), "portrait": portrait.to_dict(),
                           "seed": secrets.randbelow(2**50), "prompt_id": "", "prompt": ""}
            state["active"] = job
            self.store.write("state.json", state)
            _live.add(string(job["id"]))
            self.worker = asyncio.create_task(self.run(job, prepare))
            return string(job["id"])

    def update(self, job: Object, **changes: object) -> None:
        job.update(object_value(changes))
        state = self.state()
        state["active"] = job
        self.store.write("state.json", state)
        self.store.record(job)

    def finish(self, job: Object, stage: str, error: str = "") -> None:
        settings = Settings.read(object_value(job["settings"]))
        job.update(stage=stage, error=error, finished=time.time())
        self.store.record(job)
        cooldown = 0 if stage == "rejected" else time.time() + settings.cooldown_seconds
        self.store.write("state.json", {"active": None, "cooldown_until": cooldown})

    async def run(self, job: Object, prepare: Prepare | None = None) -> None:
        settings = Settings.read(object_value(job["settings"]))
        client = ComfyClient(settings.comfy_url)
        try:
            if prepare:
                if not await client.idle():
                    self.finish(job, "rejected", "ComfyUI 已有其他任务，本次没有提交")
                    return
                prompt = await prepare()
                portrait = Portrait.read(object_value(job["portrait"]))
                image = ""
                if portrait.mode == "img2img":
                    image = await client.upload(self.store.path("portraits", portrait.image))
                if not await client.idle():
                    self.finish(job, "rejected", "ComfyUI 正忙，本次没有提交")
                    return
                seed = job["seed"]
                assert isinstance(seed, int)
                graph = build_workflow(settings, portrait, prompt, seed, image)
                prompt_id = str(uuid.uuid4())
                self.update(job, stage="submitting", prompt=prompt, prompt_id=prompt_id)
                returned_id = await client.submit(graph, prompt_id)
                self.update(job, stage="running", prompt_id=returned_id)
            elif job.get("stage") in ("submitting", "unknown"):
                located = await client.locate(string(job["prompt_id"]))
                if located:
                    self.update(job, prompt_id=located, stage="running")
            deadline = time.monotonic() + settings.task_timeout
            while time.monotonic() < deadline:
                result = await client.result(string(job["prompt_id"]))
                if result:
                    status = object_value(result.get("status", {}))
                    if status.get("status_str") == "error":
                        self.finish(job, "failed", "ComfyUI 执行失败，请查看 ComfyUI 日志")
                        return
                    outputs = object_value(result.get("outputs", {}))
                    output = object_value(outputs.get("222", {}))
                    images = output.get("images", [])
                    if isinstance(images, list) and images:
                        data = await client.download(object_value(images[0]))
                        filename = self.store.save_image("outputs", data)
                        self.update(job, stage="downloaded", file=filename)
                        await self.send(job)
                        return
                    if status.get("completed") is True:
                        self.finish(job, "failed", "任务已结束但保存节点没有返回图片")
                        return
                await asyncio.sleep(2)
            self.update(job, stage="unknown", error="等待超时，已保留任务 ID；请查询原任务，勿重复提交")
        except SubmissionRejected as error:
            self.finish(job, "failed", str(error))
        except (ClientError, OSError, TimeoutError, ComfyError, ComfyUnavailable):
            if job.get("prompt_id"):
                self.update(job, stage="unknown", error="ComfyUI 连接中断或响应异常，无法确认任务结果；原任务可能仍在运行，请恢复查询，不要重复生图。")
            else:
                self.finish(job, "failed", str(ComfyUnavailable()))
        except asyncio.CancelledError:
            if job.get("stage") == "sending":
                self.finish(job, "send_unknown", "发送结果未知，图片已保存，请从记录查看")
            elif job.get("prompt_id"):
                self.update(job, stage="unknown", error="插件重载，等待恢复原任务")
            else:
                self.finish(job, "failed", "插件重载，任务尚未提交")
            raise
        except Exception as error:
            message = f"{type(error).__name__}：连接或处理失败，请检查服务和日志"
            if job.get("prompt_id"):
                self.update(job, stage="unknown", error=message)
            else:
                self.finish(job, "failed", message)
        finally:
            _live.discard(string(job["id"]))
            if job.get("origin") and job.get("stage") in ("failed", "rejected", "unknown"):
                try:
                    await self.deliver(job)
                except Exception:
                    pass

    async def send(self, job: Object) -> None:
        if not job.get("origin"):
            self.finish(job, "completed")
            return
        self.update(job, stage="sending")
        _live.add(string(job["id"]))
        try:
            await self.deliver(job)
        except Exception:
            self.finish(job, "send_failed", "图片已保存，发送失败；请从生成记录下载")
        else:
            self.finish(job, "completed")
        finally:
            _live.discard(string(job["id"]))

    async def recover(self) -> None:
        async with self.gate:
            if self.worker and not self.worker.done():
                raise ValueError("任务仍在运行")
            active = self.state().get("active")
            if not active:
                return
            job = object_value(active)
            if job.get("id") in _live:
                raise ValueError("任务仍在处理")
            if job.get("stage") in ("utility", "preparing"):
                if job.get("stage") == "utility":
                    state = self.state()
                    state["active"] = None
                    self.store.write("state.json", state)
                else:
                    self.finish(job, "failed", "上次任务未提交，已结束")
            elif job.get("stage") == "sending":
                self.finish(job, "send_unknown", "上次发送结果未知，图片已保存；为避免重复发送，请从记录查看")
            elif job.get("file"):
                self.worker = asyncio.create_task(self.send(job))
            else:
                _live.add(string(job["id"]))
                self.worker = asyncio.create_task(self.run(job))

    async def release_unknown(self) -> None:
        async with self.gate:
            if self.worker and not self.worker.done():
                raise ValueError("请等待当前查询结束")
            active = self.state().get("active")
            if not active:
                return
            job = object_value(active)
            if job.get("stage") != "unknown":
                raise ValueError("仅可解除结果未知的任务")
            settings = Settings.read(object_value(job["settings"]))
            if not await ComfyClient(settings.comfy_url).idle():
                raise ValueError("ComfyUI 队列仍有任务，不能解除")
            self.finish(job, "released", "管理员确认解除；未重新提交或中断 ComfyUI 任务")

    async def close(self) -> None:
        if self.worker and not self.worker.done():
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass
