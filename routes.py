"""HTTP 接口层。

所有接口挂在 /lora_stack_pro 下：
  GET  stats     索引状态
  POST rescan    重建索引
  GET  tree      列一层目录
  GET  search    跨层搜索
  GET  thumb     预览图（lora 或文件夹）
  GET  info      本地元数据（safetensors 头部 + 旁挂文件）
  GET  civitai   Civitai 信息（按 sha256 查询，带缓存）
  GET  proxy     转发 Civitai 远程图片
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

from aiohttp import web

try:
    from . import lora_meta, thumbs
    from .lora_index import INDEX, norm_path, thumb_url
    from .prefetch import QUEUE as PREFETCH
except ImportError:  # 少数加载方式下没有包上下文
    import lora_meta  # type: ignore[no-redef]
    import thumbs  # type: ignore[no-redef]
    from lora_index import INDEX, norm_path, thumb_url  # type: ignore[no-redef]
    from prefetch import QUEUE as PREFETCH  # type: ignore[no-redef]

LOGGER = logging.getLogger("LoraStackPro")

_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
}


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


async def _ensure_index(timeout: float = 60.0) -> bool:
    return await asyncio.to_thread(INDEX.ensure_ready, timeout)


# --------------------------------------------------------------------- 索引
async def stats(request: web.Request) -> web.Response:
    return web.json_response(INDEX.stats())


async def rescan(request: web.Request) -> web.Response:
    INDEX.build_in_background()
    return web.json_response({"ok": True, "scanning": True})


async def tree(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引正在构建，请稍后再试", "scanning": True}, status=503)
    path = request.query.get("path", "")
    focus = request.query.get("focus", "")
    sort = request.query.get("sort", "name")
    filter_kind = request.query.get("filter", "")
    data = await asyncio.to_thread(INDEX.list_dir, path, focus, sort, filter_kind)
    return web.json_response(data)


def _dumps_utf8(data) -> str:
    """不把中文转义成 \\uXXXX：搜索结果里的中文路径占大头，转义后每个字多一倍字节。"""
    return json.dumps(data, ensure_ascii=False)


async def search(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引正在构建，请稍后再试", "scanning": True}, status=503)
    query = request.query.get("q", "")
    try:
        limit = max(1, min(int(request.query.get("limit", "400")), 2000))
    except ValueError:
        limit = 400
    try:
        offset = max(0, int(request.query.get("offset", "0")))
    except ValueError:
        offset = 0
    include_tags = request.query.get("tags", "1").lower() not in ("0", "false", "no")
    scope = request.query.get("scope", "all").lower()
    data = await asyncio.to_thread(INDEX.search, query, limit, offset, include_tags, scope)
    return web.json_response(data, dumps=_dumps_utf8)


# --------------------------------------------------------------------- 图片
def _thumb_for(rel: str, dir_path: str) -> tuple[str, float]:
    """返回 (预览图绝对路径, 该图自身的 mtime)。

    版本号必须用图片自己的 mtime：用 LoRA 文件的 mtime 在换封面时恒不变化，
    浏览器会一直拿缓存里的旧图（详见 HANDOVER 5.14）。
    """
    if rel:
        entry = INDEX.get(rel)
        if entry and entry.thumb:
            return entry.thumb, entry.thumb_mtime
    node = INDEX.folders.get(norm_path(dir_path)) if dir_path else None
    if node and node.thumb:
        return node.thumb, node.mtime
    if not dir_path and not rel:
        for child in INDEX.children.get("", []):
            child_node = INDEX.folders.get(child)
            if child_node and child_node.thumb:
                return child_node.thumb, child_node.mtime
    return "", 0.0


async def thumb(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    rel = request.query.get("rel", "")
    dir_path = request.query.get("dir", "")
    img = request.query.get("img", "")
    want_full = _truthy(request.query.get("full"), False)
    try:
        width = int(request.query.get("w", "0") or 0)
    except ValueError:
        width = 0
    if width <= 0:
        width = thumbs.DEFAULT_WIDTH
    width = max(64, min(width, 2048))

    abs_path = ""
    mtime = 0.0
    if img and rel:
        entry = INDEX.get(rel)
        if entry is None:
            return web.json_response({"error": "找不到该 LoRA"}, status=404)
        name = os.path.basename(img.replace("\\", "/"))
        candidate = os.path.join(os.path.dirname(entry.abspath), name)
        if name and os.path.isfile(candidate):
            abs_path = candidate
            try:
                mtime = os.path.getmtime(candidate)
            except OSError:
                mtime = 0.0
    else:
        abs_path, mtime = await asyncio.to_thread(_thumb_for, rel, dir_path)
    if not abs_path or not os.path.isfile(abs_path):
        return web.json_response({"error": "没有预览图"}, status=404)

    # URL 里已经带了图片 mtime 做版本号，可以放心让浏览器长期缓存
    long_cache = {"Cache-Control": "public, max-age=604800"}
    if want_full:
        mime = _IMAGE_MIME.get(os.path.splitext(abs_path)[1].lower(), "application/octet-stream")
        headers = {"Content-Type": mime}
        headers.update(long_cache)
        return web.FileResponse(abs_path, headers=headers)

    # 优先返回缩放后的 WebP：原图动辄 1-5 MB，卡片只需要一两百像素
    cached = await asyncio.to_thread(thumbs.ensure, abs_path, width, mtime)
    if cached:
        headers = {"Content-Type": "image/webp"}
        headers.update(long_cache)
        return web.FileResponse(cached, headers=headers)

    mime = _IMAGE_MIME.get(os.path.splitext(abs_path)[1].lower(), "application/octet-stream")
    headers = {"Content-Type": mime}
    headers.update(long_cache)
    return web.FileResponse(abs_path, headers=headers)


async def proxy(request: web.Request) -> web.Response:
    url = request.query.get("url", "")
    result = await asyncio.to_thread(lora_meta.proxy_image, url)
    if not result:
        return web.json_response({"error": "图片获取失败"}, status=404)
    raw, content_type = result
    return web.Response(
        body=raw,
        headers={
            # content_type 已在 proxy_image 里过了白名单；nosniff 再挡一层，
            # 免得浏览器把二进制流嗅探成可执行内容
            "Content-Type": content_type,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=86400",
        },
    )


# ----------------------------------------------------------------- 元数据
def _entry(rel: str):
    entry = INDEX.get(rel)
    if entry is None:
        raise KeyError(rel)
    return entry


async def info(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    rel = request.query.get("rel", "")
    allow_hash = _truthy(request.query.get("hash"), False)
    try:
        entry = _entry(rel)
    except KeyError:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)

    def work() -> dict:
        data = lora_meta.local_info(
            entry.rel, entry.abspath, entry.size, entry.mtime, allow_compute_hash=allow_hash
        )
        # 缩略图 URL 带上图片自身的 mtime，换封面后浏览器才会重新取图
        # 详情页的大图，用更大的目标宽度
        data["thumb"] = thumb_url(rel=entry.rel, mtime=entry.thumb_mtime, width=1024) if entry.thumb else ""
        data["folder"] = entry.folder
        data["images"] = INDEX.list_images(entry.rel)
        data["cover"] = INDEX.covers.get(entry.rel, "")
        # 指定封面指向的图片已经不在了：静默降级成自动识别，但要让用户看得见
        data["cover_stale"] = entry.rel in INDEX.stale_covers
        return data

    return web.json_response(await asyncio.to_thread(work))


async def civitai(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    rel = request.query.get("rel", "")
    refresh = _truthy(request.query.get("refresh"), False)
    allow_compute = not _truthy(request.query.get("nocompute"), False)
    try:
        entry = _entry(rel)
    except KeyError:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)

    def work() -> dict:
        sha = lora_meta.get_sha256(
            entry.rel, entry.abspath, entry.size, entry.mtime, allow_compute=allow_compute
        )
        payload = lora_meta.fetch_civitai(sha or "", entry.abspath, refresh=refresh)
        summary = lora_meta.civitai_summary(payload)
        summary["sha256"] = sha or ""
        summary["api"] = lora_meta.search_civitai_by_hash_url(sha) if sha else ""
        summary["cached_beside"] = os.path.isfile(entry.abspath + ".civitai.info")
        return summary

    return web.json_response(await asyncio.to_thread(work))


# --------------------------------------------------------------- 手动备注
async def aliases(request: web.Request) -> web.Response:
    """整张「相对路径 → 别名」表，前端启动时拉一次。"""
    data = await asyncio.to_thread(lora_meta.load_aliases)
    return web.json_response({"aliases": data})


async def set_alias(request: web.Request) -> web.Response:
    """设置或清除某个 LoRA 的显示别名（只改显示层，绝不触碰磁盘文件名）。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rel = str(payload.get("rel") or "").replace("\\", "/").strip("/")
    if not rel:
        return web.json_response({"error": "缺少 rel"}, status=400)
    alias = str(payload.get("alias") or "").strip()
    # 设非空别名才要求文件真实存在，否则 aliases.json 会被任意路径塞满垃圾。
    # 清除（alias 为空）不设这道门：文件删掉之后，旧别名仍然得能被清掉。
    if alias:
        if not await _ensure_index():
            return web.json_response({"error": "索引正在构建，请稍后再试", "scanning": True}, status=503)
        if INDEX.get(rel) is None:
            return web.json_response({"error": "找不到该 LoRA"}, status=404)
    data = await asyncio.to_thread(lora_meta.save_alias, rel, alias)
    await asyncio.to_thread(INDEX.refresh_aliases)
    return web.json_response({"ok": True, "aliases": data})


