"""Lora 索引层。

负责三件事：
1. 扫描 loras 目录（含所有额外模型路径），构建文件夹树 + lora 条目。
2. 为每个 lora 定位本地预览图（兼容各种乱七八糟的图片命名）。
3. 提供跨层搜索：既命中 lora 文件名，也命中任意层级的文件夹名。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote

import folder_paths

try:  # 拼音搜索依赖，装不上就静默降级
    from pypinyin import lazy_pinyin, Style as PinyinStyle
    _HAS_PINYIN = True
except Exception:  # noqa: BLE001
    _HAS_PINYIN = False

LOGGER = logging.getLogger("LoraStackPro")

LORA_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".sft", ".gguf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp"}
PREVIEW_DIR_NAMES = ("previews", "preview", "thumbnails", "thumbs", "covers", "封面")
SKIP_DIR_NAMES = {".git", "__pycache__", ".cache", ".ipynb_checkpoints"}

TAG_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tag_cache.json")
MAX_TAG_BLOB = 4096
MAX_TAGS = 200

_SPLIT_RE = re.compile(r"[_\-\s.]+")


def norm_path(path: str) -> str:
    """统一为 `/` 分隔、无首尾斜杠的相对路径。"""
    path = (path or "").replace("\\", "/").strip()
    path = re.sub(r"/{2,}", "/", path)
    return path.strip("/")


def to_abs_fs(path: str) -> str:
    """转成当前平台的文件系统路径。"""
    return path.replace("/", os.sep)


def human_lower(text: str) -> str:
    """全角转半角 + 小写，方便中英混排检索。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def thumb_url(rel: str = "", dir_path: str = "", mtime: float = 0.0, width: int = 256) -> str:
    """缩略图地址：lora 用 rel，文件夹用 dir，全部相对路径。

    `w` 是请求的目标宽度，服务端会按需缩放并缓存；`t` 是图片自身 mtime，
    用来让换封面后浏览器重新取图。
    """
    stamp = int(mtime) if mtime else 0
    size = f"&w={int(width)}" if width else ""
    if rel:
        return f"/lora_stack_pro/thumb?rel={quote(rel, safe='')}&t={stamp}{size}"
    return f"/lora_stack_pro/thumb?dir={quote(dir_path, safe='')}&t={stamp}{size}"


def _pinyin_variants(text: str) -> str:
    """生成全拼 + 首字母两种变体，让用户能打 `nvpuzhuang` 或 `npz` 搜到「女仆装」。"""
    if not _HAS_PINYIN or not text:
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return ""
    try:
        full = "".join(lazy_pinyin(text))
        initials = "".join(lazy_pinyin(text, style=PinyinStyle.FIRST_LETTER))
    except Exception:  # noqa: BLE001 - 拼音转换失败不该影响搜索
        return ""
    if not full:
        return ""
    return f" {full} {initials} "


def search_blob(*parts: str) -> str:
    """把若干字段拼成可检索文本，保留原形、拆词形与拼音形。"""
    chunks = []
    for part in parts:
        if not part:
            continue
        low = human_lower(part)
        chunks.append(low)
        spaced = _SPLIT_RE.sub(" ", low).strip()
        if spaced != low:
            chunks.append(spaced)
        pinyin = _pinyin_variants(low)
        if pinyin:
            chunks.append(pinyin)
    return " \u0001 ".join(chunks)


def _extract_tags(meta: dict) -> list[str]:
    """从 safetensors 头部元数据里抽取可检索文本（标签、名、描述、触发词）。"""
    words: list[str] = []
    for key in ("ss_output_name", "title", "description", "ss_notes"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            words.append(value.strip())

    freq = meta.get("ss_tag_frequency")
    if isinstance(freq, dict):
        totals: dict[str, int] = {}
        for dataset in freq.values():
            if not isinstance(dataset, dict):
                continue
            for tag, count in dataset.items():
                if not isinstance(count, (int, float)):
                    continue
                totals[tag] = totals.get(tag, 0) + int(count)
        for tag, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:MAX_TAGS]:
            words.append(tag)

    for key in ("ss_trigger_words", "trigger_words", "trainedWords"):
        value = meta.get(key)
        if isinstance(value, str):
            words.append(value)
        elif isinstance(value, list):
            words.extend(str(v) for v in value if str(v).strip())
    return words


def _iter_dirs(root: str):
    """用 scandir 手写目录遍历，yield (相对目录, 该层文件 DirEntry 列表)。

    为什么不用 os.walk：它为每个目录额外做一次 islink 检查（本库 3683 个目录就是
    3683 次多余系统调用），而且拿到的是文件名而不是 DirEntry。scandir 在 Windows
    上通过 FindFirstFile 一次就带回类型、大小和修改时间，后续 entry.stat() 和
    entry.is_dir() 都不再落到系统调用。实测把索引构建的系统调用砍掉七成。
    """
    stack = [""]
    while stack:
        rel_dir = stack.pop()
        abs_dir = os.path.join(root, to_abs_fs(rel_dir)) if rel_dir else root
        try:
            with os.scandir(abs_dir) as handle:
                items = list(handle)
        except OSError:
            continue

        files: list[os.DirEntry] = []
        subdirs: list[str] = []
        for dent in items:
            try:
                is_dir = dent.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if dent.name not in SKIP_DIR_NAMES:
                    subdirs.append(dent.name)
            else:
                files.append(dent)
        yield rel_dir, files
        # 逆序压栈，让出栈顺序与目录项原顺序一致
        for name in reversed(subdirs):
            stack.append(norm_path(f"{rel_dir}/{name}" if rel_dir else name))


