"""Run the real HTTP client against a local fake ComfyUI server."""

from dataclasses import replace
import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from astrbot_plugin_image_studio.engine import Engine
from astrbot_plugin_image_studio.comfy import ComfyClient
from astrbot_plugin_image_studio.models import Portrait, Settings
from astrbot_plugin_image_studio.store import Store
from test_core import picture


@pytest.mark.asyncio
async def test_http_reference_upload_submit_poll_download(tmp_path):
    submitted = []
    async def queue(request):
        return web.json_response({"queue_running":[],"queue_pending":[]})
    async def upload(request):
        reader = await request.multipart()
        image = await reader.next()
        assert image.name == "image"
        assert bytes(await image.read()) == picture()
        return web.json_response({"name":"reference.png","subfolder":"astrbot_image_studio"})
    async def submit(request):
        body = await request.json()
        submitted.append(body)
        assert body["prompt"]["398"]["inputs"]["image"] == "astrbot_image_studio/reference.png"
        return web.json_response({"prompt_id":body["prompt_id"]})
    async def result(request):
        return web.json_response({request.match_info["prompt_id"]:{"status":{"completed":True},"outputs":{"222":{"images":[{"filename":"output.png","subfolder":"","type":"output"}]}}}})
    async def view(request):
        assert request.query["filename"] == "output.png"
        return web.Response(body=picture(),content_type="image/png")
    app = web.Application()
    app.router.add_get('/queue',queue)
    app.router.add_post('/upload/image',upload)
    app.router.add_post('/prompt',submit)
    app.router.add_get('/history/{prompt_id}',result)
    app.router.add_get('/view',view)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        store = Store(tmp_path)
        portrait = Portrait("alice",image=store.save_image("portraits",picture()),appearance="blue eyes",style="ink",mode="img2img")
        engine = Engine(store,AsyncMock())
        settings = replace(Settings(),comfy_url=f"http://127.0.0.1:{port}")
        await engine.start(settings,portrait,AsyncMock(return_value="garden"))
        await engine.worker
        assert len(submitted) == 1
        assert engine.state()["active"] is None
        record = store.read("history.json")["items"][0]
        assert record["stage"] == "completed"
        assert store.path("outputs",record["file"]).read_bytes() == picture()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_download_waits_for_all_network_chunks():
    expected = b"a" * 100000 + b"b" * 100000
    async def streamed(request):
        response = web.StreamResponse(headers={"Content-Type":"image/png"})
        await response.prepare(request)
        await response.write(expected[:1000])
        await asyncio.sleep(0.02)
        await response.write(expected[1000:])
        await response.write_eof()
        return response
    app = web.Application()
    app.router.add_get('/view',streamed)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner,'127.0.0.1',0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]
        data = await ComfyClient(f'http://127.0.0.1:{port}').download({"filename":"a.png"})
        assert data == expected
    finally:
        await runner.cleanup()
