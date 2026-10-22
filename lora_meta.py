"""LoRA 元数据层。

信息来源按成本从低到高：
1. safetensors 头部 __metadata__（秒级，不读整个文件）
2. 旁挂文件（.weilin-info.json / .metadata.json / .civitai.info）里的 sha256
3. 全文件 sha256（慢，惰性 + 缓存）
4. Civitai by-hash API（联网，缓存到 data/civitai/）
"""

from __future__ import annotations

import atexit
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import uuid
from urllib.parse import quote

LOGGER = logging.getLogger("LoraStackPro")

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PLUGIN_DIR, "data")
CIVITAI_DIR = os.path.join(DATA_DIR, "civitai")
HASH_CACHE = os.path.join(DATA_DIR, "hash_cache.json")

MAX_HEADER_BYTES = 96 * 1024 * 1024
# 远程图片的硬上限：Civitai 的图最大也就几 MB，32 MB 足够宽裕。
# 没有这个上限时，一个指向几 GB 文件的 URL 会把内存或磁盘直接吃穿。
MAX_IMAGE_BYTES = 32 * 1024 * 1024
IMAGE_CHUNK = 64 * 1024
# 代理转发时只认这几种类型：远程说自己是 text/html 或 image/svg+xml 时
# 直接在 ComfyUI 同源下渲染，等于给了对方一个在本机端口里执行脚本的机会。
SAFE_IMAGE_TYPES = frozenset({
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/bmp",
})
CIVITAI_API = "https://civitai.com/api/v1/model-versions/by-hash/{}"
ALIAS_PATH = os.path.join(DATA_DIR, "aliases.json")
USAGE_PATH = os.path.join(DATA_DIR, "usage.json")
RECENT_LIMIT = 10
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

COMMON_TAGS = {
    "1girl", "1boy", "solo", "solo focus", "looking at viewer", "upper body", "full body",
    "smile", "simple background", "white background", "masterpiece", "best quality",
    "high quality", "absurdres", "commentary", "multiple views", "close-up", "portrait",
    "male focus", "female", "monochrome", "greyscale", "from side", "standing", "sitting",
}

_lock = threading.RLock()
_hash_cache: dict[str, dict] | None = None
_hash_dirty = 0
_hash_saved_at = 0.0


def _ensure_dirs() -> None:
    os.makedirs(CIVITAI_DIR, exist_ok=True)


