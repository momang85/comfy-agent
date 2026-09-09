# 模型加载节点家族速查

```markdown
# ComfyUI 模型加载家族速查

## 通用接线模式
- **输入**：模型路径（字符串）或模型对象（直接传递）
- **输出**：模型实例（Checkpoint/CLIP/VAE等）或处理后的张量（Latent/Conditioning）
- **关键接口**：`model`（模型对象）、`conditioning`（控制条件）、`latent`（潜在表示）

## 核心节点与参数
1. **CheckpointLoaderSimple**  
   - 用途：加载基础模型（含UNET/CLIP/VAE）  
   - 参数：`ckpt_name`（模型文件名）

2. **CLIPTextEncode**  
   - 用途：文本转条件  
   - 参数：`text`（提示词）、`clip`（CLIP模型）

3. **VAELoader**  
   - 用途：加载VAE解码器  
   - 参数：`vae_name`（VAE文件名）

4. **LoRALoader**  
   - 用途：加载LoRA权重  
   - 参数：`lora_name`（LoRA文件）、`strength`（强度）

5. **ControlNetLoader**  
   - 用途：加载ControlNet预处理模型  
   - 参数：`control_net_name`（ControlNet文件）

## 常见坑
- **模型路径错误**：确保文件名与`models`目录结构一致  
- **类型不匹配**：CLIP/VAE需从CheckpointLoader分离后单独传递  
- **强度参数**：LoRA/ControlNet的`strength`过高会导致生成异常  

## 上下游接口约定
- **上游**：文本提示（CLIPTextEncode）→ 条件生成（Conditioning系列）  
- **下游**：模型对象（Checkpoint/VAE）→ 采样器（KSampler）或解码器（VAEDecode）  
- **关键传递**：`latent`（LatentImage）→ KSampler → `latent` → VAEDecode → 图像
```