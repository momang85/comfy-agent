# 潜空间节点家族速查

```markdown
# ComfyUI 潜空间节点家族速查

## 家族通用接线模式
- **输入**：`LATENT`（核心数据类型，含张量和批次信息）
- **输出**：`LATENT`（保持结构一致，部分节点输出列表）
- **关键连线**：`VAE_ENCODER`→潜操作→`VAE_DECODER`，形成"编码-处理-解码"流水线

## 核心节点与参数
- **VHS_SplitLatents**：拆分批次为独立潜张量，`batch_size`控制拆分粒度
- **VHS_MergeLatents**：合并多个潜列表，需确保形状一致
- **VHS_GetLatentCount**：输出批次数量，用于条件判断
- **VHS_DuplicateLatents**：按`count`复制潜张量，扩展数据量
- **VHS_SelectEveryNthLatent**：步进采样，`step`控制间隔
- **VHS_SelectLatents**：索引选择，支持列表（如`[0,2,4]`）

## 常见坑
1. **形状不匹配**：拆分/合并前检查`batch_size`和通道数
2. **索引越界**：选择节点确保索引不超过实际数量
3. **内存泄漏**：大量拆分后及时释放无用中间变量

## 接口约定
- **上游**：`CLIP_TEXT_ENCODER`（提示词）→`VAE_ENCODER`（生成初始LATENT）
- **下游**：潜操作→`VAE_DECODER`→`IMAGE_OUT`（输出图像）
- **特殊约定**：与`VAE`家族交互时，确保输入为`BCHW`格式张量
```