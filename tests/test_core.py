"""Verify isolation, workflow wiring and durable pressure limits."""

import asyncio
import io
import time
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from astrbot_plugin_image_studio.comfy import ComfyClient, ComfyError, ComfyUnavailable, SubmissionRejected
from astrbot_plugin_image_studio.engine import Engine
from astrbot_plugin_image_studio.models import Portrait, Settings, object_value
from astrbot_plugin_image_studio.store import Store
from astrbot_plugin_image_studio.workflow import build_workflow


def picture():
    data = io.BytesIO()
    Image.new("RGB", (32, 48), "green").save(data, "PNG")
    return data.getvalue()


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path)


@pytest.fixture
def portrait():
    return Portrait("alice", appearance="blue eyes", style="watercolor")


def test_persona_isolation_and_safe_paths(store):
    name = store.save_image("portraits", picture())
    store.save_portrait(Portrait("alice", image=name, appearance="blue eyes"))
    store.save_portrait(Portrait("bob", appearance="green eyes"))
    assert store.portrait("alice").appearance == "blue eyes"
    assert store.portrait("bob").appearance == "green eyes"
    with pytest.raises(ValueError):
        store.portrait("missing")
    for name in ("../secret", "..\\secret", "/etc/passwd", "missing.png"):
        with pytest.raises(ValueError):
            store.path("portraits", name)
    with pytest.raises(Exception):
        store.save_image("portraits", b"not an image")


@pytest.mark.parametrize("values", [
    {"width": 887}, {"width": 1536, "height": 1536}, {"steps": True},
    {"cfg": float("nan")}, {"cooldown_seconds": 0}, {"task_timeout": 0},
    {"comfy_url": "file:///tmp"}, {"comfy_url": "http://user:key@host"},
    {"comfy_url": "http://host?token=secret"}, {"other": "unexpected"},
])
def test_invalid_settings(values):
    with pytest.raises(ValueError):
        Settings.read(values)


def test_settings_roundtrip():
    assert Settings.read(Settings().to_dict()) == Settings()


def test_workflow_direct_prompt_and_reference_branch(portrait):
    graph = build_workflow(Settings(), portrait, "new scene", 73)
    assert graph["218"]["inputs"]["text"] == "new scene"
    assert graph["220"]["inputs"]["denoise"] == 1
    assert graph["220"]["inputs"]["seed"] == 73
    assert not any(node["class_type"] in ("TIPO", "PixAITaggerPlus") for node in graph.values())
    portrait = replace(portrait, mode="img2img", denoise=0.55)
    with pytest.raises(ValueError):
        build_workflow(Settings(), portrait, "new scene", 73)
    graph = build_workflow(Settings(), portrait, "new scene", 73, "astrbot/reference.png")
    assert graph["398"]["inputs"]["image"] == "astrbot/reference.png"
    assert graph["220"]["inputs"]["latent_image"] == ["428", 0]
    assert graph["220"]["inputs"]["denoise"] == 0.55
    assert "208" not in graph


@pytest.mark.asyncio
async def test_concurrent_calls_have_one_submission_and_share_cooldown(store, portrait):
    finish = asyncio.Event()
    async def prepare():
        await finish.wait()
        return "sunlit park"
    engine = Engine(store, AsyncMock())
    other = Engine(store, AsyncMock())
    client = AsyncMock()
    client.idle.return_value = True
    client.submit.return_value = "accepted"
    client.result.return_value = {"outputs": {"222": {"images": [{"filename": "a.png"}]}}, "status": {"completed": True}}
    client.download.return_value = picture()
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        outcomes = await asyncio.gather(engine.start(Settings(), portrait, prepare), other.start(Settings(), portrait, prepare), return_exceptions=True)
        assert sum(isinstance(value, str) for value in outcomes) == 1
        assert sum(isinstance(value, ValueError) for value in outcomes) == 1
        finish.set()
        workers = [e.worker for e in (engine, other) if e.worker]
        await asyncio.gather(*workers)
        assert client.submit.await_count == 1
        assert engine.state()["active"] is None
        assert 290 < engine.state()["cooldown_until"] - time.time() <= 300
        with pytest.raises(ValueError, match="冷却"):
            await other.start(Settings(), portrait, prepare)


@pytest.mark.asyncio
async def test_busy_comfy_never_calls_llm_and_does_not_start_cooldown(store, portrait):
    prepare = AsyncMock(return_value="scene")
    client = AsyncMock()
    client.idle.return_value = False
    client.check_connection.return_value = False
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        with pytest.raises(ValueError, match="已有其他任务"):
            await engine.start(Settings(), portrait, prepare)
    prepare.assert_not_awaited()
    client.submit.assert_not_awaited()
    assert engine.state()["cooldown_until"] == 0


