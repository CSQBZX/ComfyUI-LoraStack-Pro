"""Civitai 批量补全队列。

前端原来的整堆预取是串行 fetch 循环：关掉页面就断，而且每一项都要现算
整个文件的 sha256。上千个 LoRA 走那条路跑不完，这也是 data/civitai/ 长期
只有个位数缓存的直接原因。

这里把任务搬到后端线程：限速、可停止、续跑靠旁挂文件天然幂等，并且可以只
挑「没有预览图」的那部分先补。
"""

from __future__ import annotations

import logging
import os
import threading
import time

try:
    from . import lora_meta
    from .lora_index import INDEX
except ImportError:  # 少数加载方式下没有包上下文
    import lora_meta  # type: ignore[no-redef]
    from lora_index import INDEX  # type: ignore[no-redef]

LOGGER = logging.getLogger("LoraStackPro")

DEFAULT_DELAY = 1.2      # Civitai 匿名接口的礼貌间隔
MAX_DELAY = 12.0
MAX_LIMIT = 5000


def _truthy(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _int_of(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class PrefetchQueue:
    """单实例后台任务：按顺序给 LoRA 补 Civitai 信息，顺带下载缺失的封面。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state = self._blank_state()
        self._plan_cache: tuple[float, dict] | None = None

    @staticmethod
    def _blank_state() -> dict:
        return {
            "running": False,
            "planned": 0,
            "done": 0,
            "ok": 0,
            "cached": 0,
            "failed": 0,
            "covered": 0,
            "skipped": 0,
            "current": "",
            "delay": DEFAULT_DELAY,
            "started_at": 0.0,
            "finished_at": 0.0,
            "message": "",
            "error": "",
        }

    # ------------------------------------------------------------------ 统计
    @staticmethod
    def _has_local_payload(entry) -> bool:
        """旁挂文件或插件缓存里是否已经有这个 LoRA 的 C 站数据（不联网、不算哈希）。"""
        try:
            return lora_meta.has_cached_civitai(entry.rel, entry.abspath, entry.size)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _has_payload_fast(entry, hash_cache: dict) -> bool:
        """纯内存判断，一次文件系统调用都不做。

        扫描目录时已经顺手记下每个 LoRA 旁边有没有 .civitai.info / .weilin-info.json，
        这里直接读那个标记。统计接口每次打开面板都要跑，一次上千个 isfile 就是
        几百毫秒；真正开跑时再用精确版本核对。
        """
        if getattr(entry, "has_sidecar", False):
            return True
        record = hash_cache.get(entry.rel)
        return isinstance(record, dict) and bool(record.get("sha256"))

    def plan(self) -> dict:
        """看看还有多少活要干，不启动任何任务。统计结果短时间缓存，跑完会失效。"""
        now = time.time()
        with self._lock:
            cached = self._plan_cache
            if cached and now - cached[0] < 30:
                return dict(cached[1])
        entries = list(INDEX.entries)
        hash_cache = lora_meta._load_hash_cache()
        no_cover = 0
        cached_count = 0
        pending = 0
        for entry in entries:
            has_cover = bool(entry.thumb)
            has_payload = self._has_payload_fast(entry, hash_cache)
            if not has_cover:
                no_cover += 1
            if has_payload:
                cached_count += 1
            if not has_payload or not has_cover:
                pending += 1
        result = {
            "total": len(entries),
            "no_cover": no_cover,
            "cached": cached_count,
            "pending": pending,
            "default_delay": DEFAULT_DELAY,
        }
        with self._lock:
            self._plan_cache = (now, result)
        return dict(result)

    # ------------------------------------------------------------------ 生命周期
    def status(self) -> dict:
        with self._lock:
            data = dict(self._state)
        if data["running"] and data["planned"]:
            remain = max(0, data["planned"] - data["done"])
            data["remaining"] = remain
            data["percent"] = round(data["done"] * 100.0 / data["planned"], 1)
        else:
            data["remaining"] = 0
            data["percent"] = 100.0 if data["planned"] else 0.0
        return data

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._state["running"])

    def stop(self) -> dict:
        self._stop.set()
        with self._lock:
            if self._state["running"]:
                self._state["message"] = "正在停止…"
        return self.status()

    def start(self, options: dict | None = None) -> dict:
        options = options or {}
        if self.is_running():
            return {"ok": False, "error": "补全任务已经在跑了", "status": self.status()}

        only_missing_cover = _truthy(options.get("only_missing_cover"), True)
        skip_cached = _truthy(options.get("skip_cached"), True)
        with_cover = _truthy(options.get("download_cover"), True)
        limit = max(0, min(_int_of(options.get("limit"), 200), MAX_LIMIT))
        delay = _int_of(options.get("delay_ms"), int(DEFAULT_DELAY * 1000)) / 1000.0
        delay = max(0.0, min(delay, MAX_DELAY))

        entries = list(INDEX.entries)
        if not entries:
            return {"ok": False, "error": "索引还没就绪，稍后再试"}

        if only_missing_cover:
            entries = [entry for entry in entries if not entry.thumb]
        if skip_cached:
            entries = [entry for entry in entries if not self._has_local_payload(entry)]
        if limit:
            entries = entries[:limit]

        if not entries:
            return {"ok": False, "error": "没有需要补全的项（筛选后为空）"}

        self._stop.clear()
        with self._lock:
            self._state = self._blank_state()
            self._state.update({
                "running": True,
                "planned": len(entries),
                "delay": delay,
                "started_at": time.time(),
                "message": "已启动",
            })
        snapshot = list(entries)
        self._thread = threading.Thread(
            target=self._run,
            args=(snapshot, delay, with_cover),
            name="LoraStackPro-Prefetch",
            daemon=True,
        )
        self._thread.start()
        LOGGER.info("[LoraStackPro] 批量补全启动: %d 项, 间隔 %.2fs", len(snapshot), delay)
        return {"ok": True, "status": self.status()}

    # ------------------------------------------------------------------ 执行
    def _run(self, entries, delay: float, download_cover: bool) -> None:
        empty_streak = 0
        try:
            for index, entry in enumerate(entries):
                if self._stop.is_set():
                    break
                with self._lock:
                    self._state["current"] = entry.rel
                    self._state["done"] = index
                    self._state["message"] = f"处理中 {index + 1}/{len(entries)}"
                outcome, extra_delay = self._process(entry, download_cover)
                with self._lock:
                    self._state[outcome] = self._state.get(outcome, 0) + 1
                    self._state["done"] = index + 1

                # 连续失败说明可能被限流，逐步放慢但不至于卡死
                if outcome == "failed":
                    empty_streak += 1
                else:
                    empty_streak = 0
                step = delay
                if extra_delay:
                    step = max(step, extra_delay)
                elif empty_streak >= 3:
                    step = min(MAX_DELAY, max(step, 1.0) * 1.6)
                if empty_streak >= 8:
                    with self._lock:
                        self._state["message"] = "连续失败，已提前停止"
                    break
                if step > 0 and index + 1 < len(entries):
                    if self._stop.wait(step):
                        break
        except Exception as exc:  # noqa: BLE001 - 后台线程异常不能静默吞掉
            LOGGER.exception("[LoraStackPro] 批量补全线程异常")
            with self._lock:
                self._state["error"] = str(exc)
        finally:
            with self._lock:
                stopped = self._stop.is_set()
                self._state["running"] = False
                self._state["finished_at"] = time.time()
                self._plan_cache = None      # 跑过一轮，统计要重算
                if self._state["error"]:
                    self._state["message"] = "出错停止"
                elif stopped:
                    self._state["message"] = "已停止"
                else:
                    self._state["message"] = "已完成"
                self._state["current"] = ""
            LOGGER.info(
                "[LoraStackPro] 批量补全结束: ok=%s cached=%s failed=%s covered=%s skipped=%s",
                self._state["ok"], self._state["cached"], self._state["failed"],
                self._state["covered"], self._state["skipped"],
            )

    def _process(self, entry, download_cover: bool) -> tuple[str, float]:
        """处理单个 LoRA，返回 (结果分类, 建议的额外等待秒数)。"""
        try:
            sha = lora_meta.get_sha256(
                entry.rel, entry.abspath, entry.size, entry.mtime, allow_compute=True
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[LoraStackPro] 哈希失败 %s: %s", entry.rel, exc)
            return "failed", 0.0

        if not sha:
            return "skipped", 0.0

        try:
            payload = lora_meta.fetch_civitai(sha, entry.abspath)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[LoraStackPro] Civitai 请求失败 %s: %s", entry.rel, exc)
            return "failed", 0.0

        if not payload.get("ok"):
            error_text = str(payload.get("error") or "")
            if "429" in error_text or "rate" in error_text.lower():
                return "failed", 5.0
            return "failed", 0.0

        if download_cover and not entry.thumb:
            self._attach_cover(entry, payload)

        return ("cached" if payload.get("cached") else "ok"), 0.0

    def _attach_cover(self, entry, payload: dict) -> None:
        """把 C 站第一张样例图下载到 LoRA 同目录并设为封面。"""
        try:
            summary = lora_meta.civitai_summary(payload)
        except Exception:  # noqa: BLE001
            return
        url = ""
        for image in summary.get("images") or []:
            candidate = image.get("url") if isinstance(image, dict) else ""
            if candidate:
                url = candidate
                break
        if not url:
            return
        folder = os.path.dirname(entry.abspath)
        try:
            result = lora_meta.download_cover(url, folder, entry.stem)
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[LoraStackPro] 下载封面失败 %s: %s", entry.rel, exc)
            return
        if not result.get("ok"):
            return
        try:
            lora_meta.save_cover(entry.rel, result["file"])
            INDEX.refresh_covers()
            INDEX.set_thumb(entry.rel, result["path"], "user")
        except Exception as exc:  # noqa: BLE001
            LOGGER.debug("[LoraStackPro] 应用封面失败 %s: %s", entry.rel, exc)
            return
        with self._lock:
            self._state["covered"] += 1


QUEUE = PrefetchQueue()
