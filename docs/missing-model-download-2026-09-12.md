# 缺模型搜索 + 下载弹窗（2026-09-12）

用户诉求：**LLM 发现缺少模型时，去模型站查有没有、在哪下，弹窗告知名称/大小/是否适配本机/该下到哪个目录，
用户同意则在同一个弹窗里监控下载，拒绝或失败就按原有降级路径走。**

## 一、用户可见流程

```
任务因缺模型失败
  → 引擎 early-return 带 missing_models + next_step（并抛 model_missing 事件 → 顶部提示条）
  → 大脑 search_models(filename, folder)
       本机 ComfyUI-Manager 目录（离线，含真实 url/size/save_path）
       → hf-mirror → HuggingFace → Civitai → ModelScope（任一命中即止）
  → 大脑 download_model(url, filename, folder, size, ...)
       弹窗 #modeldlmodal：文件/名称/大小/来源/是否适配本机/存放目录 + 警告（大小不符、空间不足…）
  → 用户「下载」→ 同弹窗变成进度条（百分比 · 已传输 · 速度 · 已用时长）+「取消下载」
  → 完成：原子落位 → 弹窗提示"下载完成，正在自动重跑刚才失败的任务…"
       同时向该项目大脑投递 [system] 消息 → 大脑自动重跑原任务（同一 项目|模板|文件 只重跑一次）
  → 用户「不下载」/ 下载失败 / 取消：弹窗给出状态，大脑收到 [system] 降级提示并沿规则 12 降级
       （失败时给出文件名、目标目录、手动下载地址）
```

## 二、代码落点

| 层 | 文件 | 内容 |
|---|---|---|
| 核心 | `comfy_agent/model_download.py`（新增，纯标准库） | URL 公网校验 + 逐跳重定向复校验、路径安全、四源搜索、`DownloadManager` 非阻塞下载 |
| 引擎 | `comfy_agent/runner.py:301-317` | 缺模型分支补 `next_step` + 抛 `model_missing` 事件（新 `_emit_event`） |
| 大脑 | `brain/tools.py` | `search_models`（只读查询）/ `download_model`（登记即返回）；候选缓存用于**校正大脑自拟的 url/大小** |
| 大脑 | `brain/agent.py` 规则 13 | 缺模型先查来源再问用户；url/size 必须照抄候选 |
| Web | `brain/web/server.py` | `GET /api/model-downloads`、`POST /api/model-download`、`bind_model_downloader()`、`_download_finished()`（自动重跑/降级投递） |
| 前端 | `index.html` / `style.css` / `app.js` | `#modeldlmodal` 弹窗 + 进度条；`model_download`/`model_missing` 事件分派；刷新或切项目后从服务端恢复未决弹窗 |

## 三、三道安全边界

1. **出网**：仅 http/https；host 经 DNS 解析后拒绝 loopback/私有/链路本地/保留/多播/未指定（`ip.is_global` 兜底）；
   拒绝内嵌凭据；重定向**逐跳复校验**（`_ValidatingRedirect.redirect_request`，上限 4 跳）——防公网地址 302 到内网。
   （与 `client.py` 的回环白名单方向相反：那里只连本机 ComfyUI，这里只连公网。）
2. **落盘**：folder/filename 逐段校验（禁 `..`、`.`、盘符、冒号、绝对路径、空段、Windows 保留名）；
   folder 必须在 `MODEL_FOLDERS` 白名单或已是磁盘上存在的目录；后缀必须在模型文件白名单；
   最终路径 `resolve()` 后必须仍在 `models/` 内（用 `Path.parents` 判定，避免 `models_evil` 前缀绕过）。
3. **执行**：后台线程 + `.part` 临时文件 → 校验（非空 / 不小于 Content-Length 的 90% / 不是 HTML 错误页）→ `os.replace` 原子落位；
   校验不过绝不进 `models/`；单文件上限 `MODEL_DOWNLOAD_MAX_GB`（默认 40GB）+ 落盘前留 1GB 余量。

## 四、实测证据（2026-09-12，真机真网络）

