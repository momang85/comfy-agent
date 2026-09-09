# 局部修复配方（修脸/修手/局部崩坏）

## 何时用
用户说"修复某处崩坏"（脸/手/眼睛/局部细节坏）时走本配方。**禁止全图高 denoise i2i 重绘**（构图漂移且修不到部位，已实测失败）。

## 首选方案（立即可行，直接用模板）
`run_template(i2i)` 定向修复，关键参数：
- `image` = 崩坏图（先 upload_image 送 /input，或用已有文件名）
- `denoise` = 0.3~0.4（**必须低于 0.5**：保持构图只重绘细节）
- `prompt` 开头强调修复部位：如 "beautiful face, perfect anatomy, detailed eyes, ..." + 原图主体描述
- `negative` 排除崩坏特征："deformed face, bad anatomy, disfigured, poorly drawn face, mutation, extra limb"
- 评估不达标时**调整参数**（denoise 0.3↔0.4、强化修复词）而不是原样重试

## 高级方案（用户明确要求"精确局部修复"才尝试）
本机 Impact Pack 支持 SAM 检测局部重绘（模型 `sam_vit_b_01ec64.pth` 在位；
注意本机**没有** face_yolov8m 经典脸检模型）。节点链：
SAMLoader → 检测器 → DetailerForEach/FaceDetailer 接在采样输出后。
搭链前必须 learn_node 逐个确认输入；propose_edit 连续被拒 2 次就**降级回首选方案**，不要死磕。

## 纪律
- 相同参数不要重复提交（引擎会拦截）
- 每步都 evaluate；失败两次即降级方案，不要烧执行预算
