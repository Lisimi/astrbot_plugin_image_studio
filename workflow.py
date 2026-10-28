"""Build an API graph from the user's Anima sampling chain."""

from .models import Object, Portrait, Settings


def build_workflow(settings: Settings, portrait: Portrait, prompt: str, seed: int, image: str = "") -> Object:
    graph: Object = {
        "206": {"class_type": "UNETLoader", "inputs": {"unet_name": settings.unet, "weight_dtype": "default"}},
        "207": {"class_type": "CLIPLoader", "inputs": {"clip_name": settings.clip, "type": "qwen_image", "device": "default"}},
        "205": {"class_type": "VAELoader", "inputs": {"vae_name": settings.vae}},
        "218": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["207", 0], "text": prompt}},
        "214": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["207", 0], "text": settings.negative}},
        "208": {"class_type": "EmptyLatentImage", "inputs": {"width": settings.width, "height": settings.height, "batch_size": 1}},
        "220": {"class_type": "KSampler", "inputs": {"model": ["206", 0], "positive": ["218", 0], "negative": ["214", 0], "latent_image": ["208", 0], "seed": seed, "steps": settings.steps, "cfg": settings.cfg, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "221": {"class_type": "VAEDecode", "inputs": {"samples": ["220", 0], "vae": ["205", 0]}},
        "222": {"class_type": "SaveImage", "inputs": {"images": ["221", 0], "filename_prefix": "astrbot_image_studio"}},
    }
    if portrait.mode == "img2img":
        if not image:
            raise ValueError("参考图重绘需要先上传画像")
        graph.pop("208")
        graph["398"] = {"class_type": "LoadImage", "inputs": {"image": image}}
        graph["425"] = {"class_type": "ImageScale", "inputs": {"image": ["398", 0], "upscale_method": "lanczos", "width": settings.width, "height": settings.height, "crop": "center"}}
        graph["428"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["425", 0], "vae": ["205", 0]}}
        sampler = graph["220"]
        assert isinstance(sampler, dict) and isinstance(sampler["inputs"], dict)
        sampler["inputs"].update(latent_image=["428", 0], denoise=portrait.denoise)
    return graph
