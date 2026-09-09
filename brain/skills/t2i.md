# 文生图 / 图生图（t2i / i2i）

## 模板选择
- 用户没指定模型 → 默认 `t2i`（novaAnimeXL SDXL）
- 明确要快/要轻量/要SD1.5 → `t2i` 传 `ckpt: "sd1.5\\anything-v5.safetensors"`（此时分辨率默认 512）
- 有输入图片+要改内容/风格 → `i2i`
- 要锁姿势换画风 → `style_transfer`

## 提示词写法（SDXL/SD1.5，CLIP 编码器）
- 英文 booru 标签，逗号分隔，质量词在前：`masterpiece, best quality, ...`
- 主体 → 细节 → 构图/镜头 → 光线/氛围 → 风格
- 77 token 截断：重要内容放前 40 词内
- 动漫模型（novaAnime/anything）用动漫标签体系：1girl/1cat, silver hair, twintails...
- 负面词默认模板已带；用户特别要求"不要XX"时追加到 negative

## 关键参数经验
- SDXL 最佳分辨率簇：1024x1024 / 1152x896 / 896x1152（避免畸变）
- steps 20-28 足够；cfg 5-8（高了过饱和）
- denoise（i2i）：0.4-0.55 微调 / 0.6-0.75 换风格 / >0.8 接近重画
- 多张（batch 2-4）时保持同 seed 只能变参数，不同 seed 才有多样性

## 评估要点（view_image criteria 示例）
"画面应包含：{用户的核心元素}；无肢体畸形；符合{风格}要求"
