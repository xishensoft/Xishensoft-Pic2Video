# Xishensoft's Pic2Video

给几张图片、粘一个视频链接，一键出一条带背景音乐的成片。做视频时最省事的那一步：**图片 + B 站 / 抖音 / YouTube 的音乐 → mp4**。

![主窗口](screenshots/main-window-dark.png)

- **图片随便几张**：拖进来排序，每张单独调秒数，底部时间线点哪跳哪、拖右边界直接改时长。
- **音乐直接从视频链接取整条音轨**：B 站（含 b23.tv 短链 / 裸 BV、av 号）、抖音、YouTube、快手、小红书、微博；也可以把本地音频/视频直接拖进窗口。
- **窗口里就能预览**：540p 预览片在窗口里播，改完参数自动重渲。
- **规格可调**：9 档画面比例 + 宽高手填、30/60fps、画质三档、五种画面铺底（原图铺底 / 纯色留白 / 自定义背景图 / 背景视频 / 裁切铺满）、淡入淡出与转场、导出目录。
- **顺手的小东西**：简体中文 / 繁體中文 / English / 日本語 / 한국어 五种界面；深色/浅色主题；只挑一个主色，自动配对撞色；简洁模式（只留「加图片 / 导出视频」）与专业模式。

## 快速开始

1. 装依赖（只装一次）：

   ```
   python -m pip install PyQt6
   ```

   还需要 `ffmpeg`、`ffprobe` 在 `PATH` 里（实测 9.0.2）。

2. 双击 `启动.bat`（它就是 `python pic2video.py`）。
3. 把图片拖进窗口，或点「加图片」。
4. 把链接粘进「音乐链接」框，等状态栏显示「BGM 已就绪：时长 x:xx」。
5. 看预览，点「导出视频」，选个目录。

## 各平台的音乐

| 来源 | 需要什么 | 说明 |
| --- | --- | --- |
| B 站 | 无 | 不需要登录、不读 Cookie；DASH 音轨里按码率取最高那档 |
| 抖音 / YouTube / 快手 / 小红书 / 微博 | `yt-dlp` | **按需下载**：设置里点「下载」装进工具目录的 `tools/`（约 21 MB），不用了点「删除」清掉；第一次粘这类链接时也会问一次 |
| 本地文件 | 无 | 音频直接当 BGM；视频自动抽音轨 |

取的是**整条音轨**，不做人声分离。

## 命令行（批量/脚本用）

```
python pic2video.py --export 输出.mp4 --audio 本地音频.m4a 图1.jpg 图2.png 图3.webp
python pic2video.py --export 输出.mp4 --audio "https://www.douyin.com/video/..." 图1.jpg 图2.png
python pic2video.py --export 输出.mp4 --bili BV13qs46FE9C --total 4 图1.jpg 图2.png
python pic2video.py --export 竖屏.mp4 --bili BV... --ratio 9:16 --fps 60 --fit cover --transition fade 图1.jpg 图2.png
```

`--total` 是每张图的秒数；`--fps` / `--ratio` / `--width` / `--height` / `--fit` / `--bg` /
`--pad-color` / `--no-bg-blur` / `--transition` 同设置页；命令行不写 `settings.json`。

## 设置

![设置（深色）](screenshots/settings-dark.png)

| 项 | 说明 |
| --- | --- |
| 界面语言 / 界面主题 / 主色 | 五种语言；跟随系统 / 浅色 / 深色；主色点开是色环，对撞色自动按 180° 算 |
| 默认导出目录 | 留空＝每张图所在目录；每次导出还能在保存框里改 |
| 画面比例 / 分辨率 / 帧率 / 画质 | 9 档比例只当预设，宽高都能手填；30/60fps；高 / 标准 / 小体积 |
| 画面铺底 / 留白颜色 / 背景文件 / 背景处理 | 五种铺底；纯色可自选；背景图/视频可模糊或不模糊 |
| 默认节奏 / 逐张默认秒数 | 跟 BGM、自设总长、逐张秒数 |
| 音乐淡入淡出 / 图片转场 | 秒数可调；转场支持硬切与淡入淡出 |
| 抖音 / YouTube 支持 | yt-dlp 的安装状态，一键下载 / 删除 |

预览音频始终跟随 **Windows 默认输出设备**，插拔耳机自动切，不用重启。

## 项目结构

```
pic2video.py     主程序（窗口 + 命令行）
bili_audio.py    B 站取音轨（WBI 签名 + playurl）
audio_source.py  音源判定与 yt-dlp 封装
theme.py         主题、主色/对撞色、QSS
i18n.py          五种语言的词条
启动.bat          双击开窗口
tests/           单元检查（95 条）
docs/            研究笔记、设计记录（wayfinder 票）与原始探针脚本
screenshots/     README 里的截图
```

## 开发

```
python tests/test_project.py     # 时间线 / 帧数 / 设置 / filtergraph / 主题 / 音源
python tests/scan_keys.py        # 检查 tr() 的中文都有四种译文
```

## 已知限制

- B 站付费/会员试看视频只能拿到试看片段；地区/版权限制的会直接失败并给原因。
- 抖音 / YouTube 等平台如果要求登录（年龄限制、会员内容），yt-dlp 会失败并显示原因。
- 出片时间约为片长的 1/5；很长的音乐建议用「逐张秒数」控总长。
- 预览是 540p 的样片，画质不代表成片。
- 目前只在 Windows 上验证过。

## 许可

个人自用工具，暂未附许可证文件。
