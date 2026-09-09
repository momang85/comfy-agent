# 图像IO节点家族速查

```markdown
# ComfyUI 图像IO家族速查

## 通用接线模式
- **输入**：图像（Image）、掩码（Mask）、可选模型（Model）
- **输出**：图像（Image）、预览（Preview）、文件路径（String）
- **关键**：LoadImage系列需指定路径，SaveImage需连接预览或图像

## 核心节点要点
| 节点                | 用途                          | 关键参数                          |
|---------------------|-------------------------------|-----------------------------------|
| LoadImage           | 加载图片                      | `image_path`（支持通配符）        |
| SaveImage           | 保存图片                      | `prefix`（文件名前缀）            |
| PreviewImage        | 实时预览                      | 无（直接连接图像输出）            |
| ImageScale/By       | 尺寸缩放                      | `width/height`或`scale_factor`    |
| ImageBlend          | 图像混合                      | `blend_factor`（0-1）             |
| ImageUpscaleWithModel| 模型放大                      | `model`（需连接放大模型）         |

## 常见坑
1. LoadImage路径需用`/`分隔，不支持反斜杠
2. ImageScaleBy缩放因子建议≤4，否则失真
3. SaveImage未连接预览时可能不触发保存
4. LoadImageMask需确保图片为灰度且尺寸匹配原图

## 接口约定
- **上游**：CLIPTextEncode/VAEDecode → 图像IO → KSampler/SavedImage
- **关键**：VAEDecode输出需经ImageScale调整尺寸后再保存
- **注意**：Mask相关节点（如ResizeImageMask）需在LoadImage后立即处理
```