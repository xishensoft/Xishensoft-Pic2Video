<!-- DSH-FORMAT: md-v1 -->
<!-- DSH-PARENT: 00 -->
<!-- DSH-LABELS: wayfinder:task -->
<!-- DSH-IDEMPOTENCY-KEY: deck-25c9df47-5984540 -->
<!-- DSH-CREATED: 2026-11-22T15:44:27.124Z -->
<!-- DSH-BLOCKED-BY: 10 -->
# 11: 多平台 BGM：抖音/YouTube 等链接与本地文件（yt-dlp 按需装删）

Type: task

Blocked by: 10
Status: resolved
## Question

BGM 来源要能不只 B 站（用户 m01167：背景音乐链接支持别的视频平台，具体问）。用户 m0117x 拍板全要：抖音（含 v.douyin.com 短链）、YouTube、快手/小红书/微博，加本地音视频文件直接拖进窗口。

依赖策略（用户原话要求）：yt-dlp 不随工具发布，设置里一键下载/一键删除。

## 落点

- 新增 `.scratch/pic2video/audio_source.py`：`kind_of()` 判 local / bili / web，B 站仍走 `bili_audio`，其余走 yt-dlp；`install_ytdlp()` / `remove_ytdlp()` 装进工具目录 `tools/`；错误转中文。
- `pic2video.py`：FetchWorker 多来源、YtdlpWorker、设置窗「抖音 / YouTube 支持」行（下载 / 删除 / 显示版本体积）、超链接时提示装 yt-dlp、拖入本地音频/视频直接当 BGM、命令行 `--audio` 也接受平台链接。
- 串行于票 10：同一批文件（pic2video.py / i18n.py / 设置窗）连续改，合拢会打架。

## Answer

已完成并验证。

**实现**
- 新增 `audio_source.py`：`kind_of()` 把来源分成 `local` / `bili` / `web`（B 站含 b23.tv 短链、裸 BV/av）；`fetch_audio()` 对 `web` 走 `python -m yt_dlp`，取整条音轨并存成 m4a；`install_ytdlp()` / `remove_ytdlp()` 装进工具目录 `tools/`（`pip --target`）；错误统一转中文。
- `pic2video.py`：BGM 框改叫「音乐链接」；`FetchWorker` 支持三种来源；粘 `web` 链接且没装 yt-dlp 时弹一次询问（约 21 MB），装完自动继续；设置窗加「抖音 / YouTube 支持」行（显示版本与体积，一键下载 / 删除）；把本地音频或视频拖进窗口直接当 BGM（`extract_audio` 抽音轨）；命令行 `--audio` 也接受平台链接。

**验证**
- 单测全过（音源组：B 站/短链/裸 BV/av/抖音/抖音短链/YouTube/本地音频/本地视频/普通网页/空来源、`ytdlp_ready`）。
- 命令行三条实跑：`--bili BV13qs46FE9C` 取到 11:01 音轨并出 3 秒片；`--audio tone6.m4a` 出 6 秒片；`--audio https://example.com/not-a-video` 正确以退出码 2 报「下载被站点拒绝」。
- 设置窗截图里看到「已安装 2026.10.26 · 21 MB（占地方可以随时删）」与「删除」按钮。

## 进度
