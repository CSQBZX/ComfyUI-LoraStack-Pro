"""LoRA Stack Pro 节点。

一个节点完成整堆 LoRA 的叠加，堆内容由前端可视化面板维护，
序列化到隐藏 widget `lora_stack`（JSON 字符串）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict

import comfy.sd
import comfy.utils
import folder_paths

try:
    from . import lora_meta
except ImportError:  # 少数加载方式下没有包上下文
    import lora_meta  # type: ignore[no-redef]

LOGGER = logging.getLogger("LoraStackPro")

LORA_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".sft", ".gguf"}


def _as_float(value, default: float = 1.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "off", "no", "")
    if isinstance(value, (int, float)):
        return value != 0
    return default


def parse_stack(raw) -> list[dict]:
    """把 widget 里的 JSON 解析成规范化的堆条目，兼容 weilin 的字段命名。"""
    if raw is None or raw == "":
        return []
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            LOGGER.warning("[LoraStackPro] lora_stack 不是合法 JSON，按空堆处理")
            return []
    if isinstance(data, dict):
        data = next(
            (data[key] for key in ("stack", "loras", "lora", "temp_lora", "items")
             if isinstance(data.get(key), list)),
            [],
        )
    if not isinstance(data, list):
        return []

    parsed: list[dict] = []
    for item in data:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = (
            item.get("name") or item.get("lora") or item.get("path")
            or item.get("lora_name") or item.get("rel") or ""
        )
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.replace("\\", "/").strip().lstrip("/")

        enabled = True
        for key in ("enabled", "on", "active"):
            if key in item:
                enabled = _as_bool(item[key], True)
                break
        if item.get("muted") is True or item.get("hidden") is True:
            enabled = False

        base_weight = item.get("weight", 1.0)
        strength_model = _as_float(
            item.get("strength_model", item.get("sm", base_weight)), 1.0
        )
        strength_clip = _as_float(
            item.get("strength_clip", item.get("sc", item.get("text_encoder_weight", base_weight))),
            1.0,
        )
        parsed.append({
            "name": name,
            "strength_model": strength_model,
            "strength_clip": strength_clip,
            "enabled": enabled,
        })
    return parsed


def _lora_roots() -> list[str]:
    try:
        return [os.path.realpath(r) for r in folder_paths.get_folder_paths("loras") if r]
    except Exception:  # noqa: BLE001
        return []


def resolve_lora_path(name: str) -> str | None:
    """相对名优先走 ComfyUI 官方解析，绝对路径必须落在 loras 根目录内。"""
    try:
        full = folder_paths.get_full_path("loras", name)
    except Exception:  # noqa: BLE001
        full = None
    if full and os.path.isfile(full):
        return full
    if os.path.isabs(name) and os.path.isfile(name):
        real = os.path.realpath(name)
        for root in _lora_roots():
            try:
                if os.path.commonpath([real, root]) == root:
                    return real
            except ValueError:
                continue
    return None


def _cache_limit_bytes() -> int:
    """权重缓存上限，可用 LORA_STACK_PRO_CACHE_MB 覆盖；0 表示退回单条缓存。"""
    raw = os.environ.get("LORA_STACK_PRO_CACHE_MB", "").strip()
    if raw:
        try:
            return max(0, int(float(raw))) * 1024 * 1024
        except ValueError:
            pass
    return 3072 * 1024 * 1024


class _LoraWeightCache:
    """按路径缓存已加载的 LoRA 权重，按总字节数封顶的 LRU。

    为什么不沿用「只留最近一个」：堆内容（含权重）被写进了 IS_CHANGED 的哈希，
    所以点一次 ± 按钮就会让整个堆重新执行。只留一个条目时，改第 8 项的权重
    会把前 7 项全部重新读盘——SDXL LoRA 单个 100-200MB，一次调参就是几百 MB
    的无谓磁盘 IO。改成按路径命中后，同样的操作只剩内存查找。
    """

    __slots__ = ("_lock", "_entries", "_bytes", "_limit")

    def __init__(self, limit_bytes: int | None = None) -> None:
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, tuple[dict, dict | None, int]] = OrderedDict()
        self._bytes = 0
        self._limit = _cache_limit_bytes() if limit_bytes is None else max(0, int(limit_bytes))

    @staticmethod
    def _weight_bytes(state) -> int:
        """估算一份权重占用的字节数，用于封顶淘汰。"""
        total = 0
        if isinstance(state, dict):
            for tensor in state.values():
                try:
                    total += int(tensor.numel()) * int(tensor.element_size())
                except Exception:  # noqa: BLE001 - 非张量条目直接跳过
                    continue
        return total

    def get(self, path: str):
        with self._lock:
            hit = self._entries.get(path)
            if hit is not None:
                self._entries.move_to_end(path)
                return hit[0], hit[1]
        state, meta = comfy.utils.load_torch_file(path, safe_load=True, return_metadata=True)
        size = self._weight_bytes(state)
        with self._lock:
            previous = self._entries.pop(path, None)
            if previous is not None:
                self._bytes -= previous[2]
            self._entries[path] = (state, meta, size)
            self._bytes += size
            # 至少保留一条，避免上限被设成 0 时把刚读进来的自己淘汰掉
            while self._bytes > self._limit and len(self._entries) > 1:
                _, (_, _, dropped) = self._entries.popitem(last=False)
                self._bytes -= dropped
        return state, meta

    def invalidate(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._bytes,
                "limit": self._limit,
            }


class LoraStackPro:
    """可视化 LoRA 堆：逐层浏览、跨层搜索、预览图 + Civitai 信息。"""

    CATEGORY = "loaders/lora"
    FUNCTION = "apply_stack"
    RETURN_TYPES = ("MODEL", "CLIP")
    RETURN_NAMES = ("model", "clip")
    OUTPUT_TOOLTIPS = ("叠加后的扩散模型", "叠加后的文本编码器")
    DESCRIPTION = (
        "LoRA 堆叠器。在节点面板里逐层浏览 loras 目录、跨子文件夹搜索、"
        "显示本地预览图并可查询 Civitai 信息,一次输出叠加后的 MODEL 与 CLIP。"
    )
    SEARCH_ALIASES = ["lora stack", "lora 堆", "多重 lora", "lora 管理器", "lora browser"]

    def __init__(self) -> None:
        self._cache = _LoraWeightCache()

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL", {"tooltip": "要叠加 LoRA 的扩散模型"}),
                "clip": ("CLIP", {"tooltip": "要叠加 LoRA 的文本编码器"}),
                "lora_stack": ("STRING", {
                    "default": "[]",
                    "multiline": False,
                    "tooltip": "由节点面板维护的 LoRA 堆（JSON），不要在文本框里手改",
                }),
            }
        }

    @staticmethod
    def _record_usage(rels) -> None:
        if not rels:
            return
        try:
            lora_meta.record_usage(rels)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[LoraStackPro] 记录使用失败: %s", exc)

    def apply_stack(self, model, clip, lora_stack):
        stack = parse_stack(lora_stack)
        used: list[str] = []

        for index, item in enumerate(stack):
            if not item["enabled"]:
                continue
            strength_model = item["strength_model"]
            strength_clip = item["strength_clip"]
            if strength_model == 0 and (clip is None or strength_clip == 0):
                continue

            path = resolve_lora_path(item["name"])
            if path is None:
                raise FileNotFoundError(
                    f"[LoraStackPro] 第 {index + 1} 项找不到 LoRA 文件: {item['name']}。"
                    "文件可能被移动或删除，请在节点面板中重新选择。"
                )

            try:
                state, metadata = self._cache.get(path)
            except Exception as exc:  # noqa: BLE001
                self._cache.invalidate()
                raise RuntimeError(
                    f"[LoraStackPro] 加载 LoRA 失败: {item['name']} -> {exc}"
                ) from exc

            model, clip = comfy.sd.load_lora_for_models(
                model, clip, state, strength_model, strength_clip, lora_metadata=metadata
            )
            used.append(item["name"])

        if used:
            LOGGER.info("[LoraStackPro] 已叠加 %d 个 LoRA", len(used))
            LOGGER.debug("[LoraStackPro] 顺序: %s", " -> ".join(used))
        self._record_usage(used)
        return (model, clip)

    @classmethod
    def IS_CHANGED(cls, model=None, clip=None, lora_stack="", **kwargs):
        parts = [str(lora_stack)]
        for item in parse_stack(lora_stack):
            if not item["enabled"]:
                continue
            path = resolve_lora_path(item["name"])
            if path is None:
                parts.append(f"{item['name']}:missing")
                continue
            try:
                stat = os.stat(path)
                parts.append(f"{item['name']}:{stat.st_mtime_ns}:{stat.st_size}")
            except OSError:
                parts.append(f"{item['name']}:stat_error")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class LoraStackProModelOnly(LoraStackPro):
    """只要 MODEL 的版本，省掉 CLIP 连线。"""

    RETURN_TYPES = ("MODEL",)
    RETURN_NAMES = ("model",)
    OUTPUT_TOOLTIPS = ("叠加后的扩散模型",)
    DESCRIPTION = "LoRA 堆叠器（仅 MODEL）。其余能力与 LoRA Stack Pro 完全一致。"

    @classmethod
    def INPUT_TYPES(cls):
        base = super().INPUT_TYPES()
        del base["required"]["clip"]
        return base

    def apply_stack(self, model, lora_stack):
        stack = parse_stack(lora_stack)
        used: list[str] = []
        for index, item in enumerate(stack):
            if not item["enabled"] or item["strength_model"] == 0:
                continue
            path = resolve_lora_path(item["name"])
            if path is None:
                raise FileNotFoundError(
                    f"[LoraStackPro] 第 {index + 1} 项找不到 LoRA 文件: {item['name']}。"
                )
            try:
                state, metadata = self._cache.get(path)
            except Exception as exc:  # noqa: BLE001
                self._cache.invalidate()
                raise RuntimeError(
                    f"[LoraStackPro] 加载 LoRA 失败: {item['name']} -> {exc}"
                ) from exc
            model, _ = comfy.sd.load_lora_for_models(
                model, None, state, item["strength_model"], 0, lora_metadata=metadata
            )
            used.append(item["name"])
        self._record_usage(used)
        return (model,)


NODE_CLASS_MAPPINGS = {
    "LoraStackPro": LoraStackPro,
    "LoraStackProModelOnly": LoraStackProModelOnly,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "LoraStackPro": "LoRA Stack Pro",
    "LoraStackProModelOnly": "LoRA Stack Pro (Model Only)",
}
