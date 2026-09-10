# 提示词写法（所有生成任务先读这一篇）

目标：每一条提示词都要具体到"画面里能看到什么"，不许用 beautiful/nice/detailed 这类空词。

## 一、图像（SDXL/SD1.5，英文 booru 标签）

### 八段式结构（按顺序写，前面的是重点）
1. **质量词**：masterpiece, best quality, highly detailed（动漫模型再加 absurdres；上限 3-4 个，别堆）
2. **主体**：是什么+数量+核心特征。人物用 1girl/1boy+发色发型（silver hair, twin tails, bangs）；动物 1cat/1fox；物体直接写
3. **外观细节**：眼睛/表情/服装/材质要具体——detailed eyes, soft blush, gentle smile, white sailor dress, blue ribbon, fluffy fur, glossy hair。服装写全件（上衣+下装+鞋袜）
4. **动作姿态**：sitting / standing / walking / looking at viewer / hand on chin / arms crossed / holding 物件 / dancing。一个明确姿态比"pose"强十倍
5. **环境**：具体地点而非"background"——cafe interior, cherry blossom park, rainy street, rooftop at night, classroom。人物场景再加 furniture/decoration
6. **光影**：golden hour / soft rim light / volumetric lighting / sunlight through window / moonlight / neon glow / lens flare / candlelight
7. **氛围**：serene / cozy / mysterious / romantic / dreamy / festive / nostalgic（一个词即可）
8. **构图镜头**：close-up portrait / full body / from above / dutch angle / depth of field / bokeh / looking at viewer / centered
9. **风格收尾**：anime style / Studio Ghibli style / watercolor / cel shading / semi-realistic（与所选模型一致，动漫模型别写 photorealistic）

### 技巧
- **权重语法**：`(词:1.2)` 加强、裸 `(词)`=1.1、`[词]`=0.9、嵌套相乘；`BREAK` 提前开启新的 75-token 分段（防颜色/概念串味）
- **别在正向写否定词**：`no hat`/`without glasses` 无效（CLIP 只做关联不做否定），去掉的东西写负面
- **`[a:b:0.5]` 是 A1111 专属**：ComfyUI 里按时段换提示词要用 ConditioningSetTimestepRange，别把这种语法写进提示词
- **77 token 纪律**：CLIP 有 77 token 截断——主体+外观放在最前面，环境/光影靠后（被截了损失小）。超长内容可拆两个 CLIPTextEncode + ConditioningCombine（每个编码器各有一份 token 预算；主构图放第一个，风格描述放第二个——t2i 模板的 style_prompt 参数就是这条）
- **负面词分类**（用户没给就保留模板默认，别整段重写）：
  质量类 worst quality, low quality, blurry / 解剖类 bad anatomy, extra limbs, missing fingers / 内容类 watermark, signature, text, logo / 风格冲突类（要动漫就禁 photorealistic, 3d render）
- **平庸词黑名单 → 替代**：beautiful→beautiful detailed eyes, delicate features；nice→cozy atmosphere, warm color palette；detailed→detailed fur texture, intricate lace pattern；very→(word:1.3)；good→best quality；pretty→elegant；cute→adorable, round eyes, soft blush
- **中文需求 → 英文标签速查**：可爱=cute/adorable；帅气=handsome, sharp eyes；色气=seductive, alluring；优雅=elegant, graceful；华丽=ornate, luxurious；忧郁=melancholic；温柔=gentle, soft；酷=cool, aloof；仙气=ethereal, fairy-like；性感=sexy, sensual；成熟=mature；清纯=innocent, pure；帅气眼神=sharp eyes, confident expression

### 满分示例
动漫角色：`masterpiece, best quality, absurdres, highly detailed, 1girl, silver hair, long flowing hair, violet eyes, gentle smile, white summer dress with lace trim, straw hat, sitting on wooden bench, flower garden, cherry blossom petals drifting, golden hour, soft rim light, dappled sunlight, serene atmosphere, looking at viewer, close-up portrait, depth of field, bokeh, anime style`
风景：`masterpiece, best quality, highly detailed, mountain lake at dawn, still water reflection, wooden pier, mist over water, pine trees, snow-capped peaks, golden hour, volumetric lighting, mirror reflection, peaceful atmosphere, wide shot, cinematic lighting, 8k wallpaper`
图生图改风格（有参考图时）：主体描述照写，风格词换成目标（Studio Ghibli style, soft colors, hand-drawn feel, detailed backgrounds）

## 二、视频（minimax/ltx：自然语言，不写标签，不用权重括号）