def _load_hash_cache() -> dict[str, dict]:
    global _hash_cache
    with _lock:
        if _hash_cache is not None:
            return _hash_cache
        data: dict[str, dict] = {}
        try:
            if os.path.isfile(HASH_CACHE):
                with open(HASH_CACHE, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    data = raw
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 哈希缓存读取失败: %s", exc)
        _hash_cache = data
        return data


def _save_hash_cache(force: bool = False) -> None:
    """把哈希缓存落盘。

    批量补全会为每个 LoRA 调一次 get_sha256，而这个文件现在有一百多 KB；
    每加一条就整写一次等于把磁盘当草稿纸。这里累计到一定条数或间隔才写，
    `force=True` 用于进程退出前把尾巴刷掉。
    """
    global _hash_dirty, _hash_saved_at
    with _lock:
        if _hash_cache is None:
            return
        now = time.time()
        if not force and _hash_dirty < 20 and (now - _hash_saved_at) < 10.0:
            return
        _ensure_dirs()
        tmp = HASH_CACHE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(_hash_cache, handle, ensure_ascii=False)
            os.replace(tmp, HASH_CACHE)
            _hash_dirty = 0
            _hash_saved_at = now
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 哈希缓存写入失败: %s", exc)


def flush_hash_cache() -> None:
    _save_hash_cache(force=True)


atexit.register(flush_hash_cache)


def compute_sha256(abs_path: str, chunk: int = 4 * 1024 * 1024) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(abs_path, "rb") as handle:
            for block in iter(lambda: handle.read(chunk), b""):
                digest.update(block)
    except OSError as exc:
        LOGGER.warning("[LoraStackPro] 计算哈希失败 %s: %s", abs_path, exc)
        return None
    return digest.hexdigest()


def _read_json_safe(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _normalize_metadata(meta: dict) -> dict:
    """把训练器写的字符串化 JSON 字段还原成对象，并展平 modelspec.* 前缀。"""
    out = {}
    for key, value in meta.items():
        if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
            try:
                out[key] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        out[key] = value
    for key in list(out.keys()):
        if key.startswith("modelspec."):
            out.setdefault(key.split(".", 1)[1], out[key])
    return out


def read_header_metadata(abs_path: str) -> dict:
    """读取 safetensors 头部 __metadata__，不加载权重。"""
    if not abs_path.lower().endswith(".safetensors"):
        return {}
    try:
        with open(abs_path, "rb") as handle:
            head = handle.read(8)
            if len(head) < 8:
                return {}
            header_size = int.from_bytes(head, "little", signed=False)
            if header_size <= 0 or header_size > MAX_HEADER_BYTES:
                return {}
            raw = handle.read(header_size)
    except OSError:
        return {}
    try:
        header = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    meta = header.get("__metadata__")
    if not isinstance(meta, dict):
        return {}
    return _normalize_metadata(meta)


def sidecar_files(abs_path: str) -> list[str]:
    base = abs_path
    candidates = [
        base + ".weilin-info.json",
        base + ".metadata.json",
        base + ".civitai.info",
        base + ".json",
    ]
    return [c for c in candidates if os.path.isfile(c)]


def _hash_from_sidecar(abs_path: str) -> str | None:
    for path in sidecar_files(abs_path):
        data = _read_json_safe(path)
        if not data:
            continue
        for key in ("sha256", "hash", "modelHash", "model_hash"):
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
                return value.lower()
        raw = data.get("raw")
        if isinstance(raw, dict):
            for key in ("sha256", "hash"):
                value = raw.get(key)
                if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
                    return value.lower()
        meta = data.get("metadata")
        if isinstance(meta, dict):
            value = meta.get("sha256")
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value):
                return value.lower()
    return None


def get_sha256(rel: str, abs_path: str, size: int, mtime: float, allow_compute: bool = True) -> str | None:
    cache = _load_hash_cache()
    record = cache.get(rel)
    if record and record.get("size") == size and abs(record.get("mtime", 0) - mtime) < 2:
        value = record.get("sha256")
        if isinstance(value, str) and value:
            return value

    value = _hash_from_sidecar(abs_path)
    source = "sidecar"
    if not value and allow_compute:
        value = compute_sha256(abs_path)
        source = "computed"
    if not value:
        return None
    global _hash_dirty
    with _lock:
        cache[rel] = {"sha256": value, "size": size, "mtime": mtime, "source": source}
        _hash_dirty += 1
    _save_hash_cache()
    return value


def _civitai_cache_path(sha: str) -> str:
    return os.path.join(CIVITAI_DIR, f"{sha}.json")


def load_cached_civitai(sha: str | None, abs_path: str = "") -> dict | None:
    """只读本地已有的 Civitai 数据，不联网。

    顺序：LoRA 旁边的 .civitai.info → .weilin-info.json 里的 civitai 段
    → 插件内的 data/civitai/<sha>.json。
    """
    if abs_path:
        own = abs_path + ".civitai.info"
        if os.path.isfile(own):
            data = _read_json_safe(own)
            if isinstance(data, dict):
                inner = data.get("response")
                if isinstance(inner, dict) and inner:
                    return inner
                if data.get("modelId") or data.get("id"):
                    return data
        weilin = abs_path + ".weilin-info.json"
        if os.path.isfile(weilin):
            data = _read_json_safe(weilin)
            if isinstance(data, dict):
                raw = data.get("raw")
                if isinstance(raw, dict):
                    civitai = raw.get("civitai")
                    if isinstance(civitai, dict) and (civitai.get("modelId") or civitai.get("id")):
                        return civitai
    if sha:
        payload = _read_json_safe(_civitai_cache_path(sha))
        if isinstance(payload, dict):
            response = payload.get("response")
            if isinstance(response, dict) and response:
                return response
    return None


def has_cached_civitai(rel: str, abs_path: str, size: int = -1) -> bool:
    """快速判断某个 LoRA 有没有本地 Civitai 数据，只 stat 不读内容。

    load_cached_civitai 会把 .weilin-info.json 整个读进来；用户库里有四百多个
    这种文件，逐个读加起来能把统计拖到好几秒。批量任务的计划阶段只关心有或没有。
    """
    for suffix in (".civitai.info", ".weilin-info.json"):
        if os.path.isfile(abs_path + suffix):
            return True
    cache = _load_hash_cache()
    record = cache.get(rel)
    if not isinstance(record, dict):
        return False
    if size >= 0 and record.get("size") != size:
        return False
    sha = record.get("sha256")
    if not isinstance(sha, str) or not sha:
        return False
    return os.path.isfile(_civitai_cache_path(sha))


def _write_civitai_cache(sha: str, data: dict | None, abs_path: str, url: str) -> None:
    """写入两处：插件内的按哈希缓存，以及 LoRA 文件旁边的一份副本。

    旁挂文件直接写 Civitai 原始响应（不套 wrapper），
    这样 a1111 的 Civitai Helper 等工具也能直接读。
    """
    if sha:
        try:
            with open(_civitai_cache_path(sha), "w", encoding="utf-8") as handle:
                json.dump(
                    {"url": url, "timestamp": time.time(), "response": data},
                    handle, ensure_ascii=False,
                )
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] Civitai 缓存写入失败: %s", exc)

    if data and abs_path:
        sidecar = abs_path + ".civitai.info"
        try:
            with open(sidecar, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 无法写入 %s: %s", sidecar, exc)


def fetch_civitai(sha: str, abs_path: str = "", refresh: bool = False, timeout: float = 25.0) -> dict:
    """按 sha256 取 Civitai 模型版本信息。

    先看本地（旁挂文件优先），联网成功后再回写一份到 LoRA 文件旁边。
    """
    _ensure_dirs()

    if not refresh:
        cached = load_cached_civitai(sha, abs_path)
        if cached:
            return {"ok": True, "cached": True, "data": cached, "url": ""}

    if not sha:
        return {"ok": False, "error": "无法确定该文件的哈希，可能是超大文件或读取被占用"}

    url = CIVITAI_API.format(sha)
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "缺少 requests，无法访问 Civitai"}

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
    except Exception as exc:  # noqa: BLE001 - 网络异常要回传给前端展示
        return {"ok": False, "error": f"网络请求失败: {exc}", "url": url}

    if response.status_code == 404:
        _write_civitai_cache(sha, None, "", url)
        return {"ok": False, "error": "Civitai 上没有这个哈希对应的模型", "not_found": True, "url": url}
    if response.status_code != 200:
        return {"ok": False, "error": f"Civitai 返回 HTTP {response.status_code}", "url": url}

    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "error": "Civitai 返回内容无法解析", "url": url}

    _write_civitai_cache(sha, data, abs_path, url)
    return {"ok": True, "cached": False, "url": url, "data": data}


