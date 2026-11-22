# 常见故障

四类最容易撞上的问题：取不到音轨、导出的片子发暗发灰、预览黑屏、ffmpeg 下不下来。每节按【现象】【原因】【两步解法】【依据】写，报错原文对不上号就翻最后一节的表。

文里的数字都是在 Windows 上实测的：Qt 6.10.0（自带 FFmpeg 7.1.2 后端）、ffmpeg 9.0.2 gyan full build，探针脚本放在 %TEMP%，没进仓库。

## 一、取不到音轨

### 现象

粘完链接，状态栏停在「正在取音轨…」几分钟不动；或者弹一个「取音轨失败」的框，正文是中文（B 站）或一句笼统的「取音轨失败（详情见设置里重试一次）」（其它站点）。

### 原因

1. **B 站链接走的是自带的接口实现，不经过 yt-dlp。** 出错时按 B 站返回的 `code` 翻成中文（`bili_audio.py:21-40`），所以你能看到「付费视频，需要购买后才能观看」这种话。翻不出来时才显示「B 站接口返回错误（code=…）」。`docs/research/doc_errcode.md` 里那份公共错误码表和它一一对得上。
2. **非 B 站链接必须先有 yt-dlp。** 没装不会静默失败，会弹「需要音源组件」（`pic2video.py:2605-2624`）；在简洁模式里如果没弹，是因为上一次的任务还没结束（`start_fetch` 见到 `self.fetch` 非空直接 return，`pic2video.py:2588-2603`）。
3. **一直不动有两处合法等待。** yt-dlp 的超时是 900 秒（`audio_source.py:179-183`），B 站 `_get` 失败会重试 3 次（`bili_audio.py:58-72`）。网络被限流时就表现为很久没动静。
4. **最坑的一点：yt-dlp 的原始报错在 GUI 里被丢了。** `FetchWorker` 只把 `SourceError.message` 发出去（`pic2video.py:980-983`），`detail`（`audio_source.py:38-44`）用户看不到。所以「取音轨失败（详情见设置里重试一次）」背后可能是登录、风控、地区、403 任何一种。

### 两步解法

1. **先认弹窗正文。** 是「需要源组件」就点「下载并继续」（约 25 MB，`components.py:30`）；是「付费视频」「充电专属」「地区限制」「版权限制」这类——换链接，登录也救不了；是「请求被 B 站风控拦截」——等几分钟再试，别连着点。
2. **判不出来就换一条路取同一段音轨。** 把整段视频（本地文件）直接拖进「音乐链接」框，走本地抽取（`audio_source.py:33-67` 的 `local` 分支）；或者网页里下好 m4a 再拖进来。这条路不经过网络接口，也不经过 yt-dlp。

### 依据

- 错误码与中文文案：`bili_audio.py:21-40`、`bili_audio.py:47`、`i18n.py:28-47`、`i18n.py:104-109`。
- yt-dlp 的调用参数与超时：`audio_source.py:179-183`；友好化映射：`audio_source.py:121-135`。
- 组件缺失与平台判断：`audio_source.py:80`、`audio_source.py:88`、`audio_source.py:172`；`pic2video.py:2605-2624`。
- 用户看不到 detail：`pic2video.py:980-983`。
- 需要登录/要 Cookie 的一手说明：yt-dlp 官方 README 的 `--cookies` / `--cookies-from-browser` 选项（https://github.com/yt-dlp/yt-dlp#usage-and-options）；官方 README 首页 https://github.com/yt-dlp/yt-dlp#readme 。
- B 站公共错误码（-352 风控、-400 参数、-403 权限、-404 不存在、-412 IP 风控、-688 地区、-689 版权）：仓内抓取的官方表 `docs/research/doc_errcode.md:7` 起。

## 二、导出的片子发黑 / 颜色不对

### 现象

三种说法混在一起：整片偏暗；白的地方不白、颜色发灰；画面四周比中间暗一截。

### 原因

