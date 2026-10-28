"""Inspect API graphs and apply explicit, versioned input bindings."""

from __future__ import annotations

from copy import deepcopy
import json
import math

from .models import Json, Object, number, object_value, string
from .store import revision

ROLES = {"positive": "STRING", "negative": "STRING", "image": "STRING",
         "seed": "INT", "denoise": "FLOAT"}


def api_graph(value: object) -> Object:
    graph = deepcopy(object_value(value))
    if "nodes" in graph or "links" in graph:
        raise ValueError("这是编辑器格式，请从 ComfyUI 导出 API 格式 JSON 后导入")
    if not graph or len(graph) > 500:
        raise ValueError("API 工作流须包含 1 至 500 个节点")
    if len(json.dumps(graph, ensure_ascii=False, allow_nan=False).encode()) > 1024 * 1024:
        raise ValueError("API 工作流须小于 1 MB")
    for node_id, value in graph.items():
        if not node_id or not isinstance(value, dict):
            raise ValueError("API 节点格式错误")
        if not string(value.get("class_type"), 255):
            raise ValueError("节点类型不能为空")
        object_value(value.get("inputs"))
    return graph


def inputs(graph: Object, node_id: str) -> Object:
    return object_value(object_value(graph.get(node_id))["inputs"])


def target(graph: Object, value: Json) -> tuple[str, str]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("输入映射须为节点 ID 与字段名")
    node_id, field = string(value[0], 255), string(value[1], 255)
    if field not in inputs(graph, node_id):
        raise ValueError(f"映射输入已失效：{node_id}.{field}")
    return node_id, field


def spec_for(node: Object, field: str, info: Object) -> list[Json]:
    definition = object_value(info.get(string(node["class_type"]), {}))
    groups = object_value(definition.get("input", {}))
    for group in ("required", "optional"):
        spec = object_value(groups.get(group, {})).get(field)
        if isinstance(spec, list) and spec:
            if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
                choices = spec[1].get("options")
                if isinstance(choices, list):
                    return [choices, spec[1]]
            return spec
    raise ValueError(f"节点输入定义不存在：{node['class_type']}.{field}")


def valid_scalar(value: Json, spec: list[Json], label: str) -> None:
    kind = spec[0]
    options = object_value(spec[1]) if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if isinstance(kind, list):
        if value not in kind:
            raise ValueError(f"{label} 的选项/模型已不存在，请重新选择")
    elif kind in ("INT", "FLOAT"):
        low, high = options.get("min", -1e100), options.get("max", 1e100)
        if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
            raise ValueError("节点数值约束格式错误")
        parsed = number(value, low, high)
        if kind == "INT" and not parsed.is_integer():
            raise ValueError(f"{label} 必须为整数")
    elif kind == "BOOLEAN":
        if not isinstance(value, bool):
            raise ValueError(f"{label} 必须为布尔值")
    elif kind == "STRING":
        string(value, 100000)
    else:
        raise ValueError(f"{label} 使用不支持的字面量类型 {kind}")


def reachable(graph: Object, output: str) -> Object:
    selected: Object = {}
    visiting: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError("工作流存在循环连线")
        if node_id in selected:
            return
        node = object_value(graph.get(node_id))
        visiting.add(node_id)
        for value in inputs(graph, node_id).values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int):
                if isinstance(value[1], bool) or value[1] < 0:
                    raise ValueError("连线输出序号错误")
                visit(value[0])
        visiting.remove(node_id)
        selected[node_id] = node

    visit(output)
    return selected


def inspect_graph(graph: Object, info: Object) -> Object:
    fields: list[Json] = []
    outputs: list[Json] = []
    suggested: Object = {}
    for node_id, value in graph.items():
        node = object_value(value)
        kind = string(node["class_type"])
        title = object_value(node.get("_meta", {})).get("title", kind)
        if kind in ("SaveImage", "PreviewImage"):
            outputs.append({"id": node_id, "label": f"{node_id} · {title}"})
        for field, initial in inputs(graph, node_id).items():
            try:
                spec = spec_for(node, field, info)
            except ValueError:
                continue
            data_type = spec[0]
            if not isinstance(data_type, list) and data_type not in ("STRING", "INT", "FLOAT", "BOOLEAN"):
                continue
            linked = isinstance(initial, list)
            fields.append({"node": node_id, "field": field, "label": f"{node_id} · {title} / {field}",
                           "type": "ENUM" if isinstance(data_type, list) else data_type,
                           "choices": data_type if isinstance(data_type, list) else [],
                           "options": spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {},
                           "value": initial, "linked": linked})
            role = ""
            if kind == "CLIPTextEncode" and field == "text":
                # Infer polarity from sampler connections, never from every STRING input.
                for sampler in graph.values():
                    sampler_inputs = object_value(object_value(sampler).get("inputs", {}))
                    for polarity in ("positive", "negative"):
                        if sampler_inputs.get(polarity) == [node_id, 0]:
                            role = polarity
            elif kind == "LoadImage" and field == "image":
                role = "image"
            elif field in ("seed", "noise_seed") and data_type == "INT" and kind in ("KSampler", "KSamplerAdvanced", "RandomNoise"):
                role = "seed"
            if role and role not in suggested:
                suggested[role] = [node_id, field]
    if len(outputs) == 1:
        suggested["output"] = object_value(outputs[0])["id"]
    return {"fields": fields, "outputs": outputs, "suggested": suggested,
            "fingerprint": revision(graph), "nodes": len(graph)}