def _guess_trigger_words(meta: dict) -> list[str]:
    direct = []
    for key in ("ss_trigger_words", "trigger_words", "trainedWords", "ss_trained_words"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            direct.extend([w.strip() for w in re.split(r"[,，]", value) if w.strip()])
        elif isinstance(value, list):
            direct.extend([str(w).strip() for w in value if str(w).strip()])
    if direct:
        return list(dict.fromkeys(direct))[:12]

    freq = meta.get("ss_tag_frequency")
    if not isinstance(freq, dict):
        return []
    totals: dict[str, int] = {}
    max_count = 0
    for dataset in freq.values():
        if not isinstance(dataset, dict):
            continue
        for tag, count in dataset.items():
            if not isinstance(count, (int, float)):
                continue
            totals[tag] = totals.get(tag, 0) + int(count)
            max_count = max(max_count, totals[tag])
    if max_count <= 0:
        return []
    candidates = [
        tag for tag, count in totals.items()
        if count / max_count >= 0.7 and tag.lower() not in COMMON_TAGS and len(tag) <= 48
    ]
    candidates.sort(key=lambda t: -totals[t])
    return candidates[:10]


def local_info(rel: str, abs_path: str, size: int, mtime: float, allow_compute_hash: bool = False) -> dict:
    meta = read_header_metadata(abs_path)
    sha = get_sha256(rel, abs_path, size, mtime, allow_compute=allow_compute_hash)

    base_model = (
        meta.get("ss_base_model_version")
        or meta.get("baseModel")
        or meta.get("ss_sd_model_name")
        or ""
    )
    if isinstance(base_model, str):
        base_model = os.path.splitext(base_model)[0]

    info = {
        "rel": rel,
        "abs": abs_path,
        "size": size,
        "mtime": mtime,
        "sha256": sha or "",
        "metadata_keys": sorted(meta.keys())[:60],
        "output_name": meta.get("ss_output_name") or meta.get("title") or "",
        "base_model": base_model,
        "network_dim": meta.get("ss_network_dim") or "",
        "network_alpha": meta.get("ss_network_alpha") or "",
        "resolution": meta.get("ss_resolution") or "",
        "clip_skip": meta.get("ss_clip_skip") or "",
        "train_steps": meta.get("ss_steps") or meta.get("ss_max_train_steps") or "",
        "learning_rate": meta.get("ss_learning_rate") or "",
        "dataset_dirs": meta.get("ss_datasets") or "",
        "notes": (meta.get("ss_notes") or "")[:600] if isinstance(meta.get("ss_notes"), str) else "",
        "description": (meta.get("description") or "")[:800] if isinstance(meta.get("description"), str) else "",
        "triggers": cached_trained_words(sha, abs_path) or _guess_trigger_words(meta),
        "sidecars": [os.path.basename(p) for p in sidecar_files(abs_path)],
    }
    return info


def civitai_summary(payload: dict) -> dict:
    """把 Civitai 原始响应裁成前端够用的形状。"""
    data = payload.get("data") or {}
    model = data.get("model") or {}
    images = []
    for img in (data.get("images") or [])[:12]:
        if not isinstance(img, dict):
            continue
        url = img.get("url") or ""
        if not url:
            continue
        images.append({
            "url": url,
            "nsfw": img.get("nsfwLevel", 0) or img.get("nsfw", 0),
            "width": img.get("width", 0),
            "height": img.get("height", 0),
        })
    trained = []
    for key in ("trainedWords", "triggerWords"):
        value = data.get(key)
        if isinstance(value, list):
            trained.extend([str(w) for w in value if str(w).strip()])
    stats = data.get("stats") or {}
    model_id = data.get("modelId") or model.get("id")
    version_id = data.get("id")
    link = ""
    if model_id:
        link = f"https://civitai.com/models/{model_id}"
        if version_id:
            link += f"?modelVersionId={version_id}"
    return {
        "ok": bool(payload.get("ok")),
        "cached": payload.get("cached", False),
        "error": payload.get("error", ""),
        "not_found": payload.get("not_found", False),
        "model_name": model.get("name") or data.get("name") or "",
        "version_name": data.get("name") or "",
        "base_model": data.get("baseModel") or "",
        "type": model.get("type") or "",
        "nsfw": bool(model.get("nsfw")),
        "trained_words": list(dict.fromkeys(trained))[:24],
        "images": images,
        "link": link,
        "downloads": stats.get("downloadCount", 0),
        "thumbs_up": stats.get("thumbsUpCount", 0),
        "rating": stats.get("rating", 0),
        "published": data.get("publishedAt") or data.get("createdAt") or "",
        "description": (data.get("description") or "")[:1200] if isinstance(data.get("description"), str) else "",
    }


def safe_image_type(raw: str) -> str:
    """把远程 Content-Type 归到「可安全转发」的一侧。

    远程服务器完全可以把类型说成 text/html 或 image/svg+xml，浏览器在 ComfyUI
    的同源下渲染它，就等于在本机端口里执行了对方的脚本。所以只放行位图类型；
    声明为未知（空 / octet-stream）的仍按二进制流转发，因为不少图床就是这么返回的。
    """
    ct = (raw or "").split(";")[0].strip().lower()
    if ct in SAFE_IMAGE_TYPES:
        return ct
    if ct in ("", "application/octet-stream", "binary/octet-stream"):
        return "application/octet-stream"
    return ""


def proxy_image(url: str, timeout: float = 20.0) -> tuple[bytes, str] | None:
    """把远程图片转给前端，规避可能的防盗链。

    流式读取并在超限时中止：直接读 `response.content` 会把整个响应先塞进内存，
    一个指向大文件的 URL 就能把进程撑爆。

    明确声明为非图片的响应一律不转发——这同时挡住了拿本接口当通用 HTTP 代理、
    去读内网 JSON 接口的用法。
    """
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        import requests
    except ImportError:
        return None
    try:
        with requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}, stream=True
        ) as response:
            if response.status_code != 200:
                return None
            content_type = safe_image_type(response.headers.get("Content-Type", ""))
            if not content_type:
                LOGGER.warning(
                    "[LoraStackPro] 代理目标不是图片，已拒绝: %s (%s)",
                    url[:120], response.headers.get("Content-Type", ""),
                )
                return None
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(IMAGE_CHUNK):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    LOGGER.warning(
                        "[LoraStackPro] 代理图片超过 %d MB，已中止: %s",
                        MAX_IMAGE_BYTES // 1024 // 1024, url[:120],
                    )
                    return None
                chunks.append(chunk)
            if not chunks:
                return None
            return b"".join(chunks), content_type
    except Exception:  # noqa: BLE001
        return None


