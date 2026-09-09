# 文本与条件节点家族速查

```markdown
# ComfyUI【文本与条件】家族速查

## 家族通用接线模式
- **输入**：文本/字符串（主输入）、条件（可选）  
- **输出**：处理后的文本/字符串、布尔值（条件类）  
- **核心逻辑**：单节点处理文本流，多节点通过`MergeTextLists`等组合链式调用。

## 关键节点与参数要点
- **BuildJsonPromptIdeogram**：构建结构化提示，需严格按JSON格式输入。  
- **TextTo(Upper/Lower)Case/CaseConverter**：大小写转换，支持批量处理。  
- **ReplaceText/RegexMatch**：文本替换/正则匹配，注意`pattern`区分大小写。  
- **StringFormat**：模板填充（如`"Hello {name}"`），变量需与输入键名一致。  
- **SetClipHooks**：动态修改CLIP编码，需配合`CLIPTextEncode`下游使用。  
- **CFGGuider**：条件引导，需传入`conditioning`和`text`。

## 常见坑
- **类型不匹配**：字符串节点输出未转`string`类型时，下游可能报错。  
- **正则转义**：`RegexMatch`中特殊字符需双反斜杠（如`\d`写为`\\d`）。  
- **空文本处理**：`StripWhitespace`可能意外移除必要空格，建议预检查输入。

## 与相邻家族接口约定
- **上游**：`CLIPTextEncode`需接收字符串输出，`PromptBuilder`家族可直接拼接文本节点。  
- **下游**：`Conditioning`家族（如`CFGGuider`）需文本节点提供`text`输入，`CLIP`家族需字符串输入。
```