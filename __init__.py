"""LoRA Stack Pro —— ComfyUI 可视化 LoRA 堆节点。

特性：
  * 逐层浏览 loras 目录（含所有额外模型路径）
  * 跨子文件夹搜索：既搜 LoRA 文件名，也搜文件夹名
  * 每个 LoRA 显示本地预览图（自动从同目录图片中识别）
  * 本地 safetensors 元数据 + Civitai by-hash 信息查询
"""

import logging
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

try:
    from . import lora_index, routes
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
except ImportError:  # 少数加载方式下没有包上下文
    import lora_index  # type: ignore[no-redef]
    import routes  # type: ignore[no-redef]
    from nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS  # type: ignore[no-redef]

WEB_DIRECTORY = "./web"

logging.getLogger("LoraStackPro").setLevel(logging.INFO)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

try:
    from server import PromptServer  # noqa: F401

    routes.register_routes()
except Exception as exc:  # noqa: BLE001
    logging.getLogger("LoraStackPro").warning("[LoraStackPro] 接口注册跳过: %s", exc)

# 后台扫描，不阻塞 ComfyUI 启动
lora_index.start_background_scan()