def image_data_uri(abs_path: str, max_bytes: int = 6_000_000) -> str | None:
    """小图直接内联，减少一次往返。"""
    ext = os.path.splitext(abs_path)[1].lower()
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
        ".bmp": "image/bmp",
    }.get(ext)
    if not mime:
        return None
    try:
        import base64
        if os.path.getsize(abs_path) > max_bytes:
            return None
        with open(abs_path, "rb") as handle:
            raw = handle.read()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    except OSError:
        return None


def civitai_page_url(model_id) -> str:
    if not model_id:
        return ""
    return f"https://civitai.com/models/{model_id}"


def search_civitai_by_hash_url(sha: str) -> str:
    return CIVITAI_API.format(quote(sha, safe=""))


_TAG_BLOCK_RE = re.compile(r"(?i)</?(p|div|h[1-6]|li|tr|section|blockquote)[^>]*>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
_LI_RE = re.compile(r"(?i)<li[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """把 Civitai 上的 HTML 描述压成可读纯文本。"""
    if not text:
        return ""
    out = _BR_RE.sub("\n", str(text))
    out = _LI_RE.sub("\n· ", out)
    out = _TAG_BLOCK_RE.sub("\n", out)
    out = _TAG_RE.sub("", out)
    out = html.unescape(out)
    out = re.sub(r"[ \t\u00a0]+", " ", out)
    out = re.sub(r"\n[ \t]+", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def fetch_model_description(model_id, timeout: float = 20.0) -> dict:
    """取某个 Civitai 模型的简介。

    注意：by-hash 和 model-versions 两个接口都不带 description，
    只有 models/{modelId} 有，所以这一步是独立的按需请求。
    """
    if not model_id:
        return {"ok": False, "error": "缺少 modelId"}
    _ensure_dirs()
    cache_path = os.path.join(CIVITAI_DIR, f"desc-{model_id}.json")
    if os.path.isfile(cache_path):
        data = _read_json_safe(cache_path)
        if isinstance(data, dict) and "text" in data:
            text = str(data.get("text") or "")
            if text:
                return {"ok": True, "cached": True, "text": text}
            return {"ok": False, "cached": True, "error": "这个模型在 Civitai 上没有填写描述"}

    url = f"https://civitai.com/api/v1/models/{model_id}"
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "缺少 requests"}

    try:
        response = requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"网络请求失败: {exc}"}
    if response.status_code != 200:
        return {"ok": False, "error": f"Civitai 返回 HTTP {response.status_code}"}
    try:
        data = response.json()
    except ValueError:
        return {"ok": False, "error": "Civitai 返回内容无法解析"}

    text = strip_html(str(data.get("description") or ""))[:3000]
    try:
        with open(cache_path, "w", encoding="utf-8") as handle:
            json.dump({"modelId": model_id, "text": text}, handle, ensure_ascii=False)
    except OSError as exc:
        LOGGER.warning("[LoraStackPro] 描述缓存写入失败: %s", exc)

    if not text:
        return {"ok": False, "error": "这个模型在 Civitai 上没有填写描述"}
    return {"ok": True, "cached": False, "text": text}


# ------------------------------------------------------------------ 手动备注
_alias_lock = threading.RLock()
_alias_cache: dict[str, str] | None = None


def load_aliases(force: bool = False) -> dict[str, str]:
    """读取「文件相对路径 → 用户别名」映射。只是界面显示层，绝不触碰磁盘文件名。"""
    global _alias_cache
    with _alias_lock:
        if _alias_cache is not None and not force:
            return dict(_alias_cache)
        data: dict[str, str] = {}
        try:
            if os.path.isfile(ALIAS_PATH):
                with open(ALIAS_PATH, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        if isinstance(value, str) and value.strip():
                            data[str(key).replace("\\", "/").strip("/")] = value.strip()
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 别名表读取失败: %s", exc)
        _alias_cache = data
        return dict(data)


def save_alias(rel: str, alias: str) -> dict[str, str]:
    """设置或清除某个 LoRA 的别名，返回更新后的全量表。"""
    global _alias_cache
    with _alias_lock:
        data = load_aliases()
        key = (rel or "").replace("\\", "/").strip("/")
        alias = (alias or "").strip()
        if not key:
            return dict(data)
        if alias:
            data[key] = alias[:120]
        else:
            data.pop(key, None)
        _ensure_dirs()
        tmp = ALIAS_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, ALIAS_PATH)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 别名表写入失败: %s", exc)
        _alias_cache = data
        return dict(data)


def _norm_rel(value) -> str:
    """统一成 `/` 分隔、无首尾斜杠的相对路径。"""
    return str(value or "").replace("\\", "/").strip("/")


# ------------------------------------------------------------------ 封面表
COVER_PATH = os.path.join(DATA_DIR, "covers.json")
_cover_lock = threading.RLock()
_cover_cache: dict[str, str] | None = None

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp"}


def load_covers() -> dict[str, str]:
    """「相对路径 → 封面图文件名」表，只记文件名不记绝对路径。"""
    global _cover_cache
    with _cover_lock:
        if _cover_cache is not None:
            return dict(_cover_cache)
        data: dict[str, str] = {}
        try:
            if os.path.isfile(COVER_PATH):
                with open(COVER_PATH, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    for key, value in raw.items():
                        if isinstance(value, str) and value.strip():
                            data[_norm_rel(key)] = os.path.basename(value.replace("\\", "/"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 封面表读取失败: %s", exc)
        _cover_cache = data
        return dict(data)


def save_cover(rel: str, filename: str) -> dict[str, str]:
    """指定某个 LoRA 用哪张图当封面；filename 传空则恢复自动识别。"""
    global _cover_cache
    with _cover_lock:
        data = load_covers()
        key = _norm_rel(rel)
        name = os.path.basename(str(filename or "").replace("\\", "/"))
        if not key:
            return dict(data)
        if name:
            data[key] = name
        else:
            data.pop(key, None)
        _ensure_dirs()
        tmp = COVER_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, COVER_PATH)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 封面表写入失败: %s", exc)
        _cover_cache = data
        return dict(data)


def download_cover(url: str, target_dir: str, stem: str, timeout: float = 30.0) -> dict:
    """把 Civitai 样例图下载到 LoRA 同目录。

    命名为 `<stem>.preview.<ext>`，正好落在 _pick_thumb 的「同名派生」规则上，
    就算封面表丢了也能自动认出来。

    流式写入并限制总大小：超限时把那半截文件删掉，不在用户盘上留垃圾。
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "图片地址无效"}
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "缺少 requests，无法下载"}
    try:
        with requests.get(
            url, timeout=timeout, headers={"User-Agent": USER_AGENT}, stream=True
        ) as response:
            if response.status_code != 200:
                return {"ok": False, "error": f"下载失败 HTTP {response.status_code}"}

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = _MIME_EXT.get(content_type, "")
            if not ext:
                # 明确声称是别的类型（text/html 等）就别存了，否则会往 LoRA 目录里
                # 落一个内容是 HTML、后缀是 .preview.jpg 的东西
                if content_type and content_type not in ("", "application/octet-stream"):
                    return {"ok": False, "error": f"不是图片类型: {content_type}"}
                guess = os.path.splitext(url.split("?")[0])[1].lower()
                if guess not in _IMAGE_EXTS:
                    return {"ok": False, "error": "无法确定图片类型"}
                ext = guess

            safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem or "cover").strip() or "cover"
            filename = f"{safe_stem}.preview{ext}"
            target = os.path.join(target_dir, filename)
            total = 0
            aborted = False
            try:
                with open(target, "wb") as handle:
                    for chunk in response.iter_content(IMAGE_CHUNK):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            aborted = True
                            break
                        handle.write(chunk)
            except OSError as exc:
                return {"ok": False, "error": f"写入失败: {exc}"}

            if aborted or total == 0:
                try:
                    os.remove(target)
                except OSError:
                    pass
                if aborted:
                    return {
                        "ok": False,
                        "error": f"图片超过 {MAX_IMAGE_BYTES // 1024 // 1024} MB，已中止",
                    }
                return {"ok": False, "error": "图片内容为空"}
            return {"ok": True, "file": filename, "path": target, "bytes": total}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"下载失败: {exc}"}