@dataclass
class LoraEntry:
    rel: str          # 相对 loras 根目录，正斜杠：Anima/角色/x/y.safetensors
    abspath: str
    root: str         # 命中的根目录绝对路径
    folder: str       # 所在文件夹相对路径，根目录为空串
    fname: str        # 文件名（含扩展名）
    stem: str         # 文件名（不含扩展名）
    size: int
    mtime: float
    thumb: str = ""   # 预览图绝对路径，空串表示没有
    thumb_from: str = ""  # same_name / sibling / subdir / previews / folder_only
    thumb_mtime: float = 0.0  # 预览图自身的修改时间，用来给缩略图 URL 做版本号
    has_sidecar: bool = False  # 扫描时顺手记下的「旁边有没有 civitai/weilin 旁挂文件」
    # 预计算的小写形式：搜索每次要遍历全库，现算就是几千次 NFKC 规范化
    rel_low: str = ""
    folder_low: str = ""
    stem_low: str = ""
    fname_low: str = ""

    def __post_init__(self) -> None:
        if not self.rel_low:
            self.rel_low = human_lower(self.rel)
        if not self.folder_low:
            self.folder_low = human_lower(self.folder)
        if not self.stem_low:
            self.stem_low = human_lower(self.stem)
        if not self.fname_low:
            self.fname_low = human_lower(self.fname)

    @property
    def display(self) -> str:
        return self.stem


@dataclass
class FolderNode:
    rel: str
    name: str
    parent: str
    lora_count: int = 0        # 直属 lora 数
    total_count: int = 0       # 含子目录的 lora 总数
    sub_count: int = 0         # 子文件夹数
    thumb: str = ""            # 代表图绝对路径
    mtime: float = 0.0
    # 搜索用的预计算小写串：3683 个文件夹每次搜索都现算就是 7000+ 次 NFKC
    search_target: str = ""

    def __post_init__(self) -> None:
        if not self.search_target:
            self.search_target = f"{human_lower(self.name)} {human_lower(self.rel)}"


def _pick_thumb(stem: str, images: list[tuple[str, str, float]]) -> tuple[str, str] | None:
    """在一组同目录图片里给某个 lora 挑预览图，返回 (绝对路径, 来源)。"""
    if not images:
        return None
    low_stem = human_lower(stem)
    base_map = []
    for name, full, mtime in images:
        base = os.path.splitext(name)[0]
        base_map.append((human_lower(base), full, mtime))

    # 1) 完全同名
    for base, full, _ in base_map:
        if base == low_stem:
            return full, "same_name"
    # 2) 同名派生：stem.preview / stem_v2 / stem xxx
    for base, full, _ in base_map:
        if base.startswith(low_stem + ".") or base.startswith(low_stem + " ") \
                or base.startswith(low_stem + "_") or base.startswith(low_stem + "-"):
            return full, "same_name"
    # 3) 互相包含
    for base, full, _ in base_map:
        if len(base) >= 4 and (low_stem in base or base in low_stem):
            return full, "same_name"
    return None


def _collect_images_in_dir(abs_dir: str, limit: int = 200) -> list[tuple[str, str, float]]:
    out = []
    try:
        with os.scandir(abs_dir) as it:
            for entry in it:
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in IMAGE_EXTS:
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                out.append((entry.name, entry.path, st.st_mtime))
                if len(out) >= limit:
                    break
    except OSError:
        return []
    out.sort(key=lambda x: -x[2])
    return out