# ------------------------------------------------------ 最近使用 / 收藏 / 校验
async def usage(request: web.Request) -> web.Response:
    """最近使用（上限 10 条）与收藏，补全成前端可直接渲染的形状。"""
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    data = await asyncio.to_thread(lora_meta.load_usage)

    def build_payload() -> dict:
        recent = []
        for item in data.get("recent", []):
            rel = item.get("rel", "")
            entry = INDEX.entry_json(rel)
            if entry is None:
                # 文件被移动或删除，仍然列出来但标记失效
                name = rel.rsplit("/", 1)[-1]
                recent.append({
                    "rel": rel,
                    "path": rel,
                    "stem": name.rsplit(".", 1)[0],
                    "name": name,
                    "folder": rel.rsplit("/", 1)[0] if "/" in rel else "",
                    "thumb": "",
                    "alias": INDEX.aliases.get(rel, ""),
                    "missing": True,
                    "ts": item.get("ts", 0),
                })
                continue
            entry["ts"] = item.get("ts", 0)
            recent.append(entry)
        favorites = [entry for entry in (
            INDEX.entry_json(rel) for rel in data.get("favorites", [])
        ) if entry is not None]
        return {"recent": recent, "favorites": favorites}

    return web.json_response(await asyncio.to_thread(build_payload))