def render_workflow(config: Object, prompt: str, seed: int, negative: str, image: str = "", denoise: float = .55) -> Object:
    graph = api_graph(config.get("graph"))
    mapping = object_value(config.get("mapping"))
    if set(mapping) - {*ROLES, "output"} or not mapping.get("positive") or not mapping.get("output"):
        raise ValueError("请设置正面提示词与图片输出映射")
    used: set[tuple[str, str]] = set()
    replacements: dict[str, Json] = {"positive": prompt, "negative": string(config.get("negative", negative), 100000), "seed": seed, "image": image, "denoise": denoise}
    for role in ROLES:
        if not mapping.get(role):
            continue
        node_id, field = target(graph, mapping[role])
        if (node_id, field) in used:
            raise ValueError("多个用途不能绑定同一个输入")
        used.add((node_id, field))
        if role == "image" and not image:
            raise ValueError("所选工作流需要该人格的参考图")
        inputs(graph, node_id)[field] = replacements[role]
    overrides = config.get("overrides", [])
    if not isinstance(overrides, list):
        raise ValueError("参数覆盖格式错误")
    for raw in overrides:
        item = object_value(raw)
        node_id, field = target(graph, [item.get("node"), item.get("field")])
        if (node_id, field) in used or isinstance(inputs(graph, node_id)[field], list):
            raise ValueError("不能覆盖映射输入、重复参数或连线输入")
        used.add((node_id, field))
        inputs(graph, node_id)[field] = item.get("value")
    output = string(mapping["output"], 255)
    if object_value(graph.get(output)).get("class_type") not in ("SaveImage", "PreviewImage"):
        raise ValueError("图片输出须选择 SaveImage 或 PreviewImage")
    return reachable(graph, output)


def validate_workflow(config: Object, info: Object) -> None:
    original = api_graph(config.get("graph"))
    mapping = object_value(config.get("mapping"))
    for role, expected in ROLES.items():
        if mapping.get(role):
            node_id, field = target(original, mapping[role])
            spec = spec_for(object_value(original[node_id]), field, info)
            if spec[0] != expected and not (role == "image" and isinstance(spec[0], list)):
                raise ValueError(f"{role} 映射类型须为 {expected}")
            if role == "image":
                options = object_value(spec[1]) if len(spec) > 1 and isinstance(spec[1], dict) else {}
                node = object_value(original[node_id])
                if options.get("image_upload") is not True and not (node["class_type"] == "LoadImage" and field == "image"):
                    raise ValueError("参考图映射须选择图片上传输入，例如 LoadImage.image")
    low, _ = seed_range(config, info)
    graph = render_workflow(config, "validation", low, "", "validation.png")
    image_binding = mapping.get("image")
    for node_id, value in graph.items():
        node = object_value(value)
        kind = string(node["class_type"])
        definition = object_value(info.get(kind, {}))
        if not definition:
            raise ValueError(f"ComfyUI 缺少节点：{kind}")
        if definition.get("api_node") is True:
            raise ValueError(f"首版仅支持本地节点，不支持远程 API 节点：{kind}")
        groups = object_value(definition.get("input", {}))
        values = inputs(graph, node_id)
        for field in object_value(groups.get("required", {})):
            if field not in values:
                raise ValueError(f"缺少必填输入：{node_id}.{field}")
        for field, initial in values.items():
            spec = spec_for(node, field, info)
            if isinstance(initial, list):
                if len(initial) != 2 or not isinstance(initial[0], str) or isinstance(initial[1], bool) or not isinstance(initial[1], int) or initial[1] < 0:
                    raise ValueError(f"连线格式错误：{node_id}.{field}")
                source = object_value(graph.get(string(initial[0])))
                source_info = object_value(info.get(string(source["class_type"]), {}))
                output_types = source_info.get("output", [])
                slot = initial[1]
                if not isinstance(slot, int) or not isinstance(output_types, list) or slot >= len(output_types):
                    raise ValueError(f"连线输出不存在：{node_id}.{field}")
                expected_type = "STRING" if isinstance(spec[0], list) else spec[0]
                flexible = ("*", "COMFY_MATCHTYPE_V3")
                if expected_type not in flexible and output_types[slot] not in (*flexible, expected_type):
                    raise ValueError(f"连线类型不匹配：{node_id}.{field}")
            elif image_binding == [node_id, field]:
                continue
            else:
                valid_scalar(initial, spec, f"{node_id}.{field}")
    # Mappings must affect the chosen output instead of silently targeting dead nodes.
    for role in ROLES:
        if mapping.get(role) and target(original, mapping[role])[0] not in graph:
            raise ValueError(f"{role} 映射不在所选图片输出的依赖链上")


def seed_range(config: Object, info: Object) -> tuple[int, int]:
    mapping = object_value(config.get("mapping", {}))
    if not mapping.get("seed"):
        return 0, 2**50 - 1
    graph = object_value(config["graph"])
    node_id, field = target(graph, mapping["seed"])
    spec = spec_for(object_value(graph[node_id]), field, info)
    options = object_value(spec[1]) if len(spec) > 1 and isinstance(spec[1], dict) else {}
    lower, upper = options.get("min", 0), options.get("max", 2**50 - 1)
    if not isinstance(lower, (int, float)) or not isinstance(upper, (int, float)):
        raise ValueError("种子范围格式错误")
    low, high = max(0, math.ceil(lower)), min(2**50 - 1, math.floor(upper))
    if high < low:
        raise ValueError("种子范围不支持自动随机")
    return low, high
