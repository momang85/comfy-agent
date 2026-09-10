# 工作流设计方法论（从零建图）

## 原则
1. **先画骨架，再添环节**：任何图像生成工作流的骨架是
   模型加载 → 提示词编码 → 空潜空间/图像编码 → 采样 → 解码 → 保存
2. **类型匹配是唯一硬约束**：连线前用 inspect_node 查两端类型；不确定的节点用 learn_node 生成档案
3. **每步验证**：propose_edit 批量提交，引擎做类型校验+环检测；被拒看诊断修改重提
4. **小步走**：一次 propose 一个逻辑环节（加载→条件→采样→输出），不要一次塞十步

## 提示词注入位置（关键知识点）
- **正向/负向分开编码**：正负各一个 CLIPTextEncode（clip 都接 checkpoint 的槽1），
  分别接 KSampler 的 positive/negative——不要共用一个编码器节点
- **条件合并**：需要把两段条件拼起来时用 ConditioningCombine/ConditioningConcat
  （如 ControlNet 输出 + 普通条件）；SDXL 有 text_g/text_l 双编码器时用
  CLIPTextEncodeSDXL（若存在）
- **IPAdapter 的文本注入**：IPAdapter 节点只有 image 输入没有 text 输入——
  "用文字描述风格"只能走普通 CLIPTextEncode 接 KSampler，IPAdapter 是图像条件
- **局部重绘的提示词**：VAEEncodeForInpaint 链的 KSampler 提示词只描述遮罩
  区域内要出现的内容，不要重复全图描述
- **FaceDetailer 自带 positive/negative**：接 CLIPTextEncode 输出，与主采样器
  共用同一组条件即可，不需要再造编码器
- 77 token 截断只发生在 CLIPTextEncode 内部——长提示词拆两个编码器各自接
  positive/negative 没有意义；重要内容放提示词前 40 词

## 骨架模式

**文生图骨架**（从空图，节点号自定）：
CheckpointLoaderSimple → CLIPTextEncode×2(正/负) + EmptyLatentImage → KSampler → VAEDecode → SaveImage
接线：ckpt[0]→KSampler.model；ckpt[1]→两个CLIPTextEncode.clip；ckpt[2]→VAEDecode.vae；
enc[0]→KSampler.positive；enc[0]→KSampler.negative；Empty[0]→KSampler.latent_image；KSampler[0]→VAEDecode.samples

**图生图骨架**：文生图骨架把 EmptyLatentImage 换成 LoadImage→VAEEncode，
KSampler.denoise 设 0.4-0.75

**放大环节**：VAEDecode 之后接 ImageScaleBy→（可选二次采样）→SaveImage；
或采样后 LatentUpscaleBy→再 KSampler（hiresfix）

**ControlNet 环节**：Canny(LoadImage)→ControlNetLoader→ControlNetApplyAdvanced
插入在 CLIPTextEncode 与 KSampler 之间（apply 输出正0负1接采样器）

**视频环节**：UNETLoader+CLIPLoader+VAELoader → 空AV潜空间/图生视频节点 →
KSampler → VAEDecode → CreateVideo(fps=24) → SaveVideo

## 工具流程（自由合成会话）
1. synthesize(goal, scaffold=骨架类型) 开会话
2. propose_edit 逐环节 add_node+connect（或直接 insert_lora/insert_controlnet 模式）
3. 不认识的节点：learn_node 生成档案 → 按档案接线
4. 引擎 accept 全部后 submit → wait_result → fetch_outputs → view_image 评估

## 判断标准
- propose_edit 连续被拒 2 次 = 你的理解有问题，learn_node 重新查
- 本机没有的模型文件不要引用（list_models 查）
- 显存紧张时：分辨率小步、steps 压低、batch=1
