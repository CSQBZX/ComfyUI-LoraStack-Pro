"""搜索同义词表。

痛点：目录名是中文，LoRA 文件名和训练标签是英文。
搜索 `女仆装` 时，用户真正想找的是 `maid` 的 LoRA。
这里做双向扩展：每个 token 展开成同义词组，组内 OR、组间 AND。
"""

from __future__ import annotations

# 中文 -> 英文（含常见罗马字/变体）
_GROUPS: list[tuple[str, ...]] = [
    # ---- 发色 / 发型
    ("金发", "blonde", "blond", "golden hair"),
    ("银发", "silver hair", "grey hair", "gray hair"),
    ("白发", "white hair"),
    ("黑发", "black hair"),
    ("红发", "red hair"),
    ("蓝发", "blue hair"),
    ("绿发", "green hair"),
    ("粉发", "pink hair", "pink hair"),
    ("紫发", "purple hair"),
    ("棕发", "brown hair"),
    ("双马尾", "twintails", "twin tails"),
    ("单马尾", "ponytail"),
    ("马尾", "ponytail"),
    ("短发", "short hair"),
    ("长发", "long hair"),
    ("卷发", "curly hair", "wavy hair"),
    ("波波头", "bob cut"),
    ("齐刘海", "blunt bangs"),
    ("刘海", "bangs", "fringe"),
    ("呆毛", "ahoge"),
    ("丸子头", "hair bun", "double bun"),
    ("脏辫", "dreadlocks"),
    # ---- 眼睛
    ("蓝眼", "blue eyes"),
    ("红眼", "red eyes"),
    ("绿眼", "green eyes"),
    ("金瞳", "yellow eyes", "golden eyes"),
    ("异色瞳", "heterochromia", "heterochromatic eyes"),
    ("闭眼", "closed eyes"),
    ("半闭眼", "half-closed eyes"),
    ("眼神", "eye focus", "looking"),
    # ---- 身体 / 种族
    ("巨乳", "large breasts", "huge breasts", "big breasts"),
    ("贫乳", "small breasts", "flat chest"),
    ("兽耳", "animal ears", "kemonomimi"),
    ("猫耳", "cat ears", "nekomimi"),
    ("犬耳", "dog ears"),
    ("狐耳", "fox ears", "kitsunemimi"),
    ("兔耳", "rabbit ears", "bunny ears"),
    ("狼耳", "wolf ears"),
    ("精灵耳", "pointy ears", "elf ears"),
    ("尾巴", "tail"),
    ("肌肉", "muscular", "muscle"),
    ("腹肌", "abs", "toned"),
    ("胖", "fat", "chubby", "plump"),
    ("御姐", "mature female"),
    ("大叔", "old man", "middle-aged"),
    ("精灵", "elf"),
    ("恶魔", "demon", "devil"),
    ("天使", "angel"),
    ("吸血鬼", "vampire"),
    ("怪物", "monster", "creature"),
    ("机器人", "robot", "android", "mecha"),
    ("人偶", "doll", "marionette"),
    ("非人", "non-human", "monstergirl"),
    # ---- 服装
    ("校服", "school uniform"),
    ("水手服", "serafuku", "sailor collar", "sailor uniform"),
    ("水着", "swimsuit", "swimwear"),
    ("泳装", "swimsuit", "swimwear"),
    ("泳衣", "swimsuit", "swimwear"),
    ("比基尼", "bikini"),
    ("死库水", "school swimsuit", "one-piece swimsuit"),
    ("女仆装", "maid", "maid outfit"),
    ("婚纱", "wedding dress"),
    ("和服", "kimono", "yukata"),
    ("浴衣", "yukata"),
    ("旗袍", "china dress", "cheongsam", "qipao"),
    ("汉服", "hanfu"),
    ("西装", "suit", "business suit"),
    ("制服", "uniform"),
    ("护士服", "nurse", "nurse uniform"),
    ("兔女郎", "playboy bunny", "bunny girl"),
    ("丝袜", "pantyhose", "stockings"),
    ("过膝袜", "thighhighs", "thigh highs"),
    ("手套", "gloves"),
    ("帽子", "hat", "cap"),
    ("眼镜", "glasses"),
    ("面具", "mask"),
    ("内衣", "underwear", "lingerie"),
    ("胸罩", "bra"),
    ("内裤", "panties", "underwear"),
    ("蕾丝", "lace", "lace trim"),
    ("紧身衣", "bodysuit", "tight clothes"),
    ("连体衣", "leotard"),
    ("外套", "coat", "jacket"),
    ("连衣裙", "dress"),
    ("裙子", "skirt"),
    ("高跟鞋", "high heels", "heels"),
    ("靴子", "boots"),
    ("盔甲", "armor", "armour"),
    ("铠甲", "armor"),
    ("围巾", "scarf"),
    ("项圈", "collar", "choker"),
    ("纹身", "tattoo"),
    ("耳环", "earrings"),
    ("首饰", "jewelry"),
    ("武器", "weapon", "holding weapon"),
    ("剑", "sword", "katana"),
    ("枪", "gun", "rifle", "pistol"),
    # ---- 姿势 / 行为
    ("站立", "standing"),
    ("坐", "sitting"),
    ("躺", "lying", "on back"),
    ("趴", "on stomach"),
    ("跪", "kneeling"),
    ("抬手", "arms up", "arm up"),
    ("比耶", "v sign", "peace sign"),
    ("自拍", "selfie"),
    ("睡觉", "sleeping", "sleep"),
    ("接吻", "kiss", "kissing"),
    ("拥抱", "hug", "embracing"),
    ("战斗", "fighting", "action"),
    ("舞蹈", "dancing", "dance"),
    ("跑步", "running"),
    ("回头", "looking back"),
    ("后视", "from behind"),
    ("侧面", "from side"),
    ("俯视", "from above"),
    ("仰视", "from below"),
    # ---- 构图 / 场景
    ("全身", "full body"),
    ("上半身", "upper body"),
    ("特写", "close-up", "closeup"),
    ("肖像", "portrait"),
    ("半身", "half body", "cowboy shot"),
    ("户外", "outdoors"),
    ("室内", "indoors"),
    ("教室", "classroom"),
    ("床上", "on bed", "bed"),
    ("浴室", "bathroom", "bath"),
    ("夜景", "night", "night sky"),
    ("白天", "day", "daylight"),
    ("雨天", "rain", "raining"),
    ("雪景", "snow"),
    ("海边", "beach", "ocean"),
    ("水下", "underwater"),
    ("城市", "city", "cityscape"),
    ("森林", "forest"),
    ("樱花", "cherry blossoms", "sakura"),
    ("花", "flower", "flowers"),
    # ---- 风格
    ("写实", "realistic", "photorealistic", "photo"),
    ("动漫", "anime", "anime style"),
    ("厚涂", "impasto", "painterly"),
    ("赛璐璐", "cel shading", "cel-shaded"),
    ("水彩", "watercolor"),
    ("素描", "sketch", "pencil"),
    ("线稿", "lineart", "line art"),
    ("像素", "pixel art", "pixel"),
    ("复古", "retro", "vintage"),
    ("暗黑", "dark", "gloomy"),
    ("哥特", "gothic"),
    ("赛博朋克", "cyberpunk"),
    ("奇幻", "fantasy"),
    ("科幻", "sci-fi", "science fiction"),
    ("中国风", "chinese style", "chinese"),
    ("和风", "japanese style", "japanese"),
    ("恐怖", "horror", "scary"),
    ("绘画", "painting", "illustration"),
    ("漫画", "comic", "manga"),
    ("油画", "oil painting"),
    ("水墨", "ink", "sumi-e"),
    ("扁平", "flat color", "flat"),
    ("线条", "line"),
    ("阴影", "shading"),
    # ---- 效果 / 调节
    ("画质", "quality", "detail"),
    ("细节", "detail", "detailed"),
    ("清晰", "sharp", "sharpness"),
    ("对比度", "contrast"),
    ("亮度", "brightness"),
    ("饱和度", "saturation"),
    ("加速", "turbo", "speed", "lightning", "fast"),
    ("提速", "turbo", "lcm", "lightning"),
    ("高清", "highres", "upscale"),
    ("光照", "lighting", "light"),
]