# ------------------------------------------------------------ 使用记录 / 收藏
_usage_lock = threading.RLock()
_usage_cache: dict | None = None

def _copy_usage(data: dict) -> dict:
    return {
        "recent": [dict(item) for item in data.get("recent", [])],
        "favorites": list(data.get("favorites", [])),
    }


def load_usage() -> dict:
    """读取最近使用与收藏。最近列表只保留 RECENT_LIMIT 条。"""
    global _usage_cache
    with _usage_lock:
        if _usage_cache is not None:
            return _copy_usage(_usage_cache)
        data: dict = {"recent": [], "favorites": []}
        try:
            if os.path.isfile(USAGE_PATH):
                with open(USAGE_PATH, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
                if isinstance(raw, dict):
                    recent = raw.get("recent")
                    if isinstance(recent, list):
                        items = []
                        for item in recent:
                            if not isinstance(item, dict):
                                continue
                            rel = _norm_rel(item.get("rel"))
                            if rel:
                                try:
                                    ts = float(item.get("ts") or 0)
                                except (TypeError, ValueError):
                                    ts = 0.0
                                items.append({"rel": rel, "ts": ts})
                        items.sort(key=lambda x: -x["ts"])
                        data["recent"] = items[:RECENT_LIMIT]
                    favorites = raw.get("favorites")
                    if isinstance(favorites, list):
                        data["favorites"] = [r for r in (_norm_rel(x) for x in favorites) if r]
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 使用记录读取失败: %s", exc)
        _usage_cache = data
        return _copy_usage(data)


def _save_usage(data: dict) -> dict:
    global _usage_cache
    with _usage_lock:
        _ensure_dirs()
        tmp = USAGE_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, USAGE_PATH)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 使用记录写入失败: %s", exc)
        _usage_cache = data
        return _copy_usage(data)


