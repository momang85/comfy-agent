# 大脑的两个静默故障 + comfy_status（2026-10-29）

这一轮不是加功能，是**先把大脑接回用户身上**。两处缺陷都不报错、不崩、不写日志，
从外面看只觉得"它好像有点答非所问"。

## 一、工具循环被跑了两遍

`brain/agent.py` 的 `handle()` 里：

```python
try:
    final_reply = self._tool_loop(run_count)
except ... :
    ...
else:
    final_reply = self._tool_loop(run_count)      # ← 多余的 else
```

`try` 里没有任何 `return`，所以 `else` 一定会执行：**每条用户消息都把整个工具循环
跑两遍**。第一遍的副作用（上传、提交、下载、改草稿）全部真实发生，第二遍带着第一遍
的 history 再想一次——请求成本翻倍，模型还会看到自己"刚做过"的事并重复执行。

修法：删掉 `else` 分支。回归测试 `tests/test_repair_guard.py::TestToolLoopRunsOnce::
test_handle_calls_tool_loop_once` 用 mock 数调用次数，**在旧代码上会失败**
（`AssertionError: 2 != 1`，`git stash push -- brain/agent.py` 可复现）。

## 二、模型说的话一句都没到用户

`_tool_loop` 结尾是 `return ""`：

```python
self._compact_history()
return ""          # ← 这里本该是 return final_reply
```

`return ""` 原本是"轮数用尽、让兜底汇报接手"的哨兵，但 refactor（3f27426，
2026-09-13 把循环从 `handle` 抽成方法）时把两处出口合并成了一个，于是**所有正常
结束的回合也返回空**。`handle()` 见到空串就调 `_fallback_summary()`
（`brain/agent.py:340`），用户收到的永远是那句模板文案：

```
本轮任务已完成，先给你看当前结果：
本轮没有产出可用图片，请补充要求或让我换个做法
```

模型的解释、追问、纠错全部被这句吃掉。因为兜底文案读起来很像"正常汇报"，这个
bug 活了一个半月没被发现。

修法：`return final_reply`。回归测试 `test_model_reply_reaches_user` mock
`_chat_streamed` 返回固定句子，断言 `handle()` 原样返回它。

修复后同一句话的对比：

| | 用户看到的 |
|---|---|
| 修复前 | 本轮任务已完成，先给你看当前结果：本轮没有产出可用图片… |
| 修复后 | 本机 ComfyUI 版本我这边查不到（没有查版本的接口）… |

## 三、缺一个"我这卡能跑吗"的工具

修完上面两个，模型终于能完整说话了，于是第一句就暴露真缺口：用户问
「本机什么显卡、多少显存、能跑哪些模板」，模型连调 3 次 `list_models`，最后答
"工具里没有查版本的接口"——**这是事实**：28 个工具里没有任何一个碰状态或硬件。

新增 `brain/tools.py::tool_comfy_status`，一次回答三件事：

- ComfyUI 版本、Python / PyTorch 版本、内存、每张卡的名称与总/空闲显存（来自
  `/system_stats`）；
- 每个模板在这张卡上的显存档位：`can_run`（≤ 总显存 80%）/ `tight` / `too_big`；
- 每个模板**此刻真能不能跑**：`ready = 显存够 且 模型齐全`，缺什么列在
  `missing_models` 里，`runnable_now` 汇总"现在就能跑的那几个"。

三个刻意的取舍：

1. **用十进制 GB（1e9 字节）而不是 GiB。** 显卡标称的「12GB」就是 12.79e9 字节，
   换成 GiB 显示 11.9，于是所有标注「显存 ~12GB」的模板全被判成跑不动，答案从
   "紧张但能跑"变成"你别想了"。实测这台 RTX 5070 Ti Laptop：12.8 GB，qwen_edit
   落在 `tight`。
2. **缺模型用 `model_exists()` 判，不用模糊 `find_model()`。** 后者会把
   `new.safetensors` 认成 `old.safetensors`（见 `comfy_agent/knowledge.py:354`
   的注释），"能跑"的判断就会放过真正缺少的模型，执行时才炸。
3. **先给 `summary` 一句话结论。** 实测：只给结构化字段时，模型会认真列出 7 个缺
   checkpoint 的模板，却**漏掉唯一能跑的那个**（qwen_edit），然后建议用户去下载
   模型。把"现在能直接跑：extract_frame、merge_videos、qwen_edit"顶到第一条之后，
   它才正确地说"现在立刻可用：qwen_edit"。

## 四、验证

- `python -m unittest discover -s tests` → **393 tests OK (skipped=1)**
- 真机问同一句话，模型输出（节选）：

  > **现在立刻可用**：`qwen_edit`（指令改图）、`extract_frame`（视频取帧）、
  > `merge_videos`（视频合并）
  > **真正的卡点是模型文件**：图像生成类模板都缺 checkpoint…

- 9 条 `tests/test_comfy_status.py` 覆盖：版本/显卡字段、三档显存分界、缺模型时
  `ready=false`、严格匹配、summary 不含缺模型的模板、连不上时如实报错、注册与描述。
