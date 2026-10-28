"""Exercise graph changes without assuming Anima node IDs or live models."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web

from astrbot_plugin_image_studio.comfy import ComfyClient
from astrbot_plugin_image_studio.engine import Engine
from astrbot_plugin_image_studio.models import Portrait, Settings
from astrbot_plugin_image_studio.store import Store
from astrbot_plugin_image_studio.workflow_config import api_graph, inspect_graph, render_workflow, validate_workflow
from test_core import picture


@pytest.fixture
def workflow_data():
    graph = {
        "pos": {"class_type": "Encode", "inputs": {"text": "original"}},
        "neg": {"class_type": "Encode", "inputs": {"text": "bad"}},
        "render": {"class_type": "Render", "inputs": {"positive": ["pos", 0], "negative": ["neg", 0], "seed": 1, "width": 888, "model": "local"}},
        "save": {"class_type": "SaveImage", "inputs": {"images": ["render", 0]}},
        "unused": {"class_type": "MissingOutput", "inputs": {}},
    }
    info = {
        "Encode": {"input": {"required": {"text": ["STRING", {}]}}, "output": ["CONDITIONING"]},
        "Render": {"input": {"required": {"positive": ["CONDITIONING", {}], "negative": ["CONDITIONING", {}], "seed": ["INT", {"min": 0, "max": 7}], "width": ["INT", {"min": 256, "max": 1536}], "model": ["COMBO", {"options": ["local", "other"]}]}}, "output": ["IMAGE"]},
        "SaveImage": {"input": {"required": {"images": ["IMAGE", {}]}}, "output": []},
    }
    config = {"name": "example.json", "url": Settings().comfy_url, "source": "api/example.json", "graph": graph,
              "mapping": {"positive": ["pos", "text"], "negative": ["neg", "text"], "seed": ["render", "seed"], "output": "save"},
              "overrides": [{"node": "render", "field": "width", "value": 896}], "negative": "worst"}
    return config, info


@pytest.mark.parametrize("suffix", ["", "-new-id"])
def test_graph_bindings_prune_unused_outputs_and_keep_original(workflow_data, suffix):
    config, info = workflow_data
    if suffix:
        config["graph"] = {key + suffix: {**node, "inputs": {field: [value[0] + suffix, value[1]] if isinstance(value, list) else value for field, value in node["inputs"].items()}} for key, node in config["graph"].items()}
        config["mapping"] = {role: [value[0] + suffix, value[1]] if isinstance(value, list) else value + suffix for role, value in config["mapping"].items()}
        config["overrides"][0]["node"] += suffix
    before = deepcopy(config)
    validate_workflow(config, info)
    rendered = render_workflow(config, "new scene", 5, "fallback")
    assert rendered["pos" + suffix]["inputs"]["text"] == "new scene"
    assert rendered["neg" + suffix]["inputs"]["text"] == "worst"
    assert rendered["render" + suffix]["inputs"]["width"] == 896
    assert rendered["render" + suffix]["inputs"]["seed"] == 5
    assert "unused" + suffix not in rendered
    assert config == before


def test_graph_format_and_live_metadata(workflow_data):
    config, info = workflow_data
    with pytest.raises(ValueError, match="编辑器"):
        api_graph({"nodes": [], "links": []})
    fields = inspect_graph(config["graph"], info)["fields"]
    combo = next(item for item in fields if item["field"] == "model")
    assert combo["type"] == "ENUM" and combo["choices"] == ["local", "other"]
    del info["Render"]
    with pytest.raises(ValueError):
        validate_workflow(config, info)


@pytest.mark.parametrize("change", ["missing-field", "linked-override", "duplicate", "bad-model", "bad-number", "wrong-type", "dead-binding", "cycle", "remote"])
def test_reject_changed_or_invalid_configuration(workflow_data, change):
    config, info = workflow_data
    if change == "missing-field":
        del config["graph"]["pos"]["inputs"]["text"]
    elif change == "linked-override":
        config["overrides"].append({"node": "render", "field": "positive", "value": "x"})
    elif change == "duplicate":
        config["mapping"]["negative"] = config["mapping"]["positive"]
    elif change == "bad-model":
        config["graph"]["render"]["inputs"]["model"] = "removed-model"
    elif change == "bad-number":
        config["overrides"][0]["value"] = 9999
    elif change == "wrong-type":
        config["mapping"]["positive"] = ["render", "width"]
    elif change == "dead-binding":
        config["graph"]["dead"] = {"class_type": "Encode", "inputs": {"text": "dead"}}
        config["mapping"]["positive"] = ["dead", "text"]
    elif change == "cycle":
        config["graph"]["render"]["inputs"]["positive"] = ["save", 0]
    else:
        info["Render"]["api_node"] = True
    with pytest.raises(ValueError):
        validate_workflow(config, info)


def test_explicit_prompt_mapping_can_replace_a_connected_input(workflow_data):
    config, info = workflow_data
    config["graph"]["pos"]["inputs"]["text"] = ["unused", 0]
    validate_workflow(config, info)
    graph = render_workflow(config, "new scene", 1, "")
    assert graph["pos"]["inputs"]["text"] == "new scene"
    assert "unused" not in graph


@pytest.mark.asyncio
async def test_job_uses_frozen_workflow_and_selected_output(tmp_path, workflow_data):
    config, info = workflow_data
    store = Store(tmp_path)
    store.write("workflow.json", config)
    ready = asyncio.Event()
    async def prepare():
        await ready.wait()
        return "garden"
    client = AsyncMock()
    client.node_info.return_value = info
    client.check_connection.return_value = client.idle.return_value = True
    client.submit.return_value = "accepted"
    client.result.return_value = {"status": {"completed": True}, "outputs": {"save": {"images": [{"filename": "output.png"}]}}}
    client.download.return_value = picture()
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.start(Settings(), Portrait("alice", appearance="blue eyes", style="ink"), prepare)
        store.write("workflow.json", {})
        ready.set()
        await engine.worker
    graph = client.submit.call_args.args[0]
    assert graph["render"]["inputs"]["width"] == 896
    assert 0 <= graph["render"]["inputs"]["seed"] <= 7
    assert store.read("history.json")["items"][0]["stage"] == "completed"
    client.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_server_and_missing_models_block_before_llm(tmp_path, workflow_data):
    config, info = workflow_data
    store = Store(tmp_path)
    store.write("workflow.json", config)
    engine = Engine(store, AsyncMock())
    prepare = AsyncMock(return_value="garden")
    portrait = Portrait("alice", appearance="blue eyes", style="ink")
    with pytest.raises(ValueError, match="地址已变化"):
        await engine.start(replace(Settings(), comfy_url="http://different:8188"), portrait, prepare)
    info["Render"]["input"]["required"]["model"][1]["options"] = ["removed"]
    client = AsyncMock()
    client.check_connection.return_value = True
    client.node_info.return_value = info
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        with pytest.raises(ValueError, match="模型已不存在"):
            await engine.start(Settings(), portrait, prepare)
    prepare.assert_not_awaited()
    assert engine.state()["active"] is None


@pytest.mark.asyncio
async def test_reference_binding_controls_upload_independently_of_legacy_mode(tmp_path, workflow_data):
    config, info = workflow_data
    config["graph"]["image"] = {"class_type": "LoadImage", "inputs": {"image": "old.png"}}
    config["graph"]["render"]["inputs"]["image"] = ["image", 0]
    info["Render"]["input"]["required"]["image"] = ["IMAGE", {}]
    info["LoadImage"] = {"input": {"required": {"image": [["old.png"], {"image_upload": True}]}}, "output": ["IMAGE"]}
    config["mapping"]["image"] = ["image", "image"]
    store = Store(tmp_path)
    store.write("workflow.json", config)
    engine = Engine(store, AsyncMock())
    portrait = Portrait("alice", appearance="blue eyes", style="ink", mode="text2img")
    with pytest.raises(ValueError, match="图片"):
        await engine.start(Settings(), portrait, AsyncMock())
    portrait = replace(portrait, image=store.save_image("portraits", picture()))
    client = AsyncMock()
    client.node_info.return_value = info
    client.check_connection.return_value = client.idle.return_value = True
    client.upload.return_value = "astrbot/new.png"
    client.submit.return_value = "accepted"
    client.result.return_value = {"status": {"completed": True}, "outputs": {"save": {"images": [{"filename": "output.png"}]}}}
    client.download.return_value = picture()
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.start(Settings(), portrait, AsyncMock(return_value="garden"))
        await engine.worker
    assert client.submit.call_args.args[0]["image"]["inputs"]["image"] == "astrbot/new.png"
    client.upload.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_listing_read_and_metadata_http(workflow_data):
    config, info = workflow_data
    seen = []
    async def queue(request):
        return web.json_response({"queue_running": [], "queue_pending": []})
    async def listing(request):
        assert request.query["dir"] == "workflows"
        return web.json_response([{"path": "api/图+例.json", "modified": 10}, {"path": "notes.txt"}])
    async def read(request):
        from urllib.parse import unquote
        seen.append(unquote(request.match_info["file"]))
        return web.json_response(config["graph"])
    async def metadata(request):
        kind = request.match_info["kind"]
        return web.json_response({kind: info[kind]} if kind in info else {})
    app = web.Application()
    app.router.add_get("/queue", queue)
    app.router.add_get("/userdata", listing)
    app.router.add_get("/userdata/{file:.*}", read)
    app.router.add_get("/object_info/{kind}", metadata)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        client = ComfyClient(f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}")
        assert len(await client.workflows()) == 1
        graph = await client.workflow("api/图+例.json")
        assert graph == config["graph"]
        assert seen == ["workflows/api/图+例.json"]
        validate_workflow(config, await client.node_info(graph))
        with pytest.raises(ValueError):
            await client.workflow("../secrets.json")
    finally:
        await runner.cleanup()