def record_usage(rels) -> dict:
    """节点真正执行时调用：把参与生成的 LoRA 记进最近列表（上限 10）。"""
    data = load_usage()
    now = time.time()
    merged = {item["rel"]: item["ts"] for item in data["recent"]}
    for value in rels or []:
        rel = _norm_rel(value)
        if rel:
            merged[rel] = now
    ranked = sorted(merged.items(), key=lambda kv: -kv[1])[:RECENT_LIMIT]
    data["recent"] = [{"rel": rel, "ts": ts} for rel, ts in ranked]
    return _save_usage(data)


def toggle_favorite(rel: str) -> dict:
    data = load_usage()
    key = _norm_rel(rel)
    if key:
        if key in data["favorites"]:
            data["favorites"].remove(key)
        else:
            data["favorites"].append(key)
    return _save_usage(data)


# ------------------------------------------------------------------ 堆预设
PRESET_PATH = os.path.join(DATA_DIR, "presets.json")
MAX_PRESETS = 50
_preset_lock = threading.RLock()
_preset_cache: list | None = None


def _normalize_preset_items(items) -> list[dict]:
    out: list[dict] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        rel = _norm_rel(item.get("name") or item.get("lora") or item.get("path"))
        if not rel:
            continue
        try:
            sm = float(item.get("sm", item.get("strength_model", 1.0)))
        except (TypeError, ValueError):
            sm = 1.0
        try:
            sc = float(item.get("sc", item.get("strength_clip", 1.0)))
        except (TypeError, ValueError):
            sc = 1.0
        out.append({
            "name": rel,
            "alias": str(item.get("alias") or "")[:60],
            "sm": max(-10.0, min(10.0, sm)),
            "sc": max(-10.0, min(10.0, sc)),
            "enabled": bool(item.get("enabled", True)),
        })
    return out[:64]