def _build_indexes() -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    zh_index: dict[str, set[str]] = {}
    en_index: dict[str, set[str]] = {}

    for group in _GROUPS:
        zh = group[0]
        en_words = tuple(w.lower() for w in group[1:])
        zh_index.setdefault(zh, set()).update(en_words)

        for word in en_words:
            bucket = en_index.setdefault(word, set())
            bucket.add(zh)
            bucket.add(group[0])

    return (
        {k: tuple(sorted(v)) for k, v in zh_index.items()},
        {k: tuple(sorted(v)) for k, v in en_index.items()},
    )


ZH_TO_EN, EN_TO_ZH = _build_indexes()


def expand(token: str) -> tuple[str, ...]:
    """返回 token 及其同义词，用于组内 OR 匹配。

    中文只做精确匹配（避免「马尾」被「双马尾」误吞），
    英文允许包含匹配（「cat」可以命中「cat ears」）。
    """
    token = token.lower()
    if len(token) < 2:
        return (token,)
    extra: set[str] = set()
    for key, values in ZH_TO_EN.items():
        if key == token:
            extra.update(values)
    for key, values in EN_TO_ZH.items():
        if key in token or token in key:
            extra.update(values)
    if not extra:
        return (token,)
    extra.add(token)
    return tuple(sorted(extra))