async def favorite(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rel = str(payload.get("rel") or "").replace("\\", "/").strip("/")
    if not rel:
        return web.json_response({"error": "缺少 rel"}, status=400)
    data = await asyncio.to_thread(lora_meta.toggle_favorite, rel)
    return web.json_response({"ok": True, "usage": data})


async def validate(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rels = payload.get("rels") or []
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)

    def work() -> dict:
        missing: list[str] = []
        duplicates: dict[str, list[str]] = {}
        for value in rels:
            rel = str(value or "").replace("\\", "/").strip("/")
            if not rel:
                continue
            if INDEX.get(rel) is None:
                missing.append(rel)
                continue
            others = INDEX.duplicate_relatives(rel)
            if others:
                duplicates[rel] = others
        return {"missing": missing, "duplicates": duplicates}

    return web.json_response(await asyncio.to_thread(work))


# ---------------------------------------------------------------- 堆预设
async def presets(request: web.Request) -> web.Response:
    return web.json_response({"presets": await asyncio.to_thread(lora_meta.load_presets)})


async def description(request: web.Request) -> web.Response:
    """按需拉取 Civitai 模型简介（by-hash 接口不带 description）。"""
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    rel = request.query.get("rel", "")
    entry = INDEX.get(rel)
    if entry is None:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)

    def work() -> dict:
        sha = lora_meta.get_sha256(
            entry.rel, entry.abspath, entry.size, entry.mtime, allow_compute=False
        )
        cached = lora_meta.load_cached_civitai(sha, entry.abspath)
        if not cached:
            return {"ok": False, "error": "还没有 C 站信息，先在详情里查询一次 Civitai"}
        return lora_meta.fetch_model_description(cached.get("modelId"))

    return web.json_response(await asyncio.to_thread(work))


