# 核心节点速查（从零建图的建材清单）

类型匹配是唯一硬约束：连线的输出类型必须等于输入类型（用 inspect_node 查）。

## 模型加载（产出 MODEL/CLIP/VAE）
- **CheckpointLoaderSimple**：SD1.5/SDXL 大模型。输入 ckpt_name；输出 MODEL(槽0)/CLIP(槽1)/VAE(槽2)
- **UNETLoader**：分离式扩散模型（如 MiniMax fl2va）。输入 unet_name+weight_dtype
- **CLIPLoader**：独立文本编码器（qwen3vl 等）。输入 clip_name+type（type 枚举：stable_diffusion/minimax/wan...）
- **VAELoader**：独立 VAE。输入 vae_name
- **LoraLoaderModelOnly**：只改模型的 LoRA。model 接上游 MODEL，输出 MODEL 继续传
- **ControlNetLoader**：control_net_name → CONTROL_NET
- **UpscaleModelLoader**：ESRGAN 类放大模型（本机无文件，暂不用）

## 文本/条件（CONDITIONING 链）
- **CLIPTextEncode**：提示词→条件。clip 接 CLIP，text 填提示词
- **CLIPSetLastLayer**：调 CLIP 层（SDXL 常用 -2）
- **CFGGuider**：把 MODEL+正负条件+cfg 打包成采样器用的 MODEL（视频模型常用）

## 采样（LATENT→LATENT）
- **KSampler**：model/positive/negative/latent_image/seed/steps/cfg/sampler_name/scheduler/denoise
- **KSamplerAdvanced**：加 start_at_step/end_at_step（精细控制）
- denoise<1 = 图生图幅度；scheduler 常用 simple/karras

## 潜空间（LATENT）
- **EmptyLatentImage**：空潜空间（文生图起点）width/height/batch_size
- **EmptySDXLLatentImage**：SDXL 专用空潜空间
- **VAEEncode**：IMAGE→LATENT（pixels+vae）
- **VAEDecode**：LATENT→IMAGE（samples+vae）
- **LatentUpscaleBy**：潜空间放大（samples+upscale_method+scale_by，无需外部模型）

## 图像 IO 与处理（IMAGE 链）
- **LoadImage**：image 填服务器 /input 文件名（上传用 upload_image 工具）；输出 IMAGE(0)+MASK(1)。注意：不接受连线输入！
- **SaveImage**：images+filename_prefix（输出节点，写 output 目录）
- **ImageScaleBy**：按倍数缩放（image+upscale_method+scale_by）
- **ImageScaleToTotalPixels**：按总像素缩放（megapixels+resolution_steps）
- **GetImageSize**：读图尺寸（width/height 转 INT，可接 MathExpression）

## ControlNet 链（CONTROL_NET + CONDITIONING）
- 预处理：**Canny**（边缘，离线安全）；OpenposePreprocessor（人物姿势，需联网下载模型）
- **ControlNetApplyAdvanced**：positive/negative/control_net/image/strength，输出正(0)负(1)双条件——接采样器
- union 模型（controlnet++_union_sdxl_promax）配 canny 输入即可

## 视频（IMAGE→VIDEO）
- **CreateVideo**：images+fps→VIDEO（音频可选）
- **SaveVideo**：video+filename_prefix+format(auto)（输出节点）
- **GetVideoComponents**：视频拆帧/拆音频
- 视频模型链：UNETLoader/CLIPLoader + EmptyMiniMaxH3LatentAV 或 MiniMaxH3ImageToVideo + KSampler + VAEDecode + CreateVideo + SaveVideo

## 常见坑
- CheckpointLoaderSimple 的 VAE 是槽2（不是1）；槽1是 CLIP
- 分辨率须对齐模型家族倍数（SDXL 64、SD1.5 8）
- LoadImage 只能填文件名不能接 IMAGE——管线拼接用入口删除法
- OpenPose/Depth 等预处理器首次运行要联网下载模型
- 显存 11.9GB：SDXL 1024² 约 7GB；视频模板接近满载，先小分辨率