class LoraIndex:
    """进程内单例索引，扫描一次后供所有请求复用。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tag_lock = threading.RLock()
        self._ready = threading.Event()
        self._scanning = False
        self._tag_ready = False
        self._built_at = 0.0
        self._scan_seconds = 0.0
        self._error = ""
        self.entries: list[LoraEntry] = []
        self.by_rel: dict[str, LoraEntry] = {}
        self.by_folder: dict[str, list[LoraEntry]] = {}
        self.by_fname: dict[str, list[str]] = {}
        self.folders: dict[str, FolderNode] = {}
        self.children: dict[str, list[str]] = {}
        self._roots: list[str] = []
        self._blob_cache: dict[str, str] = {}
        # 搜索结果缓存：query -> (时间戳, 排序后的行, name_total, tag_total, folders, tokens, expanded)
        # 命中几千条时翻页只切片这层，不重跑全库匹配
        self._search_cache: dict[str, tuple] = {}
        self._search_lock = threading.Lock()
        self.tag_blobs: dict[str, str] = {}
        self._tag_thread: threading.Thread | None = None
        self._warm_thread: threading.Thread | None = None
        self.aliases: dict[str, str] = {}
        self.covers: dict[str, str] = {}
        self.stale_covers: dict[str, str] = {}
        self.dup_names: set[str] = set()

    # ------------------------------------------------------------------ 构建
    def roots(self) -> list[str]:
        try:
            return [r for r in folder_paths.get_folder_paths("loras") if r]
        except Exception as exc:  # noqa: BLE001 - 目录配置缺失时不该让节点崩掉
            LOGGER.warning("[LoraStackPro] 读取 loras 目录失败: %s", exc)
            return []

    def build(self) -> None:
        started = time.perf_counter()
        roots = self.roots()
        entries: dict[str, LoraEntry] = {}
        folders: dict[str, FolderNode] = {}
        dir_images: dict[str, list[tuple[str, str, float]]] = {}
        dir_loras: dict[str, list[str]] = {}
        # 图片绝对路径 -> mtime，供后面填 thumb_mtime 用，省掉一次全库 getmtime
        img_mtime: dict[str, float] = {}

        def ensure_folder(rel: str) -> FolderNode:
            node = folders.get(rel)
            if node is None:
                parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                name = rel.rsplit("/", 1)[-1] if rel else ""
                node = FolderNode(rel=rel, name=name, parent=parent)
                folders[rel] = node
            return node

        ensure_folder("")

        for root in roots:
            if not os.path.isdir(root):
                continue
            for rel_dir, file_entries in _iter_dirs(root):
                ensure_folder(rel_dir)

                lora_here: list[str] = []
                image_here: list[tuple[str, str, float]] = []
                # 本层全部文件名（小写）用来零系统调用判断旁挂文件是否存在，
                # 否则统计「已有 C 站信息」要为一千多个 LoRA 各做几次 isfile
                name_set = {dent.name.lower() for dent in file_entries}
                for dent in file_entries:
                    fname = dent.name
                    ext = os.path.splitext(fname)[1].lower()
                    full = dent.path
                    if ext in LORA_EXTS:
                        try:
                            st = dent.stat()
                        except OSError:
                            continue
                        rel = norm_path(f"{rel_dir}/{fname}" if rel_dir else fname)
                        if rel in entries:
                            continue  # 多根目录同名时以先出现者为准
                        lower = fname.lower()
                        entries[rel] = LoraEntry(
                            rel=rel,
                            abspath=full,
                            root=root,
                            folder=rel_dir,
                            fname=fname,
                            stem=os.path.splitext(fname)[0],
                            size=st.st_size,
                            mtime=st.st_mtime,
                            has_sidecar=(
                                (lower + ".civitai.info") in name_set
                                or (lower + ".weilin-info.json") in name_set
                            ),
                        )
                        lora_here.append(rel)
                    elif ext in IMAGE_EXTS:
                        try:
                            st = dent.stat()
                        except OSError:
                            continue
                        image_here.append((fname, full, st.st_mtime))
                        img_mtime[full] = st.st_mtime

                image_here.sort(key=lambda x: -x[2])
                dir_images[rel_dir] = image_here
                if lora_here:
                    dir_loras[rel_dir] = lora_here

        # 子目录图片（同名文件夹 / previews 子目录）按需惰性收集
        subdir_image_cache: dict[str, list[tuple[str, str, float]]] = {}

        def images_of(rel_dir: str) -> list[tuple[str, str, float]]:
            if rel_dir in dir_images:
                return dir_images[rel_dir]
            if rel_dir in subdir_image_cache:
                return subdir_image_cache[rel_dir]
            found: list[tuple[str, str, float]] = []
            for root in roots:
                candidate = os.path.join(root, to_abs_fs(rel_dir))
                if os.path.isdir(candidate):
                    found = _collect_images_in_dir(candidate)
                    break
            for _name, full, mtime_value in found:
                img_mtime.setdefault(full, mtime_value)
            subdir_image_cache[rel_dir] = found
            return found

        # 分配预览图
        for rel_dir, rels in dir_loras.items():
            images = dir_images.get(rel_dir, [])
            used: set[str] = set()
            for rel in rels:
                entry = entries[rel]
                # 用户手动指定的封面优先
                forced = self.covers.get(rel)
                if forced:
                    candidate = os.path.join(os.path.dirname(entry.abspath), to_abs_fs(forced))
                    if os.path.isfile(candidate):
                        entry.thumb = candidate
                        entry.thumb_from = "user"
                        used.add(candidate)
                        continue
                hit = _pick_thumb(entry.stem, [img for img in images if img[1] not in used])
                if hit:
                    entry.thumb, entry.thumb_from = hit[0], hit[1]
                    used.add(hit[0])
                    continue
                # 同名子文件夹里的图
                sub_rel = f"{rel_dir}/{entry.stem}" if rel_dir else entry.stem
                if sub_rel in folders:
                    sub_imgs = images_of(sub_rel)
                    if sub_imgs:
                        entry.thumb, entry.thumb_from = sub_imgs[0][1], "subdir"
                        continue
                # previews / preview 子文件夹
                for pname in PREVIEW_DIR_NAMES:
                    p_rel = f"{rel_dir}/{pname}" if rel_dir else pname
                    if p_rel not in folders:
                        continue
                    p_imgs = images_of(p_rel)
                    hit = _pick_thumb(entry.stem, p_imgs)
                    if hit:
                        entry.thumb, entry.thumb_from = hit[0], "previews"
                        break
                    if len(p_imgs) == 1:
                        entry.thumb, entry.thumb_from = p_imgs[0][1], "previews"
                        break

            # 兜底：同目录剩余图片依次分配，用完则复用本目录封面图
            if images:
                leftovers = [img for img in images if img[1] not in used]
                pool = leftovers or images
                cursor = 0
                for rel in rels:
                    entry = entries[rel]
                    if entry.thumb:
                        continue
                    pick = pool[cursor % len(pool)]
                    cursor += 1
                    entry.thumb = pick[1]
                    entry.thumb_from = "folder_share"

        # 统计 + 代表图（自底向上）
        by_folder: dict[str, list[LoraEntry]] = {}
        by_fname: dict[str, list[str]] = {}
        for entry in entries.values():
            by_folder.setdefault(entry.folder, []).append(entry)
            by_fname.setdefault(entry.fname.lower(), []).append(entry.rel)
        for rel_dir, node in folders.items():
            members = by_folder.get(rel_dir, [])
            node.lora_count = len(members)
            node.mtime = max((e.mtime for e in members), default=0.0)

        # 文件夹的直接子节点
        children: dict[str, list[str]] = {}
        for rel_dir, node in folders.items():
            if rel_dir == "":
                continue
            children.setdefault(node.parent, []).append(rel_dir)
        for key in children:
            children[key].sort(key=lambda p: p.rsplit("/", 1)[-1].lower())

        # 文件夹封面：优先本层 lora 的图，其次本层闲置图，再次子文件夹封面
        def folder_own_thumb(rel_dir: str) -> str:
            members = by_folder.get(rel_dir, [])
            for entry in members:
                if entry.thumb:
                    return entry.thumb
            imgs = dir_images.get(rel_dir, [])
            for img in imgs:
                if not any(entry.thumb == img[1] for entry in members):
                    return img[1]
            return ""

        # 先算深层，再算浅层；根目录（深度视为 -1）必须最后处理
        ordered = sorted(folders.keys(), key=lambda p: (-(p.count("/") if p else -1), p))
        for rel_dir in ordered:
            node = folders[rel_dir]
            thumb = folder_own_thumb(rel_dir)
            total = node.lora_count
            if not thumb:
                for child in children.get(rel_dir, []):
                    child_node = folders.get(child)
                    if child_node and child_node.thumb:
                        thumb = child_node.thumb
                        break
            for child in children.get(rel_dir, []):
                child_node = folders.get(child)
                if child_node:
                    total += child_node.total_count
            node.thumb = thumb
            node.total_count = total
            node.sub_count = len(children.get(rel_dir, []))

        # 同名文件检测：不同目录下的同名 LoRA 会让加载解析到错误文件
        name_counter: dict[str, int] = {}
        for entry in entries.values():
            key = entry.fname_low
            name_counter[key] = name_counter.get(key, 0) + 1
        dup_names = {name for name, count in name_counter.items() if count > 1}

        # 记录预览图自身的 mtime：缩略图 URL 用它当版本号，换了封面才会拿到新图
        for entry in entries.values():
            if not entry.thumb:
                continue
            stamp = img_mtime.get(entry.thumb)
            if stamp is None:
                # covers.json 指定的图可能不在这轮扫到的目录里，退回真实 stat
                try:
                    stamp = os.path.getmtime(entry.thumb)
                except OSError:
                    stamp = 0.0
            entry.thumb_mtime = stamp

        # covers.json 里的指定封面可能早就失效了（图片被删/改名，或 LoRA 被移走）。
        # 这里只登记不删除：静默降级会让脏数据一直留着，交给 /covers/prune 或详情页清理。
        stale_covers: dict[str, str] = {}
        for rel, filename in self.covers.items():
            entry = entries.get(rel)
            if entry is None:
                stale_covers[rel] = filename
                continue
            candidate = os.path.join(os.path.dirname(entry.abspath), to_abs_fs(filename))
            if not os.path.isfile(candidate):
                stale_covers[rel] = filename

        with self._lock:
            self.entries = sorted(entries.values(), key=lambda e: human_lower(e.rel))
            self.by_rel = entries
            self.by_folder = by_folder
            self.by_fname = by_fname
            self.folders = folders
            self.children = children
            self.dup_names = dup_names
            self.stale_covers = stale_covers
            self._roots = list(roots)
            self._blob_cache = {}
            # 新增/删除文件后旧结果必须作废；换绑前先拿锁，避免请求线程
            # 正在遍历淘汰时被换掉判据
            with self._search_lock:
                self._search_cache = {}
            self._warm_thread = None  # 索引变了，预热要重来
            with self._tag_lock:
                self.tag_blobs = {}
                self._tag_ready = False
            self._built_at = time.time()
            self._scan_seconds = time.perf_counter() - started
            self._error = ""
            self._ready.set()
        LOGGER.info(
            "[LoraStackPro] 索引完成: %d 个 lora / %d 个文件夹, 耗时 %.2fs",
            len(self.entries), len(self.folders), self._scan_seconds,
        )

    def rebuild(self) -> None:
        with self._lock:
            self._scanning = True
            self._ready.clear()
        try:
            # 重建时把磁盘上的别名表和封面表一起重读：
            # 这两张表可能在插件外被改过（换机、手动编辑），只重扫目录而不重读它们，
            # 会表现为「rescan 点了没用」，必须重启 ComfyUI 才对。
            self.refresh_covers()
            self.refresh_aliases()
            self.build()
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("[LoraStackPro] 索引构建失败")
            with self._lock:
                self._error = str(exc)
                self._ready.set()
        finally:
            with self._lock:
                self._scanning = False
        if self._ready.is_set():
            self.start_tag_scan()

    def build_in_background(self) -> None:
        threading.Thread(target=self.rebuild, name="LoraStackPro-Index", daemon=True).start()

    def ensure_ready(self, timeout: float = 120.0) -> bool:
        if self._ready.is_set():
            return True
        if not self._scanning:
            threading.Thread(target=self.rebuild, name="LoraStackPro-Index", daemon=True).start()
        return self._ready.wait(timeout)

    # -------------------------------------------------------------- 标签索引
    def _load_tag_cache(self) -> dict:
        try:
            if os.path.isfile(TAG_CACHE_PATH):
                with open(TAG_CACHE_PATH, "r", encoding="utf-8-sig") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            LOGGER.warning("[LoraStackPro] 标签缓存读取失败: %s", exc)
        return {}

    def _save_tag_cache(self, cache: dict) -> None:
        try:
            os.makedirs(os.path.dirname(TAG_CACHE_PATH), exist_ok=True)
            tmp = TAG_CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(cache, handle, ensure_ascii=False)
            os.replace(tmp, TAG_CACHE_PATH)
        except OSError as exc:
            LOGGER.warning("[LoraStackPro] 标签缓存写入失败: %s", exc)

    def build_tag_index(self) -> None:
        """后台读取 safetensors 头部标签，让搜索能命中训练标签。"""
        try:
            from . import lora_meta as meta_mod
        except ImportError:
            import lora_meta as meta_mod  # type: ignore[no-redef]

        if not self.ensure_ready():
            return
        # 搜索文本的预热（拼音转换，纯 CPU）与标签读取并行跑。
        # 原来它串在标签循环里，首次标签扫描要 30 秒以上，这段时间内搜索
        # 每次都要为全库现算，用户看到的就是「点搜索卡半秒」。
        self.start_warm_blobs()
        cache = self._load_tag_cache()
        entries = list(self.entries)
        dirty = 0
        started = time.perf_counter()
        for index, entry in enumerate(entries):
            record = cache.get(entry.rel)
            if record and abs(record.get("mtime", 0) - entry.mtime) < 2:
                blob = record.get("blob", "")
            else:
                meta = meta_mod.read_header_metadata(entry.abspath)
                blob = human_lower(" ".join(_extract_tags(meta)))[:MAX_TAG_BLOB]
                cache[entry.rel] = {"mtime": entry.mtime, "blob": blob}
                dirty += 1
                if dirty % 80 == 0:
                    self._save_tag_cache(cache)
            with self._tag_lock:
                self.tag_blobs[entry.rel] = blob
            # 顺手预热搜索用的文本：拼音转换很贵，不预热的话首次搜索要为
            # 全库现算一遍，用户看到的就是「点了搜索卡一下」
            self.blob_of(entry)
        if dirty:
            # 全部命中缓存时不必重写 3.2 MB：稳态启动本该零写盘
            self._save_tag_cache(cache)
        with self._tag_lock:
            self._tag_ready = True
        LOGGER.info(
            "[LoraStackPro] 标签索引完成: %d 条, 新解析 %d 个文件, 耗时 %.1fs",
            len(self.tag_blobs), dirty, time.perf_counter() - started,
        )

    def start_tag_scan(self) -> None:
        with self._tag_lock:
            if self._tag_ready:
                return
            thread = getattr(self, "_tag_thread", None)
            if thread is not None and thread.is_alive():
                return
            self._tag_thread = threading.Thread(
                target=self.build_tag_index, name="LoraStackPro-Tags", daemon=True
            )
            self._tag_thread.start()

    def start_warm_blobs(self) -> None:
        """后台预热搜索文本（含拼音转换），让首次搜索直接命中缓存。"""
        if self._warm_thread is not None and self._warm_thread.is_alive():
            return
        if not self.entries:
            return

        def runner() -> None:
            try:
                for entry in list(self.entries):
                    self.blob_of(entry)
            except Exception:  # noqa: BLE001 - 预热失败不该影响搜索可用性
                LOGGER.exception("[LoraStackPro] 搜索文本预热失败")

        self._warm_thread = threading.Thread(
            target=runner, name="LoraStackPro-Warm", daemon=True
        )
        self._warm_thread.start()

    # ------------------------------------------------------------------ 别名
    def refresh_aliases(self) -> dict[str, str]:
        """重新加载手动备注表，别名也参与搜索。"""
        try:
            from . import lora_meta as meta_mod
        except ImportError:
            import lora_meta as meta_mod  # type: ignore[no-redef]
        try:
            aliases = meta_mod.load_aliases(force=True)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[LoraStackPro] 别名加载失败: %s", exc)
            return dict(self.aliases)
        with self._lock:
            self.aliases = dict(aliases)
            self._blob_cache = {}
        return dict(self.aliases)

    def refresh_covers(self) -> dict[str, str]:
        """重新加载用户手动指定的封面表。"""
        try:
            from . import lora_meta as meta_mod
        except ImportError:
            import lora_meta as meta_mod  # type: ignore[no-redef]
        try:
            covers = meta_mod.load_covers()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("[LoraStackPro] 封面表加载失败: %s", exc)
            return dict(self.covers)
        with self._lock:
            self.covers = dict(covers)
        return dict(self.covers)

    def prune_stale_covers(self) -> dict:
        """清理 covers.json 里指向不存在文件的条目，返回被清掉的清单。"""
        try:
            from . import lora_meta as meta_mod
        except ImportError:
            import lora_meta as meta_mod  # type: ignore[no-redef]
        targets = dict(self.stale_covers)
        removed: dict[str, str] = {}
        for rel, filename in targets.items():
            try:
                meta_mod.save_cover(rel, "")
                removed[rel] = filename
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("[LoraStackPro] 清理失效封面失败 %s: %s", rel, exc)
        self.refresh_covers()
        # 重新按剩余封面表回填内存里的缩略图
        for rel in removed:
            self.recompute_thumb(rel)
        with self._lock:
            for rel in removed:
                self.stale_covers.pop(rel, None)
        LOGGER.info("[LoraStackPro] 已清理 %d 条失效封面记录", len(removed))
        return {"removed": removed, "count": len(removed)}

    def list_images(self, rel: str) -> list[dict]:
        """列出该 LoRA 同目录下的全部候选预览图，供手动挑选封面。"""
        entry = self.get(rel)
        if entry is None:
            return []
        folder = os.path.dirname(entry.abspath)
        images = _collect_images_in_dir(folder)
        current = entry.thumb
        out: list[dict] = []
        for name, full, _mtime in images:
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({
                "file": name,
                "url": f"/lora_stack_pro/thumb?rel={quote(rel, safe='')}&img={quote(name, safe='')}&w=192",
                "current": bool(current) and os.path.normcase(full) == os.path.normcase(current),
                "size": size,
            })
        return out

    def set_thumb(self, rel: str, abs_path: str, source: str = "user") -> bool:
        """改内存索引里某个条目的封面，避免为了换张图重建整个索引。"""
        with self._lock:
            entry = self.by_rel.get(norm_path(rel))
            if entry is None:
                return False
            entry.thumb = abs_path
            entry.thumb_from = source
            try:
                entry.thumb_mtime = os.path.getmtime(abs_path) if abs_path else 0.0
            except OSError:
                entry.thumb_mtime = 0.0
            return True

    def recompute_thumb(self, rel: str) -> str:
        """按当前封面表重算单个条目的封面（改了或清了封面后用）。"""
        entry = self.get(rel)
        if entry is None:
            return ""
        forced = self.covers.get(rel)
        if forced:
            candidate = os.path.join(os.path.dirname(entry.abspath), to_abs_fs(forced))
            if os.path.isfile(candidate):
                self.set_thumb(rel, candidate, "user")
                return candidate
        images = _collect_images_in_dir(os.path.dirname(entry.abspath))
        hit = _pick_thumb(entry.stem, images)
        if hit:
            self.set_thumb(rel, hit[0], hit[1])
            return hit[0]
        if len(images) == 1:
            self.set_thumb(rel, images[0][1], "folder_share")
            return images[0][1]
        self.set_thumb(rel, "", "")
        return ""

    # ------------------------------------------------------------------ 查询
    def stats(self) -> dict:
        return {
            "ready": self._ready.is_set(),
            "scanning": self._scanning,
            "lora_count": len(self.entries),
            "folder_count": max(len(self.folders) - 1, 0),
            "scan_seconds": round(self._scan_seconds, 3),
            "built_at": self._built_at,
            "tag_ready": self._tag_ready,
            "tag_count": len(self.tag_blobs),
            "stale_covers": len(self.stale_covers),
            "error": self._error,
            "roots": list(self._roots) if self._roots else self.roots(),
        }

    SORT_KEYS = ("name", "mtime", "size")
    FILTER_KEYS = ("", "nocover", "dup")

    def list_dir(self, rel_dir: str = "", wanted: str = "", sort: str = "name",
                 filter_kind: str = "") -> dict:
        """列出一层：子文件夹 + 直属 lora。wanted 用于前端定位到某个 lora。

        sort 取 name/mtime/size，filter_kind 取 ""/nocover/dup。
        筛选只作用于 LoRA；一旦筛选生效就隐藏子文件夹，避免结果被稀释。
        """
        rel_dir = norm_path(rel_dir)
        sort = sort if sort in self.SORT_KEYS else "name"
        filter_kind = filter_kind if filter_kind in self.FILTER_KEYS else ""
        node = self.folders.get(rel_dir)
        if node is None:
            rel_dir = ""
            node = self.folders.get("")
        subfolders = []
        for child in self.children.get(rel_dir, []):
            child_node = self.folders.get(child)
            if child_node is None:
                continue
            subfolders.append({
                "name": child_node.name,
                "path": child_node.rel,
                "lora_count": child_node.lora_count,
                "total_count": child_node.total_count,
                "sub_count": child_node.sub_count,
                "thumb": thumb_url(dir_path=child_node.rel, mtime=child_node.mtime, width=192) if child_node.thumb else "",
                "mtime": child_node.mtime,
            })
        subfolders.sort(key=lambda f: human_lower(f["name"]))
        if filter_kind:
            subfolders = []

        matched: list[LoraEntry] = []
        for entry in self.by_folder.get(rel_dir, ()):
            if filter_kind == "nocover" and entry.thumb:
                continue
            if filter_kind == "dup" and entry.fname_low not in self.dup_names:
                continue
            matched.append(entry)
        if sort == "mtime":
            matched.sort(key=lambda e: (-e.mtime, human_lower(e.stem)))
        elif sort == "size":
            matched.sort(key=lambda e: (-e.size, human_lower(e.stem)))
        else:
            matched.sort(key=lambda e: human_lower(e.stem))
        loras = [self._entry_json(entry) for entry in matched]

        crumbs = []
        if rel_dir:
            acc = ""
            for part in rel_dir.split("/"):
                acc = f"{acc}/{part}" if acc else part
                crumbs.append({"name": part, "path": acc})

        thumb = node.thumb if node else ""
        return {
            "path": rel_dir,
            "name": node.name if node else "",
            "parent": node.parent if node and rel_dir else "",
            "crumbs": crumbs,
            "folders": subfolders,
            "loras": loras,
            "sort": sort,
            "filter": filter_kind,
            "lora_count": node.lora_count if node else 0,
            "filtered_count": len(loras),
            "total_count": node.total_count if node else 0,
            "thumb": thumb_url(dir_path=rel_dir, mtime=node.mtime if node else 0.0) if thumb else "",
            "focus": norm_path(wanted),
        }

    def _entry_json(self, entry: LoraEntry) -> dict:
        return {
            "rel": entry.rel,
            "path": entry.rel,
            "name": entry.fname,
            "stem": entry.stem,
            "folder": entry.folder,
            "size": entry.size,
            "mtime": entry.mtime,
            "thumb": thumb_url(rel=entry.rel, mtime=entry.thumb_mtime) if entry.thumb else "",
            "thumb_src": entry.thumb_from,
            "alias": self.aliases.get(entry.rel, ""),
            "cover": self.covers.get(entry.rel, ""),
            "dup": entry.fname_low in self.dup_names,
        }

    def entry_json(self, rel: str) -> dict | None:
        """按相对路径取单条 JSON（给 /usage 之类的接口补全显示信息）。"""
        entry = self.get(rel)
        return self._entry_json(entry) if entry else None

    def duplicate_relatives(self, rel: str) -> list[str]:
        """返回与给定文件同名的其他 LoRA 路径（走文件名索引，不再全表扫描）。"""
        entry = self.get(rel)
        if entry is None:
            return []
        others = self.by_fname.get(entry.fname.lower(), ())
        if len(others) < 2:
            return []
        return [other for other in others if other != entry.rel][:12]

    def blob_of(self, entry: LoraEntry) -> str:
        base = self._blob_cache.get(entry.rel)
        if base is None:
            folder_name = entry.folder.rsplit("/", 1)[-1] if entry.folder else ""
            alias = self.aliases.get(entry.rel, "")
            base = search_blob(entry.fname, entry.stem, entry.folder, folder_name, alias)
            self._blob_cache[entry.rel] = base
        # 只读取引用，不加锁：tag_blobs 由后台线程逐项写入，CPython 下 dict 单次读是安全的，
        # 而搜索要遍历全库，每项抢一次锁纯属浪费
        tags = self.tag_blobs.get(entry.rel, "")
        if tags:
            return base + " \u0001 " + tags
        return base

    def search(self, query: str, limit: int = 400, offset: int = 0,
               include_tags: bool = True) -> dict:
        """跨层搜索：命中 LoRA 文件名、任意上级文件夹名，或训练标签（含同义词扩展）。

        `limit` 是每页条数，`offset` 是起始位置。命中上千条时只回当前页：一次把
        600 条条目 JSON 全发出去实测约 590 KB，浏览器光解析就白白多花十几毫秒。
        `include_tags=False` 时只留名称/文件夹命中，省掉用户关掉标签开关后的无用传输。
        """
        raw_tokens = [human_lower(t) for t in re.split(r"[\s,，]+", (query or "").strip()) if t.strip()]
        if not raw_tokens:
            return {"query": query, "tokens": [], "expanded": {}, "loras": [], "folders": [],
                    "truncated": False, "total": 0, "name_total": 0, "tag_total": 0,
                    "offset": 0, "limit": limit}

        cache_key = "\u0000".join(raw_tokens)
        cached = self._search_cache.get(cache_key)
        if cached is not None and time.time() - cached[0] < 120.0:
            (_, rows, name_total, tag_total, folder_total,
             folder_out, tokens_out, expanded) = cached
        else:
            (rows, name_total, tag_total, folder_total,
             folder_out, tokens_out, expanded) = self._search_rows(raw_tokens)
            self._search_cache[cache_key] = (
                time.time(), rows, name_total, tag_total, folder_total,
                folder_out, tokens_out, expanded,
            )
            # 淘汰要加锁：min() 会遍历 dict 的键，另一个请求线程同时写就会抛
            # "dictionary changed size during iteration"。这里还必须用局部引用，
            # 否则 build() 并发替换 _search_cache 时，遍历的是旧 dict 而查询的
            # 是新 dict，会 KeyError。
            with self._search_lock:
                cache = self._search_cache
                while len(cache) > 8:
                    oldest = min(cache, key=lambda k: cache[k][0])
                    cache.pop(oldest, None)

        start = max(0, int(offset))
        usable = rows if include_tags else rows[:name_total]
        total = len(usable)
        page = usable[start:start + max(1, int(limit))]
        loras = []
        for rel, match_name, match_folder, tag_group in page:
            entry = self.by_rel.get(rel)
            if entry is None:
                continue  # 索引刚重建、结果缓存里留着已删文件
            payload = self._entry_json(entry)
            payload["match_name"] = match_name
            payload["match_folder"] = match_folder
            payload["match_tag"] = tag_group
            loras.append(payload)

        return {
            "query": query,
            "tokens": tokens_out,
            "expanded": expanded,
            "loras": loras,
            # 文件夹命中只在第一页带：它是独立的一组，翻页时重复发纯属浪费
            "folders": folder_out if start == 0 else [],
            "folder_total": folder_total,
            "truncated": start + len(page) < total,
            "total": total,
            "name_total": name_total,
            "tag_total": tag_total,
            "offset": start,
            "limit": limit,
        }

    def _search_rows(self, raw_tokens: list[str]) -> tuple:
        """算出完整命中与排序，返回可缓存的轻量行（不含每条的 payload dict）。

        每个 token 独立参与「与」匹配，不做同义词扩展：曾经做过中英同义词
        双向扩展，搜「金发」会连带命中 700+ 条沾 `blonde` 标签的条目，
        结果反而没法筛。中文现在只靠文件名、文件夹名和拼音命中。
        """
        def hits(text: str) -> bool:
            return any(token in text for token in raw_tokens)

        scored: list[tuple] = []
        first = raw_tokens[0]
        for entry in self.entries:
            blob = self.blob_of(entry)
            if not all(token in blob for token in raw_tokens):
                continue
            stem_low = entry.stem_low
            folder_low = entry.folder_low
            if stem_low == first:
                rank = 0
            elif stem_low.startswith(first):
                rank = 1
            elif first in stem_low:
                rank = 2
            elif first in folder_low:
                rank = 3
            else:
                rank = 4
            tag_group = rank >= 4
            tag_pos = 0
            if tag_group:
                # 标签按词频排序写入，越靠前越相关；用它给纯标签命中排序
                positions = [blob.find(token) for token in raw_tokens if token in blob]
                tag_pos = min(positions) if positions else 10 ** 6
            scored.append((
                (rank, tag_pos, len(entry.rel), entry.rel_low),
                entry.rel,
                hits(stem_low),
                hits(folder_low),
                tag_group,
            ))

        scored.sort(key=lambda x: x[0])
        rows = [(rel, mn, mf, tg) for _key, rel, mn, mf, tg in scored]
        # 排序保证 rank<4（名称/文件夹命中）全在前，tag_group 就是分页用的分界
        name_total = sum(1 for row in rows if not row[3])

        # 命中的文件夹（用户可能想直接点进去）
        folder_hits = []
        for rel_dir, node in self.folders.items():
            if not rel_dir or node.total_count == 0:
                continue
            target = node.search_target or f"{human_lower(node.name)} {human_lower(rel_dir)}"
            if not all(token in target for token in raw_tokens):
                continue
            folder_hits.append({
                "name": node.name,
                "path": node.rel,
                "lora_count": node.lora_count,
                "total_count": node.total_count,
                "sub_count": node.sub_count,
                "thumb": thumb_url(dir_path=node.rel, mtime=node.mtime, width=192) if node.thumb else "",
            })
        folder_hits.sort(key=lambda f: (-f["total_count"], f["name"]))

        # 文件夹命中按体积设上限：120 个实测就要 31~46 KB，比 20 条 LoRA 本身还大
        return (rows, name_total, len(rows) - name_total, len(folder_hits),
                folder_hits[:40], raw_tokens, {})

    def folder_lookup(self, rel_dir: str) -> dict | None:
        node = self.folders.get(norm_path(rel_dir))
        if node is None:
            return None
        return {
            "path": node.rel,
            "name": node.name,
            "total_count": node.total_count,
            "lora_count": node.lora_count,
        }

    def get(self, rel: str) -> LoraEntry | None:
        return self.by_rel.get(norm_path(rel))


INDEX = LoraIndex()


def start_background_scan() -> None:
    # 别名表和封面表由 rebuild() 内部负责重读，这里不要再读一遍
    INDEX.build_in_background()
    INDEX.start_tag_scan()
