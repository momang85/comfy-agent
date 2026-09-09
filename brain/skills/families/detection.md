# 检测与姿态节点家族速查

```markdown
# ComfyUI 检测与姿态节点家族速查

## 家族通用接线模式
- **输入**：图像（Image）+ 可选检测条件（如人脸ID、姿态提示）
- **输出**：检测特征（DetectionFeature）+ 原图（Image）
- **核心链**：原图 → 检测节点 → 特征注入 → 生成节点

## 关键节点与参数要点
1. **IPAdapterFaceID**  
   - 用途：基于人脸ID的特征注入  
   - 参数：`face_id`（人脸ID索引）、`weight`（权重，建议0.5-1.0）  
2. **IPAdapterFaceIDKolors**  
   - 用途：保留色彩的人脸ID适配  
   - 参数：`color_strength`（色彩强度，默认1.0）  
3. **IPAAdapterFaceIDBatch**  
   - 用途：批量处理多图人脸ID  
   - 参数：`batch_size`（批次大小，避免显存溢出）  
4. **IPAdapterUnifiedLoaderFaceID**  
   - 用途：统一加载人脸ID模型  
   - 参数：`model_path`（模型路径，需提前下载）  

## 常见坑
- **显存不足**：批量处理时降低`batch_size`或分辨率。  
- **ID失效**：确保输入图像含清晰人脸，否则特征注入失败。  
- **权重失衡**：`weight`过高导致人脸过度拟合，建议逐步调试。  

## 与相邻家族接口约定
- **上游**：`CLIPTextEncode`提供文本提示，需与检测节点并行输入生成节点。  
- **下游**：`KSampler`等生成节点需同时接收原图与检测特征，确保` conditioning_type`匹配。  
```