# 局部修复配方（修脸/修手/局部崩坏）

## 何时用
用户说"修复某处崩坏"（脸/手/眼睛/局部细节坏）时走本配方。**禁止全图高 denoise i2i 重绘**
（构图漂移且修不到部位，已实测失败：同一张图连着重绘三次，分数 6→4→6→6）。

## 首选方案：`run_template(local_repair)` —— 自动生成遮罩，只重绘问题区
```
run_template(template_id="local_repair", params={
  "image": 崩坏图,            # 上一版产物路径，或本轮上传的 server_name
  "target": "hand" | "face" | "box" | "provided" | "auto",
  "prompt": "只描述问题区该长什么样",   # 如 perfect hands, five fingers, natural anatomy
  "denoise": 0.45,           # 0.4-0.6；过高会破坏周边
  "grow_mask_by": 16
})
```
- `target=hand`：本机 RMBG 的手部 YOLO（`hand_yolov8s.pt` 在 `models/ultralytics/`）自动出遮罩
- `target=face`：脸部用**动漫专用分割权重** `anime_face_seg_v3_y11n.pt`（Anzhc Face seg 640 v3，
  单类 face，插画掩码 mAP50 0.871）——实测在全身图上能找到 0.69% 的小脸紧框；
  兜底 `face_yolov8n-seg2_60.pt`（区域偏松，含头发）。检测阈值 conf=0.10（0.25 检测不到动漫脸）
- `target=box`：自己给比例框 `"x,y,h,w"`（0-1，相对图像宽高）
- `target=provided`：用户上传黑白遮罩（白色=重绘区域），传 `mask` 参数
- **denoise 必须 ≥0.85**：普通 SDXL 不是 inpaint 模型，`VAEEncodeForInpaint` 用灰填充遮罩区，
  实测 0.65 留平灰块、0.80 留深灰块、0.85 正常；引擎会自动收敛（你传更低也会被抬到 0.85）
- 引擎会把**遮罩外的像素逐像素保留原图**（ImageCompositeMasked 贴回），所以局部就是局部
- 遮罩无效时引擎会拦下并给出原因：遮罩几乎为空 = **没检测到目标**（脸太小/手不在画面里），
  此时**不要声称已修复**，如实告知并请用户上传黑白遮罩；遮罩接近整图 = 不是局部修复，别这么用
- 历史：脸部曾走 DWPose 关键点（DWPreprocessor → FaceMaskFromPoseKeypoints），实测在两张真实
  动漫产物上掩码覆盖率都是 **0.000%**（照片训练的检测器），已弃用；不要再回退到那条链

## 备选方案（local_repair 不可用，或用户要"整图微调"时）
`run_template(i2i)` 低 denoise 定向修复：`denoise` 0.3~0.4（**必须低于 0.5**），
prompt 开头强调修复部位，negative 排除崩坏特征；评估不达标就**改参数**而不是原样重试。

## 高级方案（用户明确要求精确局部修复且愿意搭图才试）
Impact 的 SAM 链（`sam_vit_b_01ec64.pth` 在位）：`MaskToSEGS → SAMDetectorCombined → SEGSDetailer`。
注意本机**没有** `UltralyticsDetectorProvider`（Impact-Subpack 未装），所以
`BboxDetectorSEGS` / `FaceDetailer` 这类需要 BBOX_DETECTOR 的节点**用不了**，别在这上面浪费轮次。
搭链前必须 learn_node 逐个确认输入；propose_edit 连续被拒 2 次就降级回首选方案。

## 纪律
- 相同参数不要重复提交（引擎会拦截；同基底第 2 次同手法会被拦下并**自动改走局部修复**）
- 每步都 evaluate；失败两次即降级方案，不要烧执行预算