async def save_preset(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    name = str(payload.get("name") or "").strip()
    items = payload.get("items") or []
    preset_id = str(payload.get("id") or "")
    if not name:
        return web.json_response({"error": "预设需要一个名字"}, status=400)
    if not items:
        return web.json_response({"error": "预设里至少要有一个 LoRA"}, status=400)
    data = await asyncio.to_thread(lora_meta.save_preset, name, items, preset_id)
    return web.json_response({"ok": True, "presets": data})


async def delete_preset(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    preset_id = str(payload.get("id") or "")
    if not preset_id:
        return web.json_response({"error": "缺少 id"}, status=400)
    data = await asyncio.to_thread(lora_meta.delete_preset, preset_id)
    return web.json_response({"ok": True, "presets": data})


async def reveal(request: web.Request) -> web.Response:
    """在系统文件管理器里定位该 LoRA 文件。只允许 loras 根目录内的路径。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rel = str(payload.get("rel") or "").replace("\\", "/").strip("/")
    entry = INDEX.get(rel)
    if entry is None:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)

    target = os.path.realpath(entry.abspath)
    inside = False
    for root in INDEX.roots():
        try:
            if os.path.commonpath([target, os.path.realpath(root)]) == os.path.realpath(root):
                inside = True
                break
        except ValueError:
            continue
    if not inside:
        return web.json_response({"error": "路径不在 loras 目录内"}, status=403)

    def work() -> None:
        import subprocess
        import sys
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", target])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(target)])

    try:
        await asyncio.to_thread(work)
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": f"打开失败: {exc}"}, status=500)
    return web.json_response({"ok": True, "path": target})


# ---------------------------------------------------------------- 封面管理
async def set_cover(request: web.Request) -> web.Response:
    """指定某个 LoRA 用哪张本地图当封面；file 传空则恢复自动识别。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rel = str(payload.get("rel") or "").replace("\\", "/").strip("/")
    filename = str(payload.get("file") or "")
    entry = INDEX.get(rel)
    if entry is None:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)

    def work() -> dict:
        lora_meta.save_cover(rel, filename)
        INDEX.refresh_covers()
        thumb_path = INDEX.recompute_thumb(rel)
        return {"ok": True, "cover": INDEX.covers.get(rel, ""), "thumb": thumb_path}

    return web.json_response(await asyncio.to_thread(work))


async def fetch_cover(request: web.Request) -> web.Response:
    """从 Civitai 样例图里下载一张，存到 LoRA 同目录并设为封面。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rel = str(payload.get("rel") or "").replace("\\", "/").strip("/")
    url = str(payload.get("url") or "")
    entry = INDEX.get(rel)
    if entry is None:
        return web.json_response({"error": "找不到该 LoRA"}, status=404)
    if not url:
        return web.json_response({"error": "缺少图片地址"}, status=400)

    def work() -> dict:
        folder = os.path.dirname(entry.abspath)
        result = lora_meta.download_cover(url, folder, entry.stem)
        if not result.get("ok"):
            return result
        lora_meta.save_cover(rel, result["file"])
        INDEX.refresh_covers()
        INDEX.set_thumb(rel, result["path"], "user")
        return result

    return web.json_response(await asyncio.to_thread(work))


# ---------------------------------------------------------------- 底模检测
# 顺序有意义：具体的衍生体系排在通用之前，免得被 sdxl 一类宽泛词抢走
_BASE_FAMILIES = (
    ("Illustrious", ("illustrious",)),
    ("NoobAI", ("noobai",)),
    ("Pony", ("pony",)),
    ("FLUX", ("flux",)),
    ("SD3", ("sd3", "sd35", "stable-diffusion-3")),
    ("SDXL", ("sdxl", "xl10", "sd_xl")),
    ("SD2x", ("sd2", "v21", "sdv2", "sd20")),
    ("SD15", ("sd15", "sd1.5", "v15", "sdv1")),
    ("Anima", ("anima",)),
)


def base_family(raw: str) -> str:
    """把各种底模写法归到少数几个族，用于发现堆里的混搭。"""
    text = (raw or "").strip().lower()
    if not text:
        return ""
    compact = re.sub(r"[\s_\-.]*", "", text)
    if not compact:
        return ""
    for name, keys in _BASE_FAMILIES:
        for key in keys:
            if key in compact:
                return name
    return ""


async def basemodels(request: web.Request) -> web.Response:
    """批量读堆里每个 LoRA 的底模标记，前端据此提示混搭风险。只读头部，不算哈希。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": "请求体不是合法 JSON"}, status=400)
    rels = payload.get("rels") or []
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)

    def work() -> dict:
        items: dict[str, dict] = {}
        families: dict[str, list[str]] = {}
        for value in rels:
            rel = str(value or "").replace("\\", "/").strip("/")
            if not rel:
                continue
            entry = INDEX.get(rel)
            if entry is None:
                continue
            meta = lora_meta.read_header_metadata(entry.abspath)
            raw = (
                meta.get("ss_base_model_version")
                or meta.get("baseModel")
                or meta.get("sd_model_name")
                or ""
            )
            raw = os.path.splitext(raw)[0] if isinstance(raw, str) else ""
            family = base_family(raw)
            items[rel] = {"raw": raw, "family": family}
            if family:
                families.setdefault(family, []).append(rel)
        return {
            "items": items,
            "families": families,
            "conflict": len(families) > 1,
            "unknown": [rel for rel, info in items.items() if not info.get("family")],
        }

    return web.json_response(await asyncio.to_thread(work))


