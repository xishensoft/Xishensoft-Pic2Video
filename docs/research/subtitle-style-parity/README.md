# 字幕样式：libass ↔ QPainter 像素对齐实测

这目录是 `docs/字幕改造方案.md` §5 的证据：同一份样式字典，一边交给 ffmpeg 的 libass 烧录，一边用 PySide6 的 QPainter 画，然后逐像素比。

## 怎么跑

```powershell
python docs/research/subtitle-style-parity/parity_probe.py     # 三用例主脚本，写 metrics.json 与全部图片
python docs/research/subtitle-style-parity/calib.py            # 字号标定：libass Fontsize vs Qt setPixelSize
python docs/research/subtitle-style-parity/box_probe.py        # BorderStyle 3/4 的填充色
python docs/research/subtitle-style-parity/box_probe2.py       # BorderStyle 4 的框尺寸规则
python docs/research/subtitle-style-parity/parity_product.py   # 走产品真代码路径（format_ass/ass_filter vs paint_cues）
python docs/research/subtitle-style-parity/export_probe.py      # 另存的 .ass 拖回 ffmpeg 能不能烧出样式
```

`parity_probe.py` 是自己拼 ASS 的探针，用来标定常数；`parity_product.py` 走产品代码：左边 `format_ass()` → `ass_filter()` → ffmpeg 烧，右边 `paint_cues()` 画同一帧，1080p 与 540p 各跑一遍。改完渲染相关代码就重跑后者。

依赖：本机 `ffmpeg`（带 libass）、`PySide6`、`C:\Windows\Fonts\msyh.ttc`。脚本用 `QT_QPA_PLATFORM=offscreen`，并且**必须** `QFontDatabase.addApplicationFont()` 显式加载字体，否则 offscreen 下的 Qt 会画出缺字方块。

## 本机实测结果（1920×1080，ffmpeg 9.0.2-full_build，PySide6 6.12.0）

| 用例 | 掩膜 IoU | 包围盒偏差 |
| --- | --- | --- |
| `bottom`（底部居中两行，描边 3px） | 0.9353 | (-1, 0, -1, -1) px |
| `mid_right`（右中单行，描边 2px） | 0.8764 | (-2, 0, 0, 0) px |
| `topleft_box`（左上粗体 + 蓝底框） | 0.9941 | (1, 0, -1, 0) px |

## 三条结论（方案里要用的常数）

1. `line_factor = (ascent + descent) / em ≈ 1.32`（msyh）。libass 的 `Fontsize` 是字体自身行高，不是 em；生成 ASS 时要乘它，Qt 才不用乘。
2. Qt 侧 `QPen` 宽度 = `2 × outline`（libass 描边外扩，QPen 以路径为中轴）。
3. 底色框必须 `BorderStyle=4`；`BorderStyle=3` 画的是描边色框，且 `Outline=0` 时框消失。
4. `Fontsize` 只能写整数，取整零头用 `\fscx/\fscy` 补（540p 下不补会整体偏大 1%、居中字幕右移 3 px）。
5. 预览侧要 `QFont.HintingPreference.PreferNoHinting`：libass 不做 hinting，Qt 做了会把小字号中文笔画挤细。

产品路径复测（`parity_product.py`）：1080p IoU 0.9303 / 0.9609 / 0.9695，540p 0.8693 / 0.9337 / 0.9080，包围盒偏差全部 ≤2 px。

图片说明：`ff_*.png` 为 libass 真渲、`qt_*.png` 为 QPainter、`diff_*.png` 灰=重合/红=只 libass/绿=只 Qt、`compare_*.png` 三联图、`zoom_*.png` 1:1 左右并排。
## 另存 .ass 的复烧（阶段 4）

`export_probe.py` 把 `subtitle_export_texts()` 生成的 `.ass` 写进临时文件，再用 `ass_filter()` 交给 ffmpeg 烧两帧：

- `export_ass_burn.png`：第一条（默认样式，白字黑描边、底部居中）
- `export_ass_burn_styled.png`：第二条（`\pos` 覆盖到别处、72 px、金色字、蓝色实心底框）

两张都正常出图，说明另存的 `.ass` 自带样式表与逐条覆盖，不依赖 `force_style`；滤镜串里也确实搜不到 `force_style`。