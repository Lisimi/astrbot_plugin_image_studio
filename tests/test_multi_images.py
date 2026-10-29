"""Verify multi-reference profiles, bounded analysis, and frozen random choices."""

import asyncio
import base64
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from astrbot_plugin_image_studio.engine import Engine
from astrbot_plugin_image_studio.models import Portrait, Settings, reference_images
from astrbot_plugin_image_studio.store import Store
from test_core import picture
from test_plugin import plugin
from test_workflow_config import workflow_data


def test_legacy_profile_migrates_and_empty_list_removes_references():
    old = Portrait.read({"persona_id": "alice", "image": "old.png"})
    assert old.references == ("old.png",)
    assert old.to_dict()["images"] == ["old.png"]
    new = Portrait.read({"persona_id": "alice", "image": "stale.png", "images": ["a.png", "b.png", "a.png"]})
    assert new.references == ("a.png", "b.png")
    assert new.image == "a.png"
    assert Portrait.read(new.to_dict()) == new
    assert not Portrait.read({"persona_id": "alice", "image": "stale.png", "images": []}).references
    assert len(reference_images({"images": [f"ref-{i}.png" for i in range(150)]})) == 150


@pytest.mark.parametrize("images", ["one.png", [None], [""], [3]])
def test_reference_list_validation(images):
    with pytest.raises(ValueError):
        Portrait.read({"persona_id": "alice", "images": images})


def test_multiple_portraits_are_isolated_and_all_paths_are_checked(tmp_path):
    store = Store(tmp_path)
    names = tuple(store.save_image("portraits", picture()) for _ in range(3))
    store.save_portrait(Portrait("alice", images=names[:2]))
    store.save_portrait(Portrait("bob", images=names[2:]))
    assert store.portrait("alice").references == names[:2]
    assert store.portrait("bob").references == names[2:]
    with pytest.raises(ValueError):
        store.save_portrait(Portrait("alice", images=(names[0], "../bad.png")))
    assert store.portrait("alice").references == names[:2]


def test_vision_copy_is_bounded_and_original_remains_unchanged(tmp_path):
    store = Store(tmp_path)
    source = io.BytesIO()
    Image.new("RGB", (3200, 1600), "blue").save(source, "PNG")
    name = store.save_image("portraits", source.getvalue())
    uri = store.vision_image(name)
    assert uri.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as preview:
        assert preview.size == (1536, 768)
    with Image.open(store.path("portraits", name)) as original:
        assert original.size == (3200, 1600)


@pytest.mark.asyncio
async def test_analysis_uses_every_image_in_batches_and_merges_previous_result(plugin):
    instance, request = plugin
    instance.config["vision_provider"] = "vision"
    names = [instance.store.save_image("portraits", picture()) for _ in range(9)]
    results = [{"appearance": f"identity {i}", "style": "ink", "habits": "reserved"} for i in range(3)]
    instance.context.llm_generate.side_effect = [SimpleNamespace(completion_text=json.dumps(result)) for result in results]
    request.json.return_value = {"persona_id": "alice", "images": names}
    with patch.object(instance.store, "vision_image", side_effect=lambda name: "uri:" + name) as prepare:
        response = await instance.web("analyze")
        assert response.status_code == 200
        assert "id" in json.loads(response.body)
        await instance.analysis_worker
    calls = instance.context.llm_generate.await_args_list
    assert [len(call.kwargs["image_urls"]) for call in calls] == [4, 4, 1]
    assert [uri for call in calls for uri in call.kwargs["image_urls"]] == ["uri:" + name for name in names]
    assert [call.args[0] for call in prepare.call_args_list] == names
    assert json.loads(calls[1].kwargs["prompt"])["previous_analysis"] == results[0]
    assert json.loads(calls[-1].kwargs["prompt"])["images_seen"] == 9
    job = instance.store.read("analysis.json")
    assert job["stage"] == "completed" and job["processed"] == 9
    assert job["result"] == results[-1]
    assert not instance.store.read("portraits.json")
    assert instance.engine.state()["active"] is None


@pytest.mark.asyncio
async def test_analysis_failure_keeps_old_profile_and_reports_partial_progress(plugin):
    instance, request = plugin
    instance.config["vision_provider"] = "vision"
    names = [instance.store.save_image("portraits", picture()) for _ in range(5)]
    instance.store.save_portrait(Portrait("alice", appearance="original", style="original style", images=tuple(names)))
    before = instance.store.read("portraits.json")
    instance.context.llm_generate.side_effect = [SimpleNamespace(completion_text='{"appearance":"new","style":"ink"}'), OSError("provider disconnected")]
    request.json.return_value = {"persona_id": "alice", "images": names}
    assert (await instance.web("analyze")).status_code == 200
    await instance.analysis_worker
    job = instance.store.read("analysis.json")
    assert job["stage"] == "failed" and job["processed"] == 4
    assert instance.store.read("portraits.json") == before
    assert instance.engine.state()["active"] is None
    assert instance.context.llm_generate.await_count == 2


