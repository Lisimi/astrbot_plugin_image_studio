"""Validated configuration and JSON helpers."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import TypeAlias, cast
from urllib.parse import urlsplit

Json: TypeAlias = "None | bool | int | float | str | list[Json] | dict[str, Json]"
Object: TypeAlias = dict[str, Json]


def object_value(value: object) -> Object:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError("数据必须是 JSON 对象")
    return cast(Object, value)


def parse_object(text: str) -> Object:
    return object_value(json.loads(text))


def string(value: object, limit: int = 8000) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"文本长度须在 {limit} 字符以内")
    return value.strip()


def number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("数值格式错误")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"数值须在 {low} 至 {high} 之间")
    return float(value)


NEGATIVE = "worst quality, low quality, score_1, score_2, score_3, artist name, lowres, jpeg, text, words, logo, cropped, bad composition, artifacts, bad proportions, inaccurate limb, bad anatomy, extra fingers, missing fingers, extra arms, extra legs, inaccurate eyes, watermark"


@dataclass(frozen=True)
class Settings:
    comfy_url: str = "http://192.168.0.203:8188"
    vision_provider: str = ""
    prompt_provider: str = ""
    cooldown_seconds: int = 300
    task_timeout: int = 600
    width: int = 888
    height: int = 1184
    unet: str = "anima_baseV10.safetensors"
    clip: str = "qwen_3_06b_base.safetensors"
    vae: str = "qwen_image_vae.safetensors"
    steps: int = 36
    cfg: float = 4.0
    negative: str = NEGATIVE
    prompt_rules: str = "用英文描述一幅画面，保持角色身份和画风，动作、表情、场景符合当前人格与用户要求。"
    auto_rules: str = "仅在用户想看角色或图片对表达当前情境有帮助时调用，避免每轮发图。"

    def to_dict(self) -> Object:
        return object_value(asdict(self))

    @classmethod
    def read(cls, values: Object) -> Settings:
        defaults = cls().to_dict()
        if set(values) - set(defaults):
            raise ValueError("包含未知配置项")
        merged = defaults | values
        for key in ("cooldown_seconds", "task_timeout", "width", "height", "steps"):
            bounds = {"cooldown_seconds": (1, 86400), "task_timeout": (30, 3600),
                      "width": (256, 1536), "height": (256, 1536), "steps": (1, 100)}
            value = number(merged[key], *bounds[key])
            if not value.is_integer():
                raise ValueError(f"{key} 必须为整数")
            merged[key] = int(value)
        for key in ("width", "height"):
            if cast(int, merged[key]) % 8:
                raise ValueError("图片宽高必须为 8 的倍数")
        if cast(int, merged["width"]) * cast(int, merged["height"]) > 1600000:
            raise ValueError("首版限制最多 160 万像素")
        merged["cfg"] = number(merged["cfg"], 0, 20)
        for key, field_value in merged.items():
            if isinstance(defaults[key], str):
                merged[key] = string(field_value)
        url = string(merged["comfy_url"], 512).rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("ComfyUI 地址须为不含账号、查询参数的 HTTP(S) 地址")
        for key in ("unet", "clip", "vae"):
            if not merged[key]:
                raise ValueError("模型名称不能为空")
        merged["comfy_url"] = url
        return cls(comfy_url=url, vision_provider=string(merged["vision_provider"]),
                   prompt_provider=string(merged["prompt_provider"]),
                   cooldown_seconds=cast(int, merged["cooldown_seconds"]),
                   task_timeout=cast(int, merged["task_timeout"]),
                   width=cast(int, merged["width"]), height=cast(int, merged["height"]),
                   unet=string(merged["unet"]), clip=string(merged["clip"]), vae=string(merged["vae"]),
                   steps=cast(int, merged["steps"]), cfg=cast(float, merged["cfg"]),
                   negative=string(merged["negative"]), prompt_rules=string(merged["prompt_rules"]),
                   auto_rules=string(merged["auto_rules"]))


@dataclass(frozen=True)
class Portrait:
    persona_id: str
    image: str = ""
    appearance: str = ""
    style: str = ""
    habits: str = ""
    mode: str = "text2img"
    denoise: float = 0.55
    auto: bool = False
    persona_hash: str = ""

    def to_dict(self) -> Object:
        return object_value(asdict(self))

    @classmethod
    def read(cls, values: Object) -> Portrait:
        if set(values) - set(cls.__dataclass_fields__):
            raise ValueError("画像包含未知字段")
        persona_id = string(values.get("persona_id"), 255)
        if not persona_id:
            raise ValueError("请选择人格")
        mode = string(values.get("mode", "text2img"), 20)
        if mode not in ("text2img", "img2img"):
            raise ValueError("不支持的生图模式")
        auto = values.get("auto", False)
        if not isinstance(auto, bool):
            raise ValueError("自动开关须为布尔值")
        return cls(persona_id, string(values.get("image", ""), 128),
                   string(values.get("appearance", "")), string(values.get("style", "")),
                   string(values.get("habits", "")), mode,
                   number(values.get("denoise", 0.55), 0.05, 1), auto,
                   string(values.get("persona_hash", ""), 64))
