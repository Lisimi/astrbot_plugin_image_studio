"""Exercise AstrBot-facing behavior with an explicit SDK test double."""

import hashlib
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.responses import JSONResponse, Response

from astrbot_plugin_image_studio.comfy import ComfyUnavailable
from astrbot_plugin_image_studio.models import Portrait, Settings
from astrbot_plugin_image_studio.store import Store, revision
from test_workflow_config import workflow_data


@pytest.fixture
def plugin(monkeypatch, tmp_path):
    modules = {name: types.ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star", "astrbot.api.web",
        "astrbot.core", "astrbot.core.message", "astrbot.core.message.message_event_result",
        "astrbot.core.provider", "astrbot.core.provider.entities", "astrbot.core.star",
        "astrbot.core.star.filter", "astrbot.core.star.filter.command")}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    class Config(dict):
        def save_config(self, data):
            self.update(data)
    class Star:
        def __init__(self, context):
            self.context = context
    def decorator(*args, **kwargs):
        return lambda function: function
    modules["astrbot.api"].AstrBotConfig = Config
    modules["astrbot.api"].logger = SimpleNamespace(exception=lambda *args: None)
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageEventResult = object
    modules["astrbot.api.event"].filter = SimpleNamespace(command=decorator,llm_tool=decorator,on_llm_request=decorator)
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.api.star"].StarTools = SimpleNamespace(get_data_dir=lambda name: tmp_path)
    modules["astrbot.core.message.message_event_result"].MessageChain = object
    modules["astrbot.core.provider.entities"].ProviderRequest = object
    modules["astrbot.core.star.filter.command"].GreedyStr = str
    request = SimpleNamespace(method="POST", username="admin", json=AsyncMock(return_value={}))
    modules["astrbot.api.web"].request = request
    modules["astrbot.api.web"].json_response = JSONResponse
    modules["astrbot.api.web"].error_response = lambda message,status_code=400: JSONResponse({"message":message}, status_code=status_code)
    modules["astrbot.api.web"].file_response = lambda *args,**kwargs: Response()
    monkeypatch.delitem(sys.modules, "astrbot_plugin_image_studio.main", raising=False)
    main = importlib.import_module("astrbot_plugin_image_studio.main")
    monkeypatch.setattr(main.ComfyClient, "check_connection", AsyncMock(return_value=True))
    persona_manager = SimpleNamespace(get_persona=AsyncMock(return_value=SimpleNamespace(system_prompt="gentle artist")),
        resolve_selected_persona=AsyncMock(return_value=("alice", {"prompt":"gentle artist"}, None, False)))
    context = SimpleNamespace(register_web_api=lambda *args:None, persona_manager=persona_manager,
        conversation_manager=SimpleNamespace(get_curr_conversation_id=AsyncMock(return_value=None)),
        llm_generate=AsyncMock(return_value=SimpleNamespace(completion_text='{"scene":"smiling in a garden"}')),
        get_current_chat_provider_id=AsyncMock(return_value="chat"))
    instance = main.ImageStudio(context, Config(Settings(prompt_provider="test").to_dict()))
    return instance, request


@pytest.mark.asyncio
async def test_resolved_persona_uses_host_session_rules(plugin):
    instance, _ = plugin
    event = SimpleNamespace(unified_msg_origin="qq:friend:1", get_platform_name=lambda:"aiocqhttp")
    assert await instance.persona(event) == ("alice", "gentle artist")
    instance.context.persona_manager.resolve_selected_persona.assert_awaited_once_with(
        umo="qq:friend:1", conversation_persona_id=None, platform_name="aiocqhttp")


@pytest.mark.asyncio
async def test_no_persona_does_not_fall_back_to_other_portraits(plugin):
    instance, _ = plugin
    instance.context.persona_manager.resolve_selected_persona.return_value = ("[%None]", None, None, False)
    event = SimpleNamespace(unified_msg_origin="qq:friend:1", get_platform_name=lambda:"aiocqhttp")
    with pytest.raises(ValueError, match="没有"):
        await instance.persona(event)


@pytest.mark.asyncio
async def test_prompt_preserves_fixed_identity_and_rejects_stale_persona(plugin):
    instance, _ = plugin
    persona = "gentle artist"
    portrait = Portrait("alice", appearance="blue eyes", style="watercolor", persona_hash=hashlib.sha256(persona.encode()).hexdigest())
    prompt = await instance.prompt(instance.settings(), portrait, persona, "garden")
    assert "blue eyes" in prompt and "watercolor" in prompt and "smiling in a garden" in prompt
    assert "tools" not in instance.context.llm_generate.call_args.kwargs
    with pytest.raises(ValueError, match="人格已变化"):
        await instance.prompt(instance.settings(), portrait, "new persona", "garden")


@pytest.mark.asyncio
async def test_web_requires_dashboard_identity(plugin):
    instance, request = plugin
    request.username = None
    assert (await instance.web("settings")).status_code == 403


@pytest.mark.asyncio
async def test_web_config_conflict_and_persistence(plugin):
    instance, request = plugin
    request.json.return_value = {"revision":"old", "settings":instance.settings().to_dict()}
    assert (await instance.web("settings")).status_code == 409
    original = instance.settings().to_dict()
    request.json.return_value = {"revision":revision(original), "settings":original | {"cooldown_seconds":60}}
    assert (await instance.web("settings")).status_code == 200
    assert instance.settings().cooldown_seconds == 60