| 项 | 方式 | 结果 |
|---|---|---|
| 搜索 | 引擎内调 `search_models`（真 Manager 目录） | `ltx-video-2b-v0.9.1.safetensors` / `taef1_decoder.pth` 均命中，大小/目录/适配正确 |
| 下载 | 引擎内 request→confirm，真网络（GitHub raw→302→raw.githubusercontent） | 4943336 字节落位，`.part` 清空，`verify.ok`，5 个进度事件（0→21→64→100%） |
| 弹窗信息 | 浏览器实测 | 文件/名称(TAEF1 Decoder)/大小(4.71 MB)/来源(本机 Manager 目录)/适配(适配本机)/存放目录 |
| 同弹窗监控 | 浏览器点「下载」 | 进度条 + `100.0% · 4.71 MB · 850.90 KB/s · 已用 5.7s`，按钮变「取消下载」 |
| 自动重跑 | 浏览器点「下载」后完成 | 弹窗提示"下载完成，正在自动重跑…"；大脑收件箱收到 [system] 消息并回复（无具体失败任务时如实说明） |
| 拒绝降级 | 浏览器点「不下载」 | 弹窗"已拒绝下载，大脑会按缺模型降级处理"；大脑回复给出文件名/目标目录/手动地址 + 等价模板方案 |
| 失败 | 真 404/401 地址 | 弹窗先给"未能核实该地址的文件大小"警告；下载后 `failed: HTTPError 401`，无文件残留 |
| 刷新恢复 | 浏览器 reload | 未决弹窗从 `GET /api/model-downloads` 恢复 |
| 单测 | `tests/test_model_download.py`(31) + `tests/test_web.py` 新增 5 | 全绿，且**完全离线**（注入 resolver/`_head_size`/`_open_stream`，不依赖 DNS） |

## 五、本轮实测暴露并修复的问题

| # | 问题 | 根因 | 修复 |
|---|---|---|---|
| D1 | `COMFY_ROOT` 两种形态混用：`comfy_root.local`/`一键启动.bat` 存**整合包根**（`…\ComfyUI-aki-v3`），而 `config.py` 期望**ComfyUI 目录**（`…\ComfyUI-aki-v3\ComfyUI`） | 两侧各自定义，无归一；`install.bat` 校验的是 `%PATH_IN%\ComfyUI\main.py`，配置读的是 `%COMFY_ROOT%\models` | `config._normalize_comfy_root()`：两种形态都认；回归测试 `tests/test_settings.py::test_comfy_root_accepts_portable_root` |
| D2 | 大脑**自己拼 HuggingFace 地址**（`huggingface.co/taef1/taef1_decoder/...`，实测 401）并**自己估大小**（4.71MB 报成 1.2GB） | 工具只给了候选，未强制"照抄"；无二次校验 | ① `search_models` 缓存候选，`download_model` 发现 url/大小/来源与候选不符时**改用候选并回传 `url_corrected`**；② `request()` 弹窗前 HEAD 探测真实大小，与标注差异 >20% 写入 `warnings` 显示在弹窗；③ 系统提示规则 13 明确禁止自拼地址/自估大小 |
| D3 | 校验基准错误：以**登记大小**判"内容偏小"，大脑估错时会误杀正确文件 | 校验用 `rec["size"]` | 改用**本次传输的 Content-Length**（权威），拿不到才退回登记值 |
| D4 | 候选文件名匹配过宽：查 `definitely-not-a-real-model-xyz.safetensors` 会命中 `model.safetensors`（子串判据） | `_score_filename` 用 `in` 双向包含 | 改为 同名/前缀/公共前缀占比（短名 ≥6 且公共前缀 ≥8 且 ≥60%） |
| D5 | 单测会**真出网**（`size=0` 时触发 HEAD），并因未关闭 `HTTPError` 泄漏句柄（ResourceWarning） | 测试未隔离网络；异常分支未 `close()` | 测试注入假 resolver/`_head_size`/`_open_stream`；`_head_size` 在 `HTTPError` 分支 `e.close()` |

## 六、已知边界（未做）

- **多分片模型**不会自动连带下载（弹窗只对当前文件；分片需逐个下载）。
- Civitai/ModelScope 走公开接口：需要 API key 或匿名限流的条目会失败 → 按降级处理（`Manager` 目录与 HF 通常已覆盖）。
- 已存在同名文件时不覆盖（`os.replace` 会覆盖）——当前无"是否覆盖"确认；实际使用中 `search_models` 只在缺模型时被调用。
- 断点续传未实现（失败重来；4.7MB~40GB 单文件在内网/镜像下可接受）。