@pytest.mark.asyncio
async def test_offline_rejected_before_job_and_llm(store, portrait):
    prepare = AsyncMock(return_value="scene")
    client = AsyncMock()
    client.check_connection.side_effect = ComfyUnavailable()
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        with pytest.raises(ComfyUnavailable, match="本次未生成图片"):
            await engine.start(Settings(), portrait, prepare)
    prepare.assert_not_awaited()
    client.submit.assert_not_awaited()
    assert engine.worker is None
    assert engine.state() == {"active": None, "cooldown_until": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("offline"), TimeoutError(), ComfyError("HTTP 503"), ValueError("not JSON")])
async def test_connection_failure_has_safe_user_message(error):
    client = ComfyClient("http://test")
    client.idle = AsyncMock(side_effect=error)
    with pytest.raises(ComfyUnavailable, match="网络连接"):
        await client.check_connection()


@pytest.mark.asyncio
async def test_disconnect_before_submission_notifies_chat(store, portrait):
    prepare = AsyncMock()
    deliver = AsyncMock()
    client = AsyncMock()
    client.check_connection.return_value = True
    client.idle.side_effect = OSError("disconnected after acceptance")
    engine = Engine(store, deliver)
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.start(Settings(), portrait, prepare, "qq:friend:1")
        await engine.worker
    prepare.assert_not_awaited()
    client.submit.assert_not_awaited()
    deliver.assert_awaited_once()
    job = deliver.call_args.args[0]
    assert job["stage"] == "failed"
    assert "本次未生成图片" in job["error"]


@pytest.mark.asyncio
async def test_submission_timeout_is_not_retried(store, portrait):
    client = AsyncMock()
    client.idle.return_value = True
    client.submit.side_effect = TimeoutError()
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.start(Settings(), portrait, AsyncMock(return_value="scene"))
        await engine.worker
        active = object_value(engine.state()["active"])
        assert active["stage"] == "unknown" and active["prompt_id"]
        assert "无法确认任务结果" in active["error"]
        assert "本次未生成图片" not in active["error"]
        with pytest.raises(ValueError):
            await engine.start(Settings(), portrait, AsyncMock())
        assert client.submit.await_count == 1


@pytest.mark.asyncio
async def test_recover_completed_job_only_downloads_and_sends(store, portrait):
    job = {"id": "local", "stage": "unknown", "prompt_id": "server", "origin": "qq:friend:1", "persona_id": "alice", "settings": Settings().to_dict(), "portrait": portrait.to_dict()}
    store.write("state.json", {"active": job})
    delivered = AsyncMock()
    engine = Engine(store, delivered)
    client = AsyncMock()
    client.locate.return_value = "server"
    client.result.return_value = {"outputs": {"222": {"images": [{"filename": "a.png"}]}}, "status": {"completed": True}}
    client.download.return_value = picture()
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.recover()
        await engine.worker
    client.submit.assert_not_awaited()
    delivered.assert_awaited_once()
    assert store.read("history.json")["items"][0]["stage"] == "completed"


@pytest.mark.asyncio
async def test_send_failure_preserves_image_and_completes_gate(store, portrait):
    engine = Engine(store, AsyncMock(side_effect=RuntimeError()))
    job = {"id": "sent", "settings": Settings().to_dict(), "origin": "qq:friend:1", "file": store.save_image("outputs", picture())}
    await engine.send(job)
    assert job["stage"] == "send_failed"
    assert store.path("outputs", job["file"]).exists()
    assert engine.state()["active"] is None


@pytest.mark.asyncio
async def test_live_analysis_cannot_be_recovered_away(store):
    waiting = asyncio.Event()
    engine = Engine(store, AsyncMock())
    operation = asyncio.create_task(engine.exclusive(waiting.wait))
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="处理"):
        await engine.recover()
    waiting.set()
    await operation
    assert engine.state()["active"] is None


@pytest.mark.asyncio
async def test_rejected_submission_is_terminal(store, portrait):
    client = AsyncMock()
    client.idle.return_value = True
    client.submit.side_effect = SubmissionRejected("invalid graph")
    engine = Engine(store, AsyncMock())
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        await engine.start(Settings(), portrait, AsyncMock(return_value="scene"))
        await engine.worker
    assert engine.state()["active"] is None
    assert store.read("history.json")["items"][0]["stage"] == "failed"


@pytest.mark.asyncio
async def test_old_comfy_recovery_uses_client_id():
    client = ComfyClient("http://test")
    client.request = AsyncMock(return_value={"queue_running": [[0, "server-id", {}, {"client_id": "client-id"}]], "queue_pending": []})
    assert await client.locate("client-id") == "server-id"


@pytest.mark.asyncio
async def test_release_unknown_requires_idle_comfy(store):
    job = {"id": "local", "stage": "unknown", "settings": Settings().to_dict()}
    store.write("state.json", {"active": job})
    engine = Engine(store, AsyncMock())
    client = AsyncMock()
    client.idle.return_value = False
    with patch("astrbot_plugin_image_studio.engine.ComfyClient", return_value=client):
        with pytest.raises(ValueError, match="队列"):
            await engine.release_unknown()
        assert engine.state()["active"] is not None