1. **片子本身是 limited range（16–235），但文件里没写范围标记。** 出片参数只有 `-pix_fmt yuv420p -color_range tv`（`pic2video.py:746`），实测 `ffprobe` 读回来 `color_range=unknown`、`color_space=unknown`。纯白图出片后亮度是 Y=235，用 ffmpeg 正常解码仍是 RGB(255,255,255)；但播放器如果按 full 去解释这段数据，白就变成 235/255 ≈ 92%，黑被抬到 16，观感就是「发灰、不够亮、对比度低」。这不是编码错了，是标记缺了、播放器猜错了。
2. **「原图铺底 + 背景模糊」会故意把四周压暗。** 代码里背景链是 `gblur=sigma=40,eq=brightness=-0.08`（`pic2video.py:648-649`），默认 `fit="own"`、`bg_blur=True`（`pic2video.py:248-249`）。实测纯橙 #FF8000 的 1600×1200 图出 640×360：中心（原图主体）RGB(253,126,0) 不变，左边缘（模糊背景）变成 RGB(228,100,0)，每通道掉了约 25，肉眼就是「四周暗一圈」。
3. **主体本身不会被压暗。** 同一张图 `fit="cover"` 或关掉 `bg_blur`，四周也是 (253,125,0)。所以「整张都暗」指向第 1 条（播放器/范围），「只有四周暗」指向第 2 条（设计如此）。

### 两步解法

1. **拿同一个 mp4 换播放器对比。** 用 mpv 或 VLC 打开：正常了 → 片子没问题，是别的播放器把 limited 当 full 解，换播放器看即可（Windows 自带播放器、部分剪辑软件预览会这样）。
2. **换播放器还是暗，就看是不是四周暗。** 四周暗：设置里把「铺底」改成「裁切铺满」（`fit=cover`，`pic2video.py:626-629`），或关掉背景模糊；整片暗：对照原图看亮度，别再用「原图铺底」再认一次。

### 依据

- 出片参数：`pic2video.py:744-749`；滤镜里的范围转换 `in_range=full:out_range=tv`：`pic2video.py:618`、`:629`、`:633`、`:641`、`:647`、`:653`。
- 背景压暗：`pic2video.py:648-649`；默认值：`pic2video.py:248-249`。
- 实测（%TEMP%\p2v_faq_dark.py，直接调 `pic2video.render()`）：own+模糊 中心 (253,126,0) / 左缘 (228,100,0)；own 不模糊 左缘 (253,125,0)；cover 全图 (253,125,0)；`ffprobe` 只报 `pix_fmt=yuv420p`。
- 实测（%TEMP%\p2v_faq_color.py）：白图出片 Y=235，正常解码 RGB(255,255,255)；灰 128 → Y=126、解码 128；黑 → Y=16。数据是 tv、硬标 `-color_range pc` 时 pix_fmt 会变 `yuvj420p`、Y=255。
- ffmpeg 官方 filter 文档：`scale` 的 `in_range`/`out_range` 取值 auto/limited(tv)/full(pc)，https://ffmpeg.org/ffmpeg-filters.html#scale ；`format` 与 `yuv420p`/`yuvj420p` 的对应关系 https://ffmpeg.org/ffmpeg-filters.html#format 。

## 三、预览黑屏

### 现象

窗口中间那块一片黑：点「刷新预览」没反应，或者状态栏冒一句「预览播放出错：Could not open file」，画面还是黑的。

### 原因

1. **预览区本来就是黑底。** `QVideoWidget` 被显式刷成黑（`pic2video.py:2182` `QVideoWidget{background:#000000;}`，另外还有黑底调色板 `pic2video.py:2027-2032`），没画面时和「出错」长得一模一样——只能看状态栏。
2. **播放器打不开文件时只报状态栏，不动画面。** 实测 Qt 6.10.0：文件不存在 → `ResourceError(1)`，`errorString` 是 `Could not open file`，`mediaStatus` 走到 `InvalidMedia`；文件损坏（`moov atom not found` 那种截断 mp4）→ `FormatError(2)`，文案同样是 `Could not open file`。App 的 `on_player_error` 把这句话拼成「预览播放出错：…」写进状态栏（`pic2video.py:2474-2485`），所以黑屏 + 这行字 = 播放器没拿到文件。更糟的是 `errorString` 为空时它直接 return（`pic2video.py:2476-2477`），连状态栏都不写——纯黑屏、一个字提示都没有。
3. **老毛病：播放器握着文件，ffmpeg 写不进去。** 实测播放中用另一个句柄截断同一个文件直接被 Windows 拒绝（`PermissionError(13, 'Permission denied')`），说明播放器确实占着文件。现在的 `on_preview` 会先 `stop()` + `setSource(QUrl())` 松手，并且每次都是新文件名 `preview_N.mp4`（`pic2video.py:2528-2560`），这个问题基本被绕开了。
4. **渲染没成功时那块地方也不会变。** 失败会弹「预览失败」的框，正文是 ffmpeg 的原文（`pic2video.py:2575-2582`）。