# ------------------------------------------------------------ Civitai 批量补全
async def prefetch_plan(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    return web.json_response(await asyncio.to_thread(PREFETCH.plan))


async def prefetch_status(request: web.Request) -> web.Response:
    return web.json_response(PREFETCH.status())


async def prefetch_start(request: web.Request) -> web.Response:
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    result = await asyncio.to_thread(PREFETCH.start, payload)
    return web.json_response(result, status=200 if result.get("ok") else 409)


async def prefetch_stop(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "status": await asyncio.to_thread(PREFETCH.stop)})


# ---------------------------------------------------------------- 封面清理
async def prune_covers(request: web.Request) -> web.Response:
    """清掉 covers.json 里指向不存在图片的条目。"""
    if not await _ensure_index():
        return web.json_response({"error": "索引未就绪"}, status=503)
    result = await asyncio.to_thread(INDEX.prune_stale_covers)
    return web.json_response({"ok": True, **result})


# -------------------------------------------------------------- 缩略图缓存
async def thumb_stats(request: web.Request) -> web.Response:
    return web.json_response(await asyncio.to_thread(thumbs.cache_size))


async def thumb_clear(request: web.Request) -> web.Response:
    removed = await asyncio.to_thread(thumbs.clear)
    return web.json_response({"ok": True, "removed": removed})


ROUTE_TABLE = (
    ("GET", "/lora_stack_pro/stats", stats),
    ("POST", "/lora_stack_pro/rescan", rescan),
    ("GET", "/lora_stack_pro/tree", tree),
    ("GET", "/lora_stack_pro/search", search),
    ("GET", "/lora_stack_pro/thumb", thumb),
    ("GET", "/lora_stack_pro/proxy", proxy),
    ("GET", "/lora_stack_pro/info", info),
    ("GET", "/lora_stack_pro/civitai", civitai),
    ("GET", "/lora_stack_pro/aliases", aliases),
    ("POST", "/lora_stack_pro/alias", set_alias),
    ("GET", "/lora_stack_pro/usage", usage),
    ("POST", "/lora_stack_pro/favorite", favorite),
    ("POST", "/lora_stack_pro/validate", validate),
    ("POST", "/lora_stack_pro/reveal", reveal),
    ("GET", "/lora_stack_pro/presets", presets),
    ("POST", "/lora_stack_pro/presets/save", save_preset),
    ("POST", "/lora_stack_pro/presets/delete", delete_preset),
    ("GET", "/lora_stack_pro/description", description),
    ("POST", "/lora_stack_pro/cover", set_cover),
    ("POST", "/lora_stack_pro/cover/fetch", fetch_cover),
    ("POST", "/lora_stack_pro/covers/prune", prune_covers),
    ("GET", "/lora_stack_pro/thumbs/stats", thumb_stats),
    ("POST", "/lora_stack_pro/thumbs/clear", thumb_clear),
    ("POST", "/lora_stack_pro/basemodels", basemodels),
    ("GET", "/lora_stack_pro/prefetch/plan", prefetch_plan),
    ("GET", "/lora_stack_pro/prefetch/status", prefetch_status),
    ("POST", "/lora_stack_pro/prefetch/start", prefetch_start),
    ("POST", "/lora_stack_pro/prefetch/stop", prefetch_stop),
)


def register_routes() -> bool:
    try:
        from server import PromptServer
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("[LoraStackPro] 无法导入 PromptServer: %s", exc)
        return False
    instance = getattr(PromptServer, "instance", None)
    routes = getattr(instance, "routes", None)
    if routes is None:
        LOGGER.error("[LoraStackPro] PromptServer.routes 不可用，接口未注册")
        return False
    failed: list[str] = []
    for method, path, handler in ROUTE_TABLE:
        try:
            routes.route(method, path)(handler)
        except Exception as exc:  # noqa: BLE001
            # 单条失败不能让其余接口跟着不注册：那会表现成「面板能开但一片空白」，
            # 和「索引还在后台扫」几乎无法区分
            failed.append(f"{method} {path} ({exc})")
            LOGGER.warning("[LoraStackPro] 注册接口失败 %s %s: %s", method, path, exc)
    registered = len(ROUTE_TABLE) - len(failed)
    if failed:
        LOGGER.error(
            "[LoraStackPro] 已注册 %d/%d 个接口，失败的 %d 个: %s",
            registered, len(ROUTE_TABLE), len(failed), "; ".join(failed),
        )
    else:
        LOGGER.info("[LoraStackPro] 已注册 %d 个接口", registered)
    return not failed
