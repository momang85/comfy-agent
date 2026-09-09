# 采样节点家族速查

```markdown
# ComfyUI 采样家族速查

## 家族通用接线模式
- **输入**：模型、正/负提示词、噪声、图像（可选）  
- **输出**：采样图像、潜在表示（Latent）  
- **核心链**：`模型` → `KSampler` → `VAEDecode` → `图像`

## 关键节点与参数
- **KSampler**：核心采样器  
  - `sampler_name`（采样器，如Euler）、`scheduler`（调度器，如Karras）  
  - `cfg`（提示词相关性，5-15）、`steps`（步数，20-50）  
  - `denoise`（去噪强度，0-1，修复时用0.8）  
- **调度器**：控制噪声衰减节奏  
  - 常用：`Karras`（平滑）、`Exponential`（快速衰减）  

## 常见坑
1. **CFG过高**导致细节僵硬，建议≤10。  
2. **Steps不足**生成模糊，修复时需≥30。  
3. **调度器不匹配**：部分采样器仅支持特定调度器（如DPM++需Karras）。  

## 接口约定
- **上游**：`CLIPTextEncode`（提示词）、`EmptyLatentImage`（初始潜在图）。  
- **下游**：`VAEDecode`（解码潜在图）、`ImageScale`（尺寸调整）。  
- **Mask节点**（如`CropMask`）需插入`KSampler`前，通过` ConditioningSetMask` 应用。  
```