### 两步解法

1. **先看状态栏那行字。** 「生成预览…」= 还在渲，等它；「预览播放出错：…」= 文件坏了或被删了，点「刷新预览」重出；弹窗「预览失败」= ffmpeg 的原文，对照第四节看是不是磁盘/权限；一个字都没有 = 播放器报了空错误（`pic2video.py:2476-2477`），往下走。
2. **没字就换地方再试一次。** 把输出目录换到 `D:\` 这类纯英文短路径（临时目录被清、路径带中文都可能让外部工具读不到），重开程序点一次「刷新预览」；还是黑就换台机器验证是不是显卡/驱动解不了这条 H.264。

### 依据

- 黑底与调色板：`pic2video.py:2182`、`pic2video.py:2027-2032`。
- 错误处理：`pic2video.py:2474-2485`（状态栏文案 + 400 ms 后重挂）、`pic2video.py:2465-2468`（`mediaStatus` 只处理 `EndOfMedia`）、`pic2video.py:2575-2582`（预览失败弹窗）。
- 换新文件名 / 先松手：`pic2video.py:2528-2560`。
- 实测（%TEMP%\p2v_faq_qt.py，Qt 6.10.0，FFmpeg 后端 7.1.2）：正常 2 s 片子解 60 帧、状态 Loading→Loaded→Buffering→Buffered→EndOfMedia；缺文件 `ResourceError`+`Could not open file`+`InvalidMedia`；坏文件 `FormatError`+同文案+`InvalidMedia`；播放中截断被系统拒绝。
- Qt 官方文档：`errorOccurred` 信号与 `Error` 枚举（NoError/ResourceError/FormatError/NetworkError/AccessDeniedError）https://doc.qt.io/qt-6/qmediaplayer.html#errorOccurred 、`errorString()` 与 `mediaStatus` 的 InvalidMedia 说明 https://doc.qt.io/qt-6/qmediaplayer.html#mediaStatus 、`QVideoWidget` https://doc.qt.io/qt-6/qvideowidget.html 、`QAudioOutput` https://doc.qt.io/qt-6/qaudiooutput.html 。

## 四、ffmpeg 下载失败

### 现象

第一次打开问「现在就自动下载吗？」，点了「现在下载」，进度框卡住或最终弹「下载失败」；命令行版则打印「没找到 ffmpeg，正在下载（约 90 MB，只下这一次）…」然后退出。

### 原因

1. **下载是“一次性”的，没有续传也没有重试。** 源是 gyan.dev 的 essentials zip（`components.py:29`），`_download` 里一次 `urlopen(..., timeout=60)` 读完，任何异常都只变成「下载失败：检查网络，或者稍后再试一次」（`components.py:178-196`）。慢网/代理抖一下，90 MB 就得从头再来。
2. **解包后还有两道关卡会失败。** 包里找不到 exe → 「下载包里找不到 %s（源可能变了）」（`components.py:226`）；解出来的文件被杀毒软件吃掉 → 「装完了但文件不在，检查杀毒软件是否拦了」（`components.py:234`）。
3. **已经有 ffmpeg 就不会下载。** `components.exe()` 先看 `tools/`，再 `shutil.which`（`components.py:122-130`）；PATH 里有就别指望它下。

### 两步解法

1. **手动装，绕开下载器。** `winget install Gyan.FFmpeg`（对话框里原话就是这个），或者把 `ffmpeg.exe`、`ffprobe.exe` 直接丢进程序目录下的 `tools/`。
2. **装完重开一次程序。** 还报缺就是没被找到，对应提示是「ffmpeg 装好了但还是找不到，重开一次窗口试试」（`pic2video.py:812`）；确认 `tools/` 里两个 exe 都在，别只放一个。

### 依据

- 下载源与超时：`components.py:29`、`components.py:178-196`；解包校验：`components.py:225-234`；查找顺序：`components.py:122-130`。
- GUI 流程与文案：`pic2video.py:2769-2815`（「第一次运行」`pic2video.py:2775`、「正在下载 ffmpeg…」`pic2video.py:2784`、「下载失败」`pic2video.py:2810`）；CLI：`pic2video.py:797`、`pic2video.py:812`。
- gyan.dev 的 essentials 包说明：https://www.gyan.dev/ffmpeg/builds/ 。

## 报错原文对照表

「出处」是文案在仓库里的位置；「触发」是按代码能复现的条件。

| 报错原文（用户看到的） | 出处 | 触发条件 | 怎么修 |
| --- | --- | --- | --- |
| 正在取音轨…（一直不动） | pic2video.py:2598 | 链接已提交，等接口/yt-dlp 返回 | 等满 15 分钟没动静就重开；换本地文件取音轨 |
| 取音轨失败 + 弹窗 | pic2video.py:2640、pic2video.py:2641 | 任何 SourceError / BiliError | 看弹窗正文，按下表对症 |
| BGM 已就绪：{总长} · {文件名} | pic2video.py:2634-2636 | 成功 | 不用管 |
| 视频不存在或已被删除 | bili_audio.py:22（-404） | 链接里的 BV/av 不存在 | 换链接 |
| 稿件不可见（已删除/仅自己可见/审核中） | bili_audio.py:23（62002） | 稿件被下或私有 | 换链接 |
| 稿件审核中 | bili_audio.py:24（62004） | 刚投的稿还没过审 | 过审再来 |
| 没有访问权限（可能是付费/充电专属内容） | bili_audio.py:25（-403） | 付费/充电内容 | 换免费视频 |
| 请求参数有误，链接可能不是有效的视频地址 | bili_audio.py:26（-400） | 链接残缺、p 参数越界 | 复制完整链接 |
| 请求被 B 站风控拦截，请稍后重试或降低频率 | bili_audio.py:27（-352） | 连续请求、UA/签名被拦 | 等几分钟再试 |
| 请求被风控拦截（IP 层面），请稍后再试 | bili_audio.py:28（-412） | 出口 IP 被风控 | 换网络/等 |
| 该内容有地区限制，当前网络无法观看 | bili_audio.py:29（-688） | 地区限制 | 换链接，代理也可能无效 |
| 该内容有版权限制，无法观看 | bili_audio.py:30（-689） | 版权下架 | 换链接 |
| 充电专属视频，需要给 UP 主充电后才能观看 | bili_audio.py:31（87007） | 充电专属 | 换链接 |
| 付费视频，需要购买后才能观看 | bili_audio.py:32（87008） | 付费课程/番剧 | 换链接 |
| 该内容在当前地区不可观看 | bili_audio.py:33（-10403） | 地区限制变体 | 换链接 |
| 这是会员/付费内容的试看片段，完整音轨需要大会员账号 | bili_audio.py:34、:217-223 | 返回的时长只有试看那几秒 | 拿不到完整版，换链接 |
| 直播流没有点播音轨，本工具只支持点播视频/番剧 | bili_audio.py:35 | 粘的是直播间 | 换点播视频 |
| 付费课程需要登录且已购买，本工具暂不支持 | bili_audio.py:36 | 课程/cheese 链接 | 不支持 |
| 网络请求失败，请检查网络或稍后重试 | bili_audio.py:37 | 网络异常（重试 3 次后） | 查网络/代理 |
| B 站返回了非 JSON 内容（可能被风控或需要验证） | bili_audio.py:39 | 被风控页/验证码页顶替 | 等一会儿，换网络 |
| B 站接口返回错误（code=…, message=…） | bili_audio.py:47 | 错误码不在表里 | 按 message 搜，或等更新 |
| 无法识别的链接：… | bili_audio.py:126 | 既不是 BV/av 也不是 ep/ss | 复制浏览器地址栏整条 |
| 分P参数 p=%d 超出范围（共 %d P） | bili_audio.py:238 | 链接带 ?p=99 但只有 10P | 去掉 p 参数 |
| 没有可用的音频流 | bili_audio.py:252 | 只有视频轨/音轨被删 | 换链接 |
| 需要 ffmpeg 转 wav，但 PATH 中没有 | bili_audio.py:179 | 取到 wav 但找不到 ffmpeg | 先修 ffmpeg（第四节） |
| 这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载 | audio_source.py:172 | web 链接 + 没装 yt-dlp | 点下载，约 25 MB |
| 需要音源组件（对话框）+ 下载并继续 | pic2video.py:2606、:2611 | 同上，首次自动弹 | 点「下载并继续」 |
| 还没下载音源组件 | audio_source.py:80 | 直接调接口时组件不在 | 走 GUI 弹窗下载 |
| 找不到 yt-dlp，去设置里重新下载 | audio_source.py:88 | 组件记录在、exe 没了 | 设置里重下 |
| 解析超时（网络太慢或站点限流），稍后再试 | audio_source.py:90 | 15 分钟没解析完 | 换网络/换链接 |
| 这个链接不支持（可能是私密/直播/需要登录） | audio_source.py:121-135 | yt-dlp 报 Unsupported URL / No suitable extractor | 换链接 |
| 该站点要求登录，暂时取不到音轨 | audio_source.py:121-135 | yt-dlp 报 Sign in / login / cookies | 换能直接播的链接 |
| 视频是私密的，取不到音轨 | audio_source.py:121-135 | 报 private | 换链接 |
| 该视频有地区限制 | audio_source.py:121-135 | 报 geo…restrict | 换链接 |
| 网络连不上该站点（可能要代理） | audio_source.py:121-135 | timeout / connection | 查网络或代理 |
| 下载被站点拒绝（可能需要登录或频繁重试） | audio_source.py:121-135 | HTTP 403 / unable to download | 等一会儿再试 |
| 取音轨失败（详情见设置里重试一次） | audio_source.py:121-135 | 其它一切 | 换链接；原始报错界面不给看 |
| 音轨没落地（可能被站点拦了），换个链接试试 | audio_source.py:193 | 下完但文件不存在/为 0 字节 | 换链接 |
| 先给个链接或本地文件 | audio_source.py:148 | 空输入 | 填链接 |
| 正在从本地视频里抽音轨… / 正在取 B 站音轨… / 正在解析链接… | audio_source.py:159、:167、:174 | 进行中 | 等 |
| 预览播放出错：Could not open file | pic2video.py:2478 | 播放器打不开预览文件（缺文件 ResourceError / 坏文件 FormatError） | 点「刷新预览」重出 |
| 预览失败（弹窗，正文是 ffmpeg 原文） | pic2video.py:2578、:2582 | 预览渲染失败 | 看正文：磁盘满/目录不可写 |
| 先加图片 | pic2video.py:2530 附近 | 没有图片就点预览 | 加图 |
| 预览已更新（点画面播放/暂停，点色带跳转） | pic2video.py:2570-2573 | 预览成功 | 不用管 |
| 第一次运行 / 现在就自动下载吗？ | pic2video.py:2775、:2777 | 缺 ffmpeg | 点「现在下载」或先不用 |
| 正在下载 ffmpeg… | pic2video.py:2784 | 下载中 | 等 |
| 下载失败 / ffmpeg 没下成功。检查一下网络再开一次，或者手动装：winget install Gyan.FFmpeg | pic2video.py:2810-2811 | 下载异常 | 手动 winget 装 |
| 下载失败：检查网络，或者稍后再试一次 | components.py:196 | 任何 urlopen 异常 | 重试或手动装 |
| 下载包里找不到 %s（源可能变了） | components.py:226 | 包里没有目标 exe | 手动装 |
| 装完了但文件不在，检查杀毒软件是否拦了 | components.py:234 | 装完 have() 仍为假 | 关杀软重试 / 手动放 tools/ |
| 没找到 ffmpeg，正在下载（约 90 MB，只下这一次）… | pic2video.py:797 | CLI 模式缺 ffmpeg | 等或手动装 |
| ffmpeg 装好了但还是找不到，重开一次窗口试试 | pic2video.py:812 | 装完 missing_tools() 仍非空 | 重开程序，查 tools/ 里两个 exe |
| ffmpeg 退出码 {rc} | pic2video.py:777 | ffmpeg 非 0 退出且没抓到 stderr | 看导出目录权限/磁盘 |
