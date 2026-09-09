# 修复与重绘节点家族速查

```markdown
# ComfyUI 修复与重绘节点家族速查

## 家族通用接线模式
- **标准链路**：`VAE编码 → [修复/重绘节点] → VAE解码`  
- **核心输入**：`潜在图像(latent)` + `掩码(mask)`（局部重绘必备）  
- **扩展输入**：`重绘幅度/强度`、`采样器`（部分节点支持）  

## 关键节点与参数要点
1. **ReferenceLatentMask**  
   - **用途**：局部重绘/外扩，支持参考图像引导  
   - **关键参数**：`mask`（重绘区域）、`strength`（重绘强度）、`dilation`（掩码外扩像素）  
   - **技巧**：`dilation=0`时仅精确重绘，增大值可修复边缘模糊  

2. **InpaintModel**  
   - **用途**：专用修复模型，需搭配Inpaint模型  
   - **注意**：需提前加载Inpaint Checkpoint（如`sd-v1-5-inpainting.ckpt`）  

## 常见坑
- **掩码边缘锯齿**：使用`dilation`平滑，或结合`GaussianBlur`预处理  
- **重绘区域错位**：确保`mask`尺寸与`latent`分辨率匹配（通常1/8原图尺寸）  
- **上下文断裂**：`strength`过高时降低，或启用`seamless`模式  

## 与相邻家族接口约定
- **上游**：`VAE编码`节点输出`latent`，`ImageMask`节点生成`mask`  
- **下游**：`VAE解码`前检查`latent`维度（需为4维：[B, C, H, W]）  
- **协同**：与`ControlNet`家族配合时，`mask`需与`ControlNet`输入分辨率一致  
```