"""Atomic storage for portraits and durable task state."""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid
from pathlib import Path

from PIL import Image

from .models import Json, Object, Portrait, parse_object


def revision(value: Object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root
        for name in ("portraits", "outputs"):
            (root / name).mkdir(parents=True, exist_ok=True)

    def read(self, name: str, default: Object | None = None) -> Object:
        path = self.root / name
        return parse_object(path.read_text("utf-8")) if path.exists() else dict(default or {})

    def write(self, name: str, value: Object) -> None:
        path = self.root / name
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def portrait(self, persona_id: str) -> Portrait:
        from .models import object_value
        value = self.read("portraits.json").get(persona_id)
        if value is None:
            raise ValueError("当前人格尚未设置画像，请在生图工作室中配置")
        return Portrait.read(object_value(value))

    def save_portrait(self, portrait: Portrait) -> None:
        if portrait.image:
            self.path("portraits", portrait.image)
        values = self.read("portraits.json")
        values[portrait.persona_id] = portrait.to_dict()
        self.write("portraits.json", values)

    def path(self, folder: str, name: str) -> Path:
        if folder not in ("portraits", "outputs") or not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("无效图片路径")
        path = (self.root / folder / name).resolve()
        if path.parent != (self.root / folder).resolve() or not path.is_file():
            raise ValueError("图片不存在")
        return path

    def save_image(self, folder: str, data: bytes) -> str:
        if folder not in ("portraits", "outputs") or len(data) > 20 * 1024 * 1024:
            raise ValueError("图片须小于 20 MB")
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 24000000:
                raise ValueError("图片像素过大")
            image.load()
            converted = image.convert("RGB")
        name = f"{uuid.uuid4().hex}.png"
        converted.save(self.root / folder / name)
        return name

    def record(self, job: Object) -> None:
        data = self.read("history.json", {"items": []})
        items = data.get("items", [])
        if not isinstance(items, list):
            raise ValueError("生成记录损坏")
        job_id = job.get("id")
        filtered: list[Json] = [item for item in items if not isinstance(item, dict) or item.get("id") != job_id]
        self.write("history.json", {"items": [job, *filtered][:200]})