@pytest.mark.asyncio
async def test_analysis_busy_and_missing_image_are_rejected_without_llm(plugin):
    instance, request = plugin
    instance.config["vision_provider"] = "vision"
    request.json.return_value = {"persona_id": "alice", "images": ["missing.png"]}
    assert (await instance.web("analyze")).status_code == 400
    name = instance.store.save_image("portraits", picture())
    instance.store.write("state.json", {"active": {"id": "busy"}})
    request.json.return_value["images"] = [name]
    assert (await instance.web("analyze")).status_code == 400
    assert not instance.store.read("analysis.json")
    instance.context.llm_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_analysis_shutdown_marks_failure_and_releases_gate(plugin):
    instance, _ = plugin
    instance.config["vision_provider"] = "vision"
    name = instance.store.save_image("portraits", picture())
    entered = asyncio.Event()
    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    instance.context.llm_generate.side_effect = blocked
    await instance.start_analysis("alice", "gentle artist", (name,))
    await asyncio.wait_for(entered.wait(), timeout=3)
    await instance.terminate()
    assert instance.store.read("analysis.json")["stage"] == "failed"
    assert instance.engine.state()["active"] is None
    assert instance.context.llm_generate.await_count == 1


@pytest.mark.asyncio
async def test_single_image_analysis_body_remains_supported(plugin):
    instance, request = plugin
    instance.config["vision_provider"] = "vision"
    name = instance.store.save_image("portraits", picture())
    instance.context.llm_generate.return_value = SimpleNamespace(completion_text='{"appearance":"blue eyes","style":"ink"}')
    request.json.return_value = {"persona_id": "alice", "image": name}
    assert (await instance.web("analyze")).status_code == 200
    await instance.analysis_worker
    assert instance.store.read("analysis.json")["images"] == [name]
    assert len(instance.context.llm_generate.call_args.kwargs["image_urls"]) == 1


@pytest.mark.asyncio
async def test_restart_does_not_repeat_interrupted_analysis(plugin):
    instance, _ = plugin
    instance.store.write("state.json", {"active": {"stage": "utility", "id": "previous-process"}})
    instance.store.write("analysis.json", {"id": "old-analysis", "stage": "running", "processed": 4, "total": 9})
    await instance.initialize()
    assert instance.store.read("analysis.json")["stage"] == "failed"
    assert instance.store.read("analysis.json")["processed"] == 4
    assert instance.engine.state()["active"] is None
    instance.context.llm_generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_generation_does_not_choose_or_upload_reference(tmp_path):
    store = Store(tmp_path)
    portrait = Portrait("alice", appearance="blue eyes", style="ink", images=("not-needed.png",))
    client = AsyncMock()
    client.check_connection.return_value = client.idle.return_value = True
    client.submit.return_value = "accepted"
    client.result.return_value = {"status": {"completed": True}, "outputs": {"222": {"images": [{"filename": "result.png"}]}}}
    client.download.return_value = picture()
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client), patch("astrbot_plugin_image_studio.engine.secrets.choice") as choose:
        await engine.start(Settings(), portrait, AsyncMock(return_value="scene"))
        await engine.worker
        choose.assert_not_called()
    client.upload.assert_not_awaited()
    assert not store.read("history.json")["items"][0]["reference_image"]


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [False, True])
async def test_random_reference_is_frozen_for_builtin_and_custom_jobs(tmp_path, workflow_data, custom):
    store = Store(tmp_path)
    names = tuple(store.save_image("portraits", picture()) for _ in range(3))
    portrait = Portrait("alice", appearance="blue eyes", style="ink", images=names,
                        mode="text2img" if custom else "img2img")
    config, info = workflow_data
    if custom:
        config["graph"]["image"] = {"class_type": "LoadImage", "inputs": {"image": "old.png"}}
        config["graph"]["render"]["inputs"]["image"] = ["image", 0]
        info["Render"]["input"]["required"]["image"] = ["IMAGE", {}]
        info["LoadImage"] = {"input": {"required": {"image": ["STRING", {"image_upload": True}]}}, "output": ["IMAGE"]}
        config["mapping"]["image"] = ["image", "image"]
        store.write("workflow.json", config)
    client = AsyncMock()
    client.check_connection.return_value = client.idle.return_value = True
    client.node_info.return_value = info
    client.upload.return_value = "uploaded/chosen.png"
    client.submit.side_effect = TimeoutError()
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client), patch("astrbot_plugin_image_studio.engine.secrets.choice", return_value=names[2]) as choose:
        await engine.start(Settings(), portrait, AsyncMock(return_value="scene"))
        await engine.worker
        job = engine.state()["active"]
        assert job["reference_image"] == names[2]
        assert job["portrait"]["images"] == list(names)
        client.upload.assert_awaited_once_with(store.path("portraits", names[2]))
        image_node = "image" if custom else "398"
        assert client.submit.call_args.args[0][image_node]["inputs"]["image"] == "uploaded/chosen.png"
        store.save_portrait(replace(portrait, images=names[:1]))
        output_node = "save" if custom else "222"
        client.locate.return_value = "server"
        client.result.return_value = {"status": {"completed": True}, "outputs": {output_node: {"images": [{"filename": "result.png"}]}}}
        client.download.return_value = picture()
        await engine.recover()
        await engine.worker
        choose.assert_called_once_with(names)
        client.upload.assert_awaited_once()
        client.submit.assert_awaited_once()
    assert store.read("history.json")["items"][0]["reference_image"] == names[2]
