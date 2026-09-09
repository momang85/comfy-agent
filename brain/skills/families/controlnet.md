# ControlNet节点家族速查

```markdown
# ComfyUI ControlNet 家族速查

## 家族通用接线模式
- **输入**：预处理节点（如Canny）需连接 `image`（原始图）和 `control_net`（模型）；应用节点（如ControlNetLoader）连接 `control_net` 和 `positive/negative`提示词。
- **输出**：预处理节点输出 `CONTROL` 类型，供后续ControlNet应用节点使用。

## 关键节点与参数要点
- **预处理节点**（如Canny、Depth）：
  - `low_threshold/high_threshold`（Canny）：控制边缘检测敏感度。
  - `resolution`（Depth）：调整深度图分辨率，影响精度与性能。
  - `preprocessor_resolution`（通用）：统一设置预处理输出尺寸。
- **应用节点**（如ControlNetApplyAdvanced）：
  - `strength`：控制ControlNet对生成结果的引导强度（0-1）。
  - `start_percent/end_percent`：指定ControlNet生效的步数范围。

## 常见坑
1. **分辨率不匹配**：预处理输出需与生成模型输入尺寸一致，否则导致变形。
2. **模型加载错误**：确保ControlNet模型文件（如`controlnet_canny.pth`）放入正确目录。
3. **过度依赖**：高`strength`值可能导致生成结果僵硬，需结合提示词调整。

## 接口约定
- **上游**：预处理节点需从`LoadImage`获取原始图像。
- **下游**：ControlNet应用节点输出至`KSampler`，需与`CLIPTextEncode`的提示词节点配合使用。
```