def _copy_presets(data: list) -> list[dict]:
    return [dict(item) for item in data]


def load_presets() -> list[dict]:
    global _preset_cache
    with _preset_lock:
        if _preset_cache is not None:
            return _copy_presets(_preset_cache)
        data: list[dict] = []
        try:
            if os.path.isfile(PRESET_PATH):
                with open(PRESET_PATH, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
                presets = raw.get("presets") if isinstance(raw, dict) else raw
                if isinstance(presets, list):
                    for item in presets:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        if not name:
                            continue
                        data.append({
                            "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                            "name": name[:80],
                            "items": _normalize_preset_items(item.get("items")),
                            "updated": float(item.get("updated") or 0),
                        })
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 预设读取失败: %s", exc)
        _preset_cache = data
        return _copy_presets(data)


def _save_presets(data: list[dict]) -> list[dict]:
    global _preset_cache
    with _preset_lock:
        _ensure_dirs()
        tmp = PRESET_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"presets": data}, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, PRESET_PATH)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 预设写入失败: %s", exc)
        _preset_cache = data
        return _copy_presets(data)


def save_preset(name: str, items, preset_id: str = "") -> list[dict]:
    """preset_id 为空则新建，否则覆盖同名那条。"""
    data = load_presets()
    name = str(name or "").strip()[:80]
    if not name:
        return data
    normalized = _normalize_preset_items(items)
    now = time.time()

    target = None
    if preset_id:
        target = next((p for p in data if p.get("id") == preset_id), None)
    if target is None:
        target = next((p for p in data if p.get("name") == name), None)

    if target is not None:
        target["name"] = name
        target["items"] = normalized
        target["updated"] = now
    else:
        data.append({
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "items": normalized,
            "updated": now,
        })
        if len(data) > MAX_PRESETS:
            data.sort(key=lambda p: -float(p.get("updated") or 0))
            data = data[:MAX_PRESETS]
    data.sort(key=lambda p: -float(p.get("updated") or 0))
    return _save_presets(data)


def delete_preset(preset_id: str) -> list[dict]:
    data = [p for p in load_presets() if p.get("id") != preset_id]
    return _save_presets(data)


# ------------------------------------------------------------------ 触发词
def cached_trained_words(sha: str | None, abs_path: str = "") -> list[str]:
    """从本地已有的 Civitai 数据里取训练触发词，不联网。"""
    response = load_cached_civitai(sha, abs_path)
    if not response:
        return []
    words: list[str] = []
    seen: set[str] = set()
    for key in ("trainedWords", "triggerWords"):
        value = response.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            low = text.lower()
            if low in seen:
                continue
            seen.add(low)
            words.append(text)
    return words
