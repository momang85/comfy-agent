# 采样与调度速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **KSampler** — 使用指定的模型、正向和负向条件对潜在图像进行去噪，生成新的图像。（关键参数: model, seed, steps, cfg）
  - 本机接线: model ← CheckpointLoaderSimple; positive ← ImpactSwitch; negative ← ImpactSwitch
  - 坑: 忘记连接负向提示词（negative），可能导致生成不想要的元素。
- **KSamplerAdvanced** — 高级采样器，用于生成图像的潜在表示（关键参数: model, add_noise, noise_seed, steps）
  - 坑: 采样器类型和调度器的组合可能影响生成质量
- **KSamplerSelect** — 选择采样算法类型（关键参数: sampler_name）
  - 坑: 未选择时可能使用默认采样器
- **BasicScheduler** — 基础噪声调度器，控制采样步数和去噪强度（关键参数: model）
  - 坑: 步数不足可能导致细节缺失
- **RandomNoise** — 生成随机噪声，用于图像生成或视频帧间过渡（关键参数: noise_seed）
  - 坑: 种子值固定会导致重复结果，需动态变化以获得多样性
- **SamplerCustomAdvanced** — 高级自定义采样器，整合噪声、引导器和采样器（关键参数: noise）
  - 本机接线: noise ← RandomNoise; guider ← CFGGuider; sampler ← KSamplerSelect
  - 坑: sigmas格式需正确，否则可能导致采样失败
- **CFGGuider** — 基于分类器自由引导（CFG）的采样引导器，用于控制生成方向（关键参数: model, positive, negative, cfg）
  - 本机接线: model ← LoraLoaderModelOnly; positive ← LTXVConditioning; negative ← LTXVConditioning
  - 坑: cfg值过高可能导致图像过度拟合条件，过低可能导致生成偏离主题；条件输入不匹配可能导致引导失败
- **SamplerCustom** — 高度自定义的采样器，支持噪声和种子控制（关键参数: model）
  - 坑: 低cfg值可能导致生成失控
- **AlignYourStepsScheduler** — 自定义采样调度器，用于控制生成步数和去噪强度（关键参数: model_type, steps, denoise）
  - 坑: 步数设置过少可能导致生成质量下降
- **APG** — 自适应投影引导采样（关键参数: model, eta, norm_threshold, momentum）
  - 坑: 参数敏感，需小范围调试
- **AddNoise** — 向潜在图像添加噪声（关键参数: model）
  - 坑: sigmas值过高可能导致图像过度失真
- **BasicGuider** — 基础引导器，简单条件引导（关键参数: model）
  - 坑: 功能单一，复杂条件可能无法有效控制
- **BetaSamplingScheduler** — Beta采样调度器，适用于特定扩散模型（关键参数: model）
  - 坑: alpha/beta需为正数
- **CFGOverride** — 动态调整采样过程中的分类器引导强度（关键参数: model）
  - 坑: 调整范围过窄可能无效果

## 惯例与骨架
- SDXL：dpmpp_2m+karras 25-35 步 CFG 4-6；novaAnimeXL 官卡推荐 Euler a 20-30 步 CFG 4-6
- SD1.5：euler/dpmpp_2m+karras 20-30 步 CFG 6-8
- 蒸馏/turbo 模型：1-8 步、CFG≈1（多数不用负向）
- hires/多段管线用收敛型采样器（euler a 每步漂移，二段不可复现）

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
