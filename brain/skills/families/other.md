# 其他节点家族速查

```markdown
# ComfyUI【其他】家族节点速查

## 家族定位
未分类节点的兜底集合，含高级技巧、实验性功能及特殊处理节点。

## 通用接线模式
- **输入**：多类型（Latent/Model/Tensor/ControlNet等），需根据节点功能匹配
- **输出**：单一明确（如Latent/Model/ControlNet等），避免跨类型误接

## 关键节点与参数要点
- **LatentBlend**：潜在空间插值，需确保输入Latent尺寸一致
- **SelfAttentionGuidance**：引导权重控制，过高导致细节丢失
- **PerpNeg**：负向提示增强，需配合CLIP文本编码器
- **SamplerEulerCFGpp**：采样优化，CFG Scale与步数需平衡
- **TorchCompileModel**：模型编译加速，首次运行耗时较长
- **LoraSave**：LoRA权重保存，需指定正确模型路径

## 常见坑
1. **尺寸不匹配**：LatentBlend等节点要求输入尺寸一致
2. **类型混淆**：ModelComputeDtype等节点需明确输入/输出类型
3. **采样器参数**：CFGpp等采样器对步数敏感，需调整测试

## 接口约定
- **上游**：CLIP文本编码器、VAE解码器等需输出明确类型
- **下游**：UNet、采样器等节点需严格匹配输入类型
- **特殊接口**：ControlNet相关节点需与预处理器输出对齐
```