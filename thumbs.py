"""预览图缩略图缓存。

用户的预览图动辄 1-5 MB（本库中位数 1.8 MB、P90 4.4 MB、最大 31.7 MB），
而卡片上只需要显示 120px 左右。原来 /thumb 直接把原图发出去，一屏 30 张卡
就是 55~131 MB 的传输和近 1 GB 的解码位图。

这里把原图缩到目标宽度，按「绝对路径 + 文件 mtime + 宽度」缓存成 WebP。
换封面会改 mtime，key 自然失效，不需要额外清理逻辑。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading

LOGGER = logging.getLogger("LoraStackPro")

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PLUGIN_DIR, "data", "thumbs")

DEFAULT_WIDTH = 256
MAX_CACHE_BYTES = 512 * 1024 * 1024   # 超过就按最久未用清一批
QUALITY = 82

_write_lock = threading.RLock()
_trim_lock = threading.Lock()
_inflight_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}
_since_trim = 0


def _cache_file(abs_path: str, width: int, mtime: float) -> str:
    key = f"{abs_path}|{int(mtime)}|{width}"
    digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return os.path.join(CACHE_DIR, f"{digest}.webp")


def _trim_cache() -> None:
    """缓存总量超限时，删掉最旧的那批。只在生成新图后偶尔跑一次。"""
    try:
        items = []
        total = 0
        with os.scandir(CACHE_DIR) as handle:
            for dent in handle:
                try:
                    st = dent.stat()
                except OSError:
                    continue
                items.append((st.st_mtime, st.st_size, dent.path))
                total += st.st_size
        if total <= MAX_CACHE_BYTES:
            return
        items.sort()
        for _mtime, size, path in items:
            try:
                os.remove(path)
                total -= size
            except OSError:
                continue
            if total <= MAX_CACHE_BYTES * 0.8:
                break
        LOGGER.info("[LoraStackPro] 缩略图缓存已清理，当前约 %.0f MB", total / 1024 / 1024)
    except OSError:
        return


def ensure(abs_path: str, width: int, mtime: float) -> str | None:
    """确保缩略图存在，返回缓存文件路径；无法生成时返回 None（调用方退回原图）。"""
    if not abs_path or width <= 0:
        return None
    target = _cache_file(abs_path, width, mtime)
    if os.path.isfile(target):
        return target

    # 同一张图被并发请求时只让一个线程去解码。原图最大 31 MB，
    # 一屏 20 张卡同时进来时重复解码会把 CPU 和内存一起打满。
    with _inflight_lock:
        waiter = _inflight.get(target)
        leader = waiter is None
        if leader:
            waiter = threading.Event()
            _inflight[target] = waiter
    if not leader:
        waiter.wait(30.0)
        return target if os.path.isfile(target) else None
    try:
        return _generate(abs_path, width, target)
    finally:
        with _inflight_lock:
            _inflight.pop(target, None)
        waiter.set()


def _generate(abs_path: str, width: int, target: str) -> str | None:
    """真正解码并写缓存。只由 ensure 里选出的唯一线程调用。"""
    try:
        from PIL import Image
    except ImportError:
        LOGGER.debug("[LoraStackPro] 没有 Pillow，缩略图功能停用")
        return None

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError as exc:
        LOGGER.debug("[LoraStackPro] 无法创建缩略图目录: %s", exc)
        return None

    tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with Image.open(abs_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA")
            img.thumbnail((width, width), Image.LANCZOS)
            img.save(tmp, "WEBP", quality=QUALITY, method=4)
    except Exception as exc:  # noqa: BLE001 - 解码失败就回退原图，不能让浏览挂掉
        LOGGER.debug("[LoraStackPro] 缩略图生成失败 %s: %s", abs_path, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None

    try:
        with _write_lock:
            os.replace(tmp, target)
    except OSError as exc:
        LOGGER.debug("[LoraStackPro] 缩略图写入失败: %s", exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None

    # 每建够一批就检查一次缓存总量，不必每张都去扫目录
    global _since_trim
    with _trim_lock:
        _since_trim += 1
        due = _since_trim >= 64
        if due:
            _since_trim = 0
    if due:
        # 扫描几万个缓存文件要几百毫秒，丢到后台，别拖住这次图片请求
        threading.Thread(
            target=_trim_cache, name="LoraStackPro-Trim", daemon=True
        ).start()
    return target


def cache_size() -> dict:
    """缓存现状，供设置面板显示。"""
    try:
        total = 0
        count = 0
        with os.scandir(CACHE_DIR) as handle:
            for dent in handle:
                try:
                    total += dent.stat().st_size
                    count += 1
                except OSError:
                    continue
        return {"files": count, "bytes": total, "limit": MAX_CACHE_BYTES}
    except OSError:
        return {"files": 0, "bytes": 0, "limit": MAX_CACHE_BYTES}


def clear() -> int:
    """清空缩略图缓存，返回删除的文件数。"""
    removed = 0
    try:
        with os.scandir(CACHE_DIR) as handle:
            for dent in handle:
                try:
                    os.remove(dent.path)
                    removed += 1
                except OSError:
                    continue
    except OSError:
        return 0
    LOGGER.info("[LoraStackPro] 已清空 %d 个缩略图缓存文件", removed)
    return removed