### MiniMax H3 官方结构（官方 VIDEO_PROMPT_WRITING_GUIDE 的浓缩版）
H3 的提示词是一个"分镜脚本"，用字段式写法（一个字符串里写，模型自己解析）：
```
integrated_multimodal_description: [Shot 1] 风格+构图开头（如 3D CG, cinematic,
一个中景镜头框住…）。镜头语言写完整句：动作类型+幅度+速度（"The camera pushes in
with small amplitude at slow speed" 或"镜头缓慢推近"）。动作必须有时序词
（先…然后…接着…）。有台词时用 (S1)/(S2) 标注说话人，台词用引号。
overall_soundscape: 环境音 1-4 句（雨声、脚步声、霓虹灯嗡鸣、衣服窸窣…）。
non_diegetic_music: 配乐 1-3 句，只写乐器/节奏/动态（"舒缓的钢琴和弦，逐渐淡出"），
不写情绪词；没有就写 N/A。
```
- 镜头运动三件套：类型（推/拉/摇/移/俯仰/环绕/跟随/固定/POV）+ 幅度（小幅/大幅）+ 速度（缓慢/快速）
- 换镜头用"镜头切到…"并给时间点（At 00:05.000）；转场用 淡入淡出/快速甩镜/叠化
- 5 秒短片 = 单个 Shot 就够；10 秒以上按 video.md 分段，每段一个 Shot

### 通用四段式（ltx 或不用字段写法时的保底结构）
1. **主体+外观**：一个具体的人/物+"穿着/颜色/表情"
2. **动作（必写动词+时序）**：缓慢转身 / 轻轻点头 / 发丝被风吹动 / 回头望 / 闭眼微笑 / 指尖轻触水面
3. **镜头运动（必写）**：镜头缓慢推近 / 缓缓拉远 / 环绕拍摄 / 平移跟随 / 俯拍 / 固定机位
4. **光影氛围 + 音效（minimax 必写）**：黄昏逆光，暖色氛围；背景有风声与鸟鸣

### 动作/镜头词库（挑 1-2 个写进句子，不要堆）
动作：转身、回头、挥手、眨眼、深呼吸、迈步、奔跑、旋转、漂浮、轻轻点头
镜头：缓慢推近、缓缓拉远、环绕、平移跟随、俯拍、仰拍、固定机位、dolly in、pan left
光影：晨光、黄昏逆光、月光、霓虹、烛光、雾气
音效：风声、雨声、海浪、鸟鸣、脚步声、轻音乐、琴声、人群低语

### 通用公式与技巧（清影/可灵/Hailuo 官方公式合成）
- **公式**：`[镜头语言/景别/光影] + 主体(外貌细节) + 主体运动 + 场景(前景/背景) + 氛围/风格`
- **中英混写**：中文骨架 + 英文技术词（push in / slow motion / time-lapse / cinematic / close-up），
  动作动词拿不准时中英双写（"翻跟头 (somersault)"）
- **5 秒规则**：一个镜头只做一个连续动作；绝不在一段里写多个镜头/切换；运动"5 秒内能展现完"为准
- **防静止/防漂移**：给主体一个带方向的小动作（"缓缓抬头看向镜头"）；给光线钉住
  （"光线保持稳定不变"）；要静止就明确写 `[Static shot] 镜头保持完全不动`
- **音效子句放句尾**："…with crisp glass cutting sounds" / "背景有细雨声"；≤6 秒短片不要写台词
- **视频负面词（短清单即可，5-10 词）**：变形、闪烁、鬼影、重影、跳帧、多余手指、多余肢体、水印、字幕、低画质
- 正约束优于负列举："镜头完全静止" > "镜头不要动"

### 满分示例（H3 字段式）
`integrated_multimodal_description: [Shot 1] Live-action, cinematic, 一个中景镜头框住穿浅蓝色和服的少女站在樱花树下，长发和衣摆在风中轻轻飘动，她缓缓抬起头望向飘落的花瓣；镜头缓慢推近到她的侧脸，背景虚化。overall_soundscape: 微风轻拂，花瓣簌簌飘落，远处有鸟鸣。non_diegetic_music: 舒缓的钢琴单音与弦乐长音，缓慢淡入，逐渐淡出。`
（反面教材：`1girl, cherry blossoms, kimono, beautiful, high quality` —— 标签堆砌没有动作与镜头，视频会变成静帧）

## 三、长度与成本
- 图像提示词 40-80 词；视频提示词 40-80 字
- 视频任务按 video.md 的分段策略拆长片；写"5 秒"时视频模板会自动换算帧数（length 单位是帧）