@pytest.mark.asyncio
async def test_web_unknown_task_requires_explicit_release(plugin):
    instance, request = plugin
    request.json.return_value = {}
    assert (await instance.web("release")).status_code == 400


def test_schema_and_api_example_match_code():
    import json
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text("utf-8"))
    assert {key: value["default"] for key, value in schema.items()} == Settings().to_dict()
    graph = json.loads((root / "workflows/anima_api.json").read_text("utf-8"))
    assert graph["220"]["inputs"]["steps"] == 36


def test_page_discovery_matches_astrbot_428_contract():
    import json
    root = Path(__file__).resolve().parents[1]
    page = root / "pages" / "studio"
    assert (page / "index.html").is_file()
    assert (page / "app.js").is_file()
    assert (page / "style.css").is_file()
    locale = json.loads((root / ".astrbot-plugin/i18n/zh-CN.json").read_text("utf-8"))
    assert locale["pages"]["studio"]["title"] == "生图工作室"


@pytest.mark.asyncio
async def test_confirm_uses_exact_preview_without_second_llm_call(plugin):
    import json
    instance, request = plugin
    persona = "gentle artist"
    portrait = Portrait("alice",appearance="blue eyes",style="watercolor",persona_hash=hashlib.sha256(persona.encode()).hexdigest())
    instance.store.save_portrait(portrait)
    request.json.return_value = {"persona_id":"alice","idea":"garden"}
    response = await instance.web("preview")
    assert response.status_code == 200
    preview = json.loads(response.body)
    instance.engine.start = AsyncMock(return_value="job")
    request.json.return_value = {"persona_id":"alice","idea":"garden","preview_token":preview["token"]}
    assert (await instance.web("generate")).status_code == 200
    prepare = instance.engine.start.call_args.args[2]
    assert await prepare() == preview["prompt"]
    assert instance.context.llm_generate.await_count == 1
    request.json.return_value["idea"] = "changed scene"
    assert (await instance.web("generate")).status_code == 400


@pytest.mark.asyncio
async def test_tool_instructs_model_to_report_offline_failure(plugin):
    import json
    instance, _ = plugin
    instance.accept = AsyncMock(side_effect=ComfyUnavailable())
    result = json.loads(await instance.image_generate(object(), "garden"))
    assert result["ok"] is False
    assert result["reason"] == "comfy_unavailable"
    assert "本次未生成图片" in result["message"]
    assert "不要声称正在生成" in result["instruction"]


@pytest.mark.asyncio
async def test_preview_unavailable_returns_503_without_calling_llm(plugin, monkeypatch):
    import json
    from astrbot_plugin_image_studio.comfy import ComfyClient
    instance, request = plugin
    persona = "gentle artist"
    instance.store.save_portrait(Portrait("alice", appearance="blue eyes", style="ink", persona_hash=hashlib.sha256(persona.encode()).hexdigest()))
    monkeypatch.setattr(ComfyClient,"check_connection",AsyncMock(side_effect=ComfyUnavailable()))
    request.json.return_value = {"persona_id":"alice","idea":"garden"}
    response = await instance.web("preview")
    assert response.status_code == 503
    assert "本次未生成图片" in json.loads(response.body)["message"]
    instance.context.llm_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_command_reports_connection_failure_in_user_reply(plugin):
    instance, _ = plugin
    instance.accept = AsyncMock(side_effect=ComfyUnavailable())
    event = SimpleNamespace(plain_result=lambda text:text)
    replies = [reply async for reply in instance.draw(event,"garden")]
    assert len(replies) == 1
    assert "暂时无法连接本地 ComfyUI" in replies[0]


@pytest.mark.asyncio
async def test_web_workflow_inspect_save_conflict_and_validation(plugin, workflow_data, monkeypatch):
    import json
    from astrbot_plugin_image_studio.comfy import ComfyClient
    instance, request = plugin
    config, info = workflow_data
    monkeypatch.setattr(ComfyClient, "node_info", AsyncMock(return_value=info))
    request.json.return_value = {"graph": config["graph"], "name": "example.json"}
    response = await instance.web("workflow-inspect")
    assert response.status_code == 200
    data = json.loads(response.body)
    assert data["suggested"]["output"] == "save"
    assert not instance.store.read("workflow.json")
    request.json.return_value = {"workflow": config, "revision": revision({})}
    assert (await instance.web("workflow-save")).status_code == 200
    stored = instance.store.read("workflow.json")
    assert stored["fingerprint"] == revision(config["graph"])
    assert (await instance.web("workflow-save")).status_code == 409
    request.json.return_value["revision"] = revision(stored)
    config["mapping"]["positive"] = ["missing", "text"]
    assert (await instance.web("workflow-save")).status_code == 400
    assert instance.store.read("workflow.json") == stored


@pytest.mark.asyncio
async def test_changed_workflow_invalidates_prompt_preview(plugin):
    import json
    instance, request = plugin
    persona = "gentle artist"
    instance.store.save_portrait(Portrait("alice", appearance="blue eyes", style="ink", persona_hash=hashlib.sha256(persona.encode()).hexdigest()))
    request.json.return_value = {"persona_id": "alice", "idea": "garden"}
    preview = json.loads((await instance.web("preview")).body)
    instance.store.write("workflow.json", {"name": "changed"})
    instance.engine.start = AsyncMock(return_value="job")
    request.json.return_value["preview_token"] = preview["token"]
    assert (await instance.web("generate")).status_code == 400
    instance.engine.start.assert_not_awaited()
