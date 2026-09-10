# 逻辑与工具速查（本机实测节点）

> 生成于 2026-09-10，数据源 = 本机 object_info 快照（1527 节点）+ 实景档案。所有类名经存在性断言。

## 核心节点（本机存在）
- **ImpactSwitch** — 通用多路选择器，用于切换任意类型输入（关键参数: select, sel_mode, input1, input2）
  - 本机接线: input1 ← EmptyLatentImage; input2 ← ImpactSwitch; input1 ← CheckpointLoaderSimple
  - 坑: sel_mode参数可能影响选择逻辑，需根据实际需求设置
- **ImpactValueSender** — 跨节点传递数值（关键参数: value, link_id, signal_opt）
  - 坑: link_id不匹配会导致传递失败
- **ImpactValueReceiver** — 接收跨节点传递的数值（关键参数: typ, value, link_id）
  - 坑: 类型不匹配会导致运行时错误
- **ImpactLogicalOperators** — 执行布尔逻辑运算（关键参数: operator, bool_a/bool_b, 建议从条件判断节点或ImpactBoolean接入）
  - 坑: xor运算在复杂逻辑中可能难以直观理解
- **ImpactInt** — 生成或传递整型数值（关键参数: value, 默认0）
  - 坑: 需注意数值范围，避免溢出
- **ImpactFloat** — 生成或传递浮点型数值（关键参数: value, 默认1.0）
  - 坑: 浮点运算可能存在精度误差
- **ImpactNeg** — 对布尔值取反（关键参数: value）
  - 坑: 需确保输入确实是布尔类型
- **ImpactMinMax** — 比较两个值并返回最小/最大值（关键参数: mode, a/b）
  - 坑: 非数值类型比较可能无意义
- **ImpactIfNone** — 如果输入为空则返回默认值（关键参数: signal, any_input）
  - 坑: 默认值类型与预期输出不匹配可能导致后续处理错误
- **ImpactDummyInput** — 提供虚拟输入，用于测试或占位（关键参数: 无输入参数）
  - 坑: 未在本机工作流中使用，具体功能需参考官方文档
- **ImpactStringSelector** — 从字符串列表中选择一项（关键参数: strings, select, multiline）
  - 坑: 索引越界会导致选择失败
- **ImpactWildcardEncode** — 处理通配符语法文本提示，并输出条件信息，同时支持LoRA加载。（关键参数: model, clip, wildcard_text, populated_text）
  - 坑: 在'fixed'模式下，需直接在'populated_text'中填写完整文本，否则可能无法正确生成。
- **ImpactWildcardProcessor** — 处理通配符文本，支持填充、固定和重现模式（关键参数: wildcard_text, populated_text, mode, seed）
  - 本机接线: seed ← Seed (rgthree)
  - 坑: seed参数影响随机性，需确保Seed节点正确连接
- **PrimitiveString** — 生成单行文本常量（关键参数: value）
  - 坑: 仅支持单行文本，多行需用PrimitiveStringMultiline

## 惯例与骨架
- 生成任务尽量用模板参数，逻辑节点只在批量/切换需求时引入
- 字符串模板用 PrimitiveStringMultiline 或 Impact 通配符节点

---
本文档由 `scripts/rebuild_family_docs.py` 生成；节点库变动后重跑：`python scripts/rebuild_family_docs.py`
