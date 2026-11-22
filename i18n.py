"""i18n —— 界面文案的多语言表（中文原文当 key）。

约定：
- 简体中文就是「原文」，zh-CN 不另存表，查不到译文就用 key 本身；
- 其它四种语言各一张 {中文原文: 译文} 表，缺词条回落中文，不会崩；
- 只有**看得见的文案**走这里。内部值（quality 的「标准」、ratio 的「自定义」、
  命令行参数等）保持中文不动，免得老 settings.json 和脚本跟着变。

用法：
    from i18n import tr, set_locale
    set_locale("auto")      # 或 "en" / "ja" / "ko" / "zh-TW" / "zh-CN"
    print(tr("加图片"))
    print(tr("已加 {n} 张图", n=3))
"""

LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko")
LOCALE_LABELS = {
    "zh-CN": "简体中文",
    "zh-TW": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
}
SYSTEM = "auto"   # settings["lang"] 的「跟随系统」
DEFAULT = "zh-CN"

# B 站错误码 → 中文提示（和 bili_audio.ERROR_MSG 对齐，这里用来查译文）
BILI_KEYS = {
    -404: "视频不存在或已被删除",
    62002: "稿件不可见（已删除/仅自己可见/审核中）",
    62004: "稿件审核中",
    -403: "没有访问权限（可能是付费/充电专属内容）",
    -400: "请求参数有误，链接可能不是有效的视频地址",
    -352: "请求被 B 站风控拦截，请稍后重试或降低频率",
    -412: "请求被风控拦截（IP 层面），请稍后再试",
    -688: "该内容有地区限制，当前网络无法观看",
    -689: "该内容有版权限制，无法观看",
    87007: "充电专属视频，需要给 UP 主充电后才能观看",
    87008: "付费视频，需要购买后才能观看",
    -10403: "该内容在当前地区不可观看",
    "preview": "这是会员/付费内容的试看片段，完整音轨需要大会员账号",
    "live": "直播流没有点播音轨，本工具只支持点播视频/番剧",
    "cheese": "付费课程需要登录且已购买，本工具暂不支持",
    "network": "网络请求失败，请检查网络或稍后重试",
    "ffmpeg": "ffmpeg 调用失败",
    "badjson": "B 站返回了非 JSON 内容（可能被风控或需要验证）",
}

_current = DEFAULT


def locale_from_langid(lid: int) -> str:
    """Windows LANGID（如 0x0804）→ 我们支持的五种之一。"""
    primary, sub = lid & 0x03FF, (lid >> 10) & 0x3F
    if primary == 0x04:                       # zh
        # 0x01 繁体(台湾) 0x03 繁体(澳门/香港) 0x05 繁体(其他) → 繁體
        return "zh-TW" if sub in (1, 3, 5) else "zh-CN"
    return {0x11: "ja", 0x12: "ko"}.get(primary, "en")


def system_locale() -> str:
    """Windows 界面语言 → 我们支持的五种之一（拿不到就给简体中文）。"""
    try:
        import ctypes
        lid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:  # noqa: BLE001  非 Windows / 受限环境
        return DEFAULT
    return locale_from_langid(lid)


def set_locale(lang) -> str:
    """把当前界面语言设成 lang（"auto" = 跟随系统），返回实际生效的语言。"""
    global _current
    code = system_locale() if lang in (None, "", SYSTEM) else str(lang)
    if code not in LOCALES:
        code = DEFAULT
    _current = code
    return code


def current_locale() -> str:
    return _current


def language_label(lang) -> str:
    """设置里「界面语言」下拉的一项文字。"""
    if lang in (None, "", SYSTEM):
        return tr("跟随系统") + "（" + LOCALE_LABELS[system_locale()] + "）"
    return LOCALE_LABELS.get(lang, LOCALE_LABELS[DEFAULT])


def tr(text: str, **kw) -> str:
    """取译文；zh-CN 或缺词条时返回原文。kw 走 str.format。"""
    table = TR.get(_current) or {}
    out = table.get(text) or text
    if kw:
        try:
            out = out.format(**kw)
        except (KeyError, IndexError, ValueError):
            pass
    return out


def bili_message(err) -> str:
    """BiliError → 当前语言的用户可读提示（拿不到码就原样返回）。"""
    code = getattr(err, "code", None)
    if code is not None and code in BILI_KEYS:
        return tr(BILI_KEYS[code])
    return getattr(err, "user_message", None) or str(err)


# ---------------------------------------------------------------- 词条表

TR = {
    "zh-TW": {
        "{w} x {h}（{ratio}）": "{w} x {h}（{ratio}）",
        "取消": "取消",
        "原图铺底": "原圖鋪底",
        "纯色留白": "純色留白",
        "自定义背景图": "自訂背景圖",
        "自定义背景视频": "自訂背景影片",
        "裁切铺满": "裁切填滿",
        "自定义": "自訂",
        "高": "高",
        "标准": "標準",
        "小体积": "小檔案",
        "跟 BGM": "跟 BGM",
        "自设总长": "自訂總長",
        "逐张秒数": "逐張秒數",
        " 秒": " 秒",
        "未检测到输出设备": "未偵測到輸出裝置",
        "（跟随 Windows 默认，换设备自动跟）": "（跟隨 Windows 預設，換裝置自動跟）",
        "设置": "設定",
        "留空 = 每张图所在目录（每次导出还会弹框确认）": "留空 = 每張圖所在資料夾（每次匯出還會彈窗確認）",
        "浏览…": "瀏覽…",
        "默认导出目录": "預設匯出資料夾",
        "界面语言": "介面語言",
        "跟随系统": "跟隨系統",
        "简洁模式": "簡潔模式",
        "专业模式": "專業模式",
        "点一下切换：简洁模式只留必要设置，专业模式显示全部选项": "點一下切換：簡潔模式只留必要設定，專業模式顯示全部選項",
        "画面比例": "畫面比例",
        "（选比例会自动填，也可手改）": "（選比例會自動填，也可手改）",
        "分辨率（宽×高）": "解析度（寬×高）",
        "成片尺寸": "成品尺寸",
        "帧率": "影格率",
        "画质": "畫質",
        "画面铺底": "畫面鋪底",
        "纯色留白用的底色": "純色留白用的底色",
        "留白颜色": "留白顏色",
        "背景图片或视频（「自定义背景」才用）": "背景圖片或影片（「自訂背景」才用）",
        "背景文件": "背景檔案",
        "背景模糊（原图铺底与自定义背景都适用）": "背景模糊（原圖鋪底與自訂背景都適用）",
        "背景处理": "背景處理",
        "默认节奏": "預設節奏",
        "逐张模式默认": "逐張模式預設",
        "音乐淡入": "音樂淡入",
        "音乐淡出": "音樂淡出",
        "硬切": "硬切",
        "淡入淡出（每张首尾淡黑）": "淡入淡出（每張首尾淡黑）",
        "图片转场": "圖片轉場",
        "转场时长": "轉場時長",
        "预览音频输出": "預覽音訊輸出",
        "选留白颜色": "選留白顏色",
        "选背景视频": "選背景影片",
        "选背景图片": "選背景圖片",
        "视频 (*.mp4 *.mov *.mkv *.webm)": "影片 (*.mp4 *.mov *.mkv *.webm)",
        "图片 (*.jpg *.jpeg *.png *.bmp *.webp)": "圖片 (*.jpg *.jpeg *.png *.bmp *.webp)",
        "视频 (*.mp4)": "影片 (*.mp4)",
        "选默认导出目录": "選預設匯出資料夾",
        "图片出片 —— 图片 + B站 BGM → 视频": "圖片出片 —— 圖片 + B站 BGM → 影片",
        "加图片": "加圖片",
        "上移": "上移",
        "下移": "下移",
        "删除": "刪除",
        "导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏": "匯出目錄、畫幅比例、解析度、影格率、畫質、鋪底、轉場、預設節奏",
        "刷新预览": "重新整理預覽",
        "刷新预览 ●": "重新整理預覽 ●",
        "导出视频": "匯出影片",
        "预览：改完参数点右上角「刷新预览」": "預覽：改完參數點右上角「重新整理預覽」",
        "B 站链接": "B 站連結",
        "粘贴视频链接、BV 号或 b23.tv 短链，自动取整条音轨当背景音乐": "貼上影片連結、BV 號或 b23.tv 短連結，自動取整條音軌當背景音樂",
        "重试": "重試",
        "拖图片进来开始": "拖圖片進來開始",
        "参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果": "參數已更新：{w}x{h} {fps}fps，點「重新整理預覽」看效果",
        "设置已保存": "設定已儲存",
        "音频：{name}": "音訊：{name}",
        "选图片": "選圖片",
        "{name} 读不了，已跳过": "{name} 讀不了，已略過",
        "已加 {n} 张图": "已加 {n} 張圖",
        "这张图显示多久": "這張圖顯示多久",
        "第 {row} 张（秒）：": "第 {row} 張（秒）：",
        "{n} 张图 · 总长 {total} · {src}": "{n} 張圖 · 總長 {total} · {src}",
        "{n} 张图 · 总长 {total}": "{n} 張圖 · 總長 {total}",
        "无音乐": "無音樂",
        "{i}.  {dur} 秒   {name}": "{i}.  {dur} 秒   {name}",
        "先加图片": "先加圖片",
        "生成预览…": "產生預覽…",
        "预览已更新（点画面播放/暂停，点色带跳转）": "預覽已更新（點畫面播放/暫停，點色帶跳轉）",
        "预览：{name}": "預覽：{name}",
        "预览失败": "預覽失敗",
        "正在从 B 站取音轨…": "正在從 B 站取音軌…",
        "BGM 已就绪：{total} · {name}": "BGM 已就緒：{total} · {name}",
        "取音轨失败": "取音軌失敗",
        "导出到…": "匯出到…",
        "正在导出…": "正在匯出…",
        "导出完成：{path}": "匯出完成：{path}",
        "导出完成": "匯出完成",
        "视频已生成：\n{path}": "影片已產生：\n{path}",
        "打开所在文件夹": "開啟所在資料夾",
        "好": "好",
        "导出失败": "匯出失敗",
        "把图片拖进来，或点「加图片」": "把圖片拖進來，或點「加圖片」",
        "没有图片": "沒有圖片",
        "选的背景文件不存在，去设置里重新选一个": "選的背景檔案不存在，去設定裡重新選一個",
        "背景图格式不支持，用 jpg / png / webp / bmp": "背景圖格式不支援，用 jpg / png / webp / bmp",
        "ffmpeg 退出码 {rc}": "ffmpeg 結束碼 {rc}",
        "图片 + BGM 合成视频（无窗口）": "圖片 + BGM 合成影片（無視窗）",
        "输出 mp4 路径": "輸出 mp4 路徑",
        "本地音频文件": "本機音訊檔案",
        "bilibili 链接或 BV 号": "bilibili 連結或 BV 號",
        "每张图片的秒数（默认均分音乐）": "每張圖片的秒數（預設均分音樂）",
        "保留取下来的音频文件": "保留取下來的音訊檔案",
        "帧率：30 或 60": "影格率：30 或 60",
        "铺底：own 原图 / color 纯色 / image 背景图 / video 背景视频 / cover 裁切": "鋪底：own 原圖 / color 純色 / image 背景圖 / video 背景影片 / cover 裁切",
        "画面比例（默认按短边 1080 算尺寸）": "畫面比例（預設按短邊 1080 算尺寸）",
        "成片宽度（不给就按比例）": "成品寬度（不給就按比例）",
        "成片高度（不给就按比例）": "成品高度（不給就按比例）",
        "自定义背景图片或视频": "自訂背景圖片或影片",
        "纯色留白的颜色，如 #ffffff": "純色留白的顏色，如 #ffffff",
        "背景不模糊": "背景不模糊",
        "图片转场：none 硬切 / fade 淡入淡出": "圖片轉場：none 硬切 / fade 淡入淡出",
        "[1/3] 取音轨：{url}": "[1/3] 取音軌：{url}",
        "          失败：{msg}": "          失敗：{msg}",
        "[2/3] {n} 张图，总长 {total}s，{durs}": "[2/3] {n} 張圖，總長 {total}s，{durs}",
        "[3/3] 渲染 {w}x{h} {fps}fps {fit}…": "[3/3] 算圖 {w}x{h} {fps}fps {fit}…",
        "完成：{path}  ({secs}s, {mb} MB)": "完成：{path}  ({secs}s, {mb} MB)",
        "视频不存在或已被删除": "影片不存在或已被刪除",
        "稿件不可见（已删除/仅自己可见/审核中）": "稿件不可見（已刪除/僅自己可見/審核中）",
        "稿件审核中": "稿件審核中",
        "没有访问权限（可能是付费/充电专属内容）": "沒有存取權限（可能是付費/充電專屬內容）",
        "请求参数有误，链接可能不是有效的视频地址": "請求參數有誤，連結可能不是有效的影片位址",
        "请求被 B 站风控拦截，请稍后重试或降低频率": "請求被 B 站風控攔截，請稍後重試或降低頻率",
        "请求被风控拦截（IP 层面），请稍后再试": "請求被風控攔截（IP 層面），請稍後再試",
        "该内容有地区限制，当前网络无法观看": "該內容有地區限制，目前網路無法觀看",
        "该内容有版权限制，无法观看": "該內容有版權限制，無法觀看",
        "充电专属视频，需要给 UP 主充电后才能观看": "充電專屬影片，需要給 UP 主充電後才能觀看",
        "付费视频，需要购买后才能观看": "付費影片，需要購買後才能觀看",
        "该内容在当前地区不可观看": "該內容在目前地區不可觀看",
        "这是会员/付费内容的试看片段，完整音轨需要大会员账号": "這是會員/付費內容的試看片段，完整音軌需要大會員帳號",
        "直播流没有点播音轨，本工具只支持点播视频/番剧": "直播流沒有點播音軌，本工具只支援點播影片/番劇",
        "付费课程需要登录且已购买，本工具暂不支持": "付費課程需要登入且已購買，本工具暫不支援",
        "网络请求失败，请检查网络或稍后重试": "網路請求失敗，請檢查網路或稍後重試",
        "ffmpeg 调用失败": "ffmpeg 呼叫失敗",
        "B 站返回了非 JSON 内容（可能被风控或需要验证）": "B 站回傳了非 JSON 內容（可能被風控或需要驗證）",
    },
    "en": {
        "{w} x {h}（{ratio}）": "{w} x {h} ({ratio})",
        "取消": "Cancel",
        "原图铺底": "Own image backdrop",
        "纯色留白": "Solid color pad",
        "自定义背景图": "Custom image backdrop",
        "自定义背景视频": "Custom video backdrop",
        "裁切铺满": "Crop to fill",
        "自定义": "Custom",
        "高": "High",
        "标准": "Standard",
        "小体积": "Small file",
        "跟 BGM": "Fit BGM",
        "自设总长": "Set total",
        "逐张秒数": "Per image",
        " 秒": " s",
        "未检测到输出设备": "No output device",
        "（跟随 Windows 默认，换设备自动跟）": " (follows the Windows default, switches automatically)",
        "设置": "Settings",
        "留空 = 每张图所在目录（每次导出还会弹框确认）": "Empty = folder of the images (you confirm each export)",
        "浏览…": "Browse…",
        "默认导出目录": "Default export folder",
        "界面语言": "Language",
        "跟随系统": "Follow system",
        "简洁模式": "Simple mode",
        "专业模式": "Pro mode",
        "点一下切换：简洁模式只留必要设置，专业模式显示全部选项": "Click to switch: Simple keeps only the essentials, Pro shows everything",
        "画面比例": "Aspect ratio",
        "（选比例会自动填，也可手改）": "(picking a ratio fills these; you can still edit)",
        "分辨率（宽×高）": "Resolution (W×H)",
        "成片尺寸": "Output size",
        "帧率": "Frame rate",
        "画质": "Quality",
        "画面铺底": "Backdrop",
        "纯色留白用的底色": "Color used for the solid backdrop",
        "留白颜色": "Pad color",
        "背景图片或视频（「自定义背景」才用）": "Image or video (custom backdrops only)",
        "背景文件": "Background file",
        "背景模糊（原图铺底与自定义背景都适用）": "Blur the backdrop (own image and custom backdrops)",
        "背景处理": "Background",
        "默认节奏": "Default timing",
        "逐张模式默认": "Per-image default",
        "音乐淡入": "Music fade-in",
        "音乐淡出": "Music fade-out",
        "硬切": "Hard cut",
        "淡入淡出（每张首尾淡黑）": "Fade (black at each edge)",
        "图片转场": "Transition",
        "转场时长": "Transition length",
        "预览音频输出": "Preview audio output",
        "选留白颜色": "Pick pad color",
        "选背景视频": "Pick background video",
        "选背景图片": "Pick background image",
        "视频 (*.mp4 *.mov *.mkv *.webm)": "Video (*.mp4 *.mov *.mkv *.webm)",
        "图片 (*.jpg *.jpeg *.png *.bmp *.webp)": "Images (*.jpg *.jpeg *.png *.bmp *.webp)",
        "视频 (*.mp4)": "Video (*.mp4)",
        "选默认导出目录": "Pick default export folder",
        "图片出片 —— 图片 + B站 BGM → 视频": "Pic2Video — images + Bilibili BGM → video",
        "加图片": "Add images",
        "上移": "Up",
        "下移": "Down",
        "删除": "Remove",
        "导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏": "Export folder, aspect ratio, resolution, frame rate, quality, backdrop, transition, timing",
        "刷新预览": "Refresh preview",
        "刷新预览 ●": "Refresh preview ●",
        "导出视频": "Export video",
        "预览：改完参数点右上角「刷新预览」": "Preview: change settings, then hit “Refresh preview” top right",
        "B 站链接": "Bilibili link",
        "粘贴视频链接、BV 号或 b23.tv 短链，自动取整条音轨当背景音乐": "Paste a video link, BV id or b23.tv short link — the whole audio track becomes the BGM",
        "重试": "Retry",
        "拖图片进来开始": "Drop images here to start",
        "参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果": "Updated: {w}x{h} {fps}fps — hit “Refresh preview” to see it",
        "设置已保存": "Settings saved",
        "音频：{name}": "Audio: {name}",
        "选图片": "Pick images",
        "{name} 读不了，已跳过": "{name} can't be read, skipped",
        "已加 {n} 张图": "Added {n} image(s)",
        "这张图显示多久": "How long this image shows",
        "第 {row} 张（秒）：": "Image {row} (seconds):",
        "{n} 张图 · 总长 {total} · {src}": "{n} images · {total} · {src}",
        "{n} 张图 · 总长 {total}": "{n} images · {total}",
        "无音乐": "no music",
        "{i}.  {dur} 秒   {name}": "{i}.  {dur} s   {name}",
        "先加图片": "Add images first",
        "生成预览…": "Building preview…",
        "预览已更新（点画面播放/暂停，点色带跳转）": "Preview updated (click the video to play/pause, click the bar to seek)",
        "预览：{name}": "Preview: {name}",
        "预览失败": "Preview failed",
        "正在从 B 站取音轨…": "Fetching the audio track from Bilibili…",
        "BGM 已就绪：{total} · {name}": "BGM ready: {total} · {name}",
        "取音轨失败": "Audio fetch failed",
        "导出到…": "Export to…",
        "正在导出…": "Exporting…",
        "导出完成：{path}": "Exported: {path}",
        "导出完成": "Export finished",
        "视频已生成：\n{path}": "Video created:\n{path}",
        "打开所在文件夹": "Open folder",
        "好": "OK",
        "导出失败": "Export failed",
        "把图片拖进来，或点「加图片」": "Drop images here, or click “Add images”",
        "没有图片": "No images",
        "选的背景文件不存在，去设置里重新选一个": "The chosen background file doesn't exist — pick it again in Settings",
        "背景图格式不支持，用 jpg / png / webp / bmp": "Unsupported background image format — use jpg / png / webp / bmp",
        "ffmpeg 退出码 {rc}": "ffmpeg exited with code {rc}",
        "图片 + BGM 合成视频（无窗口）": "Images + BGM to video (no GUI)",
        "输出 mp4 路径": "output mp4 path",
        "本地音频文件": "local audio file",
        "bilibili 链接或 BV 号": "bilibili link or BV id",
        "每张图片的秒数（默认均分音乐）": "seconds per image (default: split the music evenly)",
        "保留取下来的音频文件": "keep the downloaded audio file",
        "帧率：30 或 60": "frame rate: 30 or 60",
        "铺底：own 原图 / color 纯色 / image 背景图 / video 背景视频 / cover 裁切": "backdrop: own / color / image / video / cover",
        "画面比例（默认按短边 1080 算尺寸）": "aspect ratio (size from a 1080 short side)",
        "成片宽度（不给就按比例）": "output width (default: from the ratio)",
        "成片高度（不给就按比例）": "output height (default: from the ratio)",
        "自定义背景图片或视频": "custom background image or video",
        "纯色留白的颜色，如 #ffffff": "pad color, e.g. #ffffff",
        "背景不模糊": "do not blur the backdrop",
        "图片转场：none 硬切 / fade 淡入淡出": "transition: none (hard cut) / fade",
        "[1/3] 取音轨：{url}": "[1/3] fetching audio: {url}",
        "          失败：{msg}": "          failed: {msg}",
        "[2/3] {n} 张图，总长 {total}s，{durs}": "[2/3] {n} images, total {total}s, {durs}",
        "[3/3] 渲染 {w}x{h} {fps}fps {fit}…": "[3/3] rendering {w}x{h} {fps}fps {fit}…",
        "完成：{path}  ({secs}s, {mb} MB)": "Done: {path}  ({secs}s, {mb} MB)",
        "视频不存在或已被删除": "Video not found or deleted",
        "稿件不可见（已删除/仅自己可见/审核中）": "Video unavailable (deleted / private / under review)",
        "稿件审核中": "Video is still under review",
        "没有访问权限（可能是付费/充电专属内容）": "No access (paid or member-only content)",
        "请求参数有误，链接可能不是有效的视频地址": "Bad request — the link may not be a valid video URL",
        "请求被 B 站风控拦截，请稍后重试或降低频率": "Blocked by Bilibili's rate limiting — retry later",
        "请求被风控拦截（IP 层面），请稍后再试": "Blocked by rate limiting (IP level) — try again later",
        "该内容有地区限制，当前网络无法观看": "Region restricted — not available from here",
        "该内容有版权限制，无法观看": "Copyright restricted — cannot be watched",
        "充电专属视频，需要给 UP 主充电后才能观看": "Sponsor-only video (bilibili charging)",
        "付费视频，需要购买后才能观看": "Paid video — must be purchased first",
        "该内容在当前地区不可观看": "Not available in this region",
        "这是会员/付费内容的试看片段，完整音轨需要大会员账号": "This is a preview clip of member/paid content; the full track needs a premium account",
        "直播流没有点播音轨，本工具只支持点播视频/番剧": "Livestreams have no on-demand audio track; only VOD videos/anime are supported",
        "付费课程需要登录且已购买，本工具暂不支持": "Paid courses need a logged-in purchase; not supported",
        "网络请求失败，请检查网络或稍后重试": "Network request failed — check your connection",
        "ffmpeg 调用失败": "ffmpeg call failed",
        "B 站返回了非 JSON 内容（可能被风控或需要验证）": "Bilibili returned non-JSON content (rate limiting or verification required)",
    },
    "ja": {
        "{w} x {h}（{ratio}）": "{w} x {h}（{ratio}）",
        "取消": "キャンセル",
        "原图铺底": "元画像を背景に",
        "纯色留白": "単色の余白",
        "自定义背景图": "カスタム背景画像",
        "自定义背景视频": "カスタム背景動画",
        "裁切铺满": "切り抜いて全面",
        "自定义": "カスタム",
        "高": "高画質",
        "标准": "標準",
        "小体积": "小さめ",
        "跟 BGM": "BGM に合わせる",
        "自设总长": "合計時間を指定",
        "逐张秒数": "1枚ずつ秒数",
        " 秒": " 秒",
        "未检测到输出设备": "出力デバイスがありません",
        "（跟随 Windows 默认，换设备自动跟）": "（Windows の既定に従う / 切り替えも自動）",
        "设置": "設定",
        "留空 = 每张图所在目录（每次导出还会弹框确认）": "空 = 各画像のフォルダー（書き出し時に毎回確認）",
        "浏览…": "参照…",
        "默认导出目录": "既定の書き出し先",
        "界面语言": "表示言語",
        "跟随系统": "システムに従う",
        "简洁模式": "シンプルモード",
        "专业模式": "プロモード",
        "点一下切换：简洁模式只留必要设置，专业模式显示全部选项": "クリックで切り替え：シンプルは必要最小限、プロは全項目",
        "画面比例": "アスペクト比",
        "（选比例会自动填，也可手改）": "（比率を選ぶと自動入力、手動変更も可）",
        "分辨率（宽×高）": "解像度（幅×高）",
        "成片尺寸": "出力サイズ",
        "帧率": "フレームレート",
        "画质": "画質",
        "画面铺底": "背景の敷き方",
        "纯色留白用的底色": "単色の余白に使う色",
        "留白颜色": "余白の色",
        "背景图片或视频（「自定义背景」才用）": "背景画像または動画（カスタム背景のみ）",
        "背景文件": "背景ファイル",
        "背景模糊（原图铺底与自定义背景都适用）": "背景をぼかす（元画像・カスタム背景の両方）",
        "背景处理": "背景処理",
        "默认节奏": "既定のテンポ",
        "逐张模式默认": "1枚ずつの既定秒数",
        "音乐淡入": "音楽フェードイン",
        "音乐淡出": "音楽フェードアウト",
        "硬切": "ハードカット",
        "淡入淡出（每张首尾淡黑）": "フェード（各画像の前後を黒へ）",
        "图片转场": "画像の切り替え",
        "转场时长": "切り替え時間",
        "预览音频输出": "プレビューの音声出力",
        "选留白颜色": "余白の色を選ぶ",
        "选背景视频": "背景動画を選ぶ",
        "选背景图片": "背景画像を選ぶ",
        "视频 (*.mp4 *.mov *.mkv *.webm)": "動画 (*.mp4 *.mov *.mkv *.webm)",
        "图片 (*.jpg *.jpeg *.png *.bmp *.webp)": "画像 (*.jpg *.jpeg *.png *.bmp *.webp)",
        "视频 (*.mp4)": "動画 (*.mp4)",
        "选默认导出目录": "既定の書き出し先を選ぶ",
        "图片出片 —— 图片 + B站 BGM → 视频": "画像から動画 — 画像 + Bilibili BGM → 動画",
        "加图片": "画像を追加",
        "上移": "上へ",
        "下移": "下へ",
        "删除": "削除",
        "导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏": "書き出し先、アスペクト比、解像度、フレームレート、画質、背景、切り替え、既定のテンポ",
        "刷新预览": "プレビュー更新",
        "刷新预览 ●": "プレビュー更新 ●",
        "导出视频": "動画を書き出す",
        "预览：改完参数点右上角「刷新预览」": "プレビュー：設定を変えたら右上の「プレビュー更新」を押す",
        "B 站链接": "Bilibili リンク",
        "粘贴视频链接、BV 号或 b23.tv 短链，自动取整条音轨当背景音乐": "動画リンク・BV 番号・b23.tv 短縮リンクを貼ると、音声トラック全体を BGM にします",
        "重试": "再試行",
        "拖图片进来开始": "画像をドラッグして開始",
        "参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果": "更新しました：{w}x{h} {fps}fps。「プレビュー更新」で確認",
        "设置已保存": "設定を保存しました",
        "音频：{name}": "音声：{name}",
        "选图片": "画像を選ぶ",
        "{name} 读不了，已跳过": "{name} を読み込めないためスキップ",
        "已加 {n} 张图": "{n} 枚の画像を追加しました",
        "这张图显示多久": "この画像の表示時間",
        "第 {row} 张（秒）：": "{row} 枚目（秒）：",
        "{n} 张图 · 总长 {total} · {src}": "{n} 枚 · 合計 {total} · {src}",
        "{n} 张图 · 总长 {total}": "{n} 枚 · 合計 {total}",
        "无音乐": "音楽なし",
        "{i}.  {dur} 秒   {name}": "{i}.  {dur} 秒   {name}",
        "先加图片": "先に画像を追加",
        "生成预览…": "プレビューを生成中…",
        "预览已更新（点画面播放/暂停，点色带跳转）": "プレビュー更新済み（映像クリックで再生/一時停止、バーで移動）",
        "预览：{name}": "プレビュー：{name}",
        "预览失败": "プレビューに失敗",
        "正在从 B 站取音轨…": "Bilibili から音声を取得中…",
        "BGM 已就绪：{total} · {name}": "BGM 準備完了：{total} · {name}",
        "取音轨失败": "音声の取得に失敗",
        "导出到…": "書き出し先…",
        "正在导出…": "書き出し中…",
        "导出完成：{path}": "書き出し完了：{path}",
        "导出完成": "書き出し完了",
        "视频已生成：\n{path}": "動画を作成しました：\n{path}",
        "打开所在文件夹": "フォルダーを開く",
        "好": "OK",
        "导出失败": "書き出しに失敗",
        "把图片拖进来，或点「加图片」": "画像をドラッグするか「画像を追加」を押す",
        "没有图片": "画像がありません",
        "选的背景文件不存在，去设置里重新选一个": "選んだ背景ファイルが見つかりません。設定で選び直してください",
        "背景图格式不支持，用 jpg / png / webp / bmp": "背景画像の形式に対応していません（jpg / png / webp / bmp）",
        "ffmpeg 退出码 {rc}": "ffmpeg 終了コード {rc}",
        "图片 + BGM 合成视频（无窗口）": "画像 + BGM から動画を作る（ウィンドウなし）",
        "输出 mp4 路径": "出力 mp4 パス",
        "本地音频文件": "ローカル音声ファイル",
        "bilibili 链接或 BV 号": "bilibili リンクまたは BV 番号",
        "每张图片的秒数（默认均分音乐）": "1 枚あたりの秒数（既定は音楽を均等割り）",
        "保留取下来的音频文件": "取得した音声ファイルを残す",
        "帧率：30 或 60": "フレームレート：30 または 60",
        "铺底：own 原图 / color 纯色 / image 背景图 / video 背景视频 / cover 裁切": "背景：own 元画像 / color 単色 / image 背景画像 / video 背景動画 / cover 切り抜き",
        "画面比例（默认按短边 1080 算尺寸）": "アスペクト比（既定は短辺 1080 で算出）",
        "成片宽度（不给就按比例）": "出力幅（省略時は比率から）",
        "成片高度（不给就按比例）": "出力高さ（省略時は比率から）",
        "自定义背景图片或视频": "カスタム背景画像または動画",
        "纯色留白的颜色，如 #ffffff": "単色余白の色（例 #ffffff）",
        "背景不模糊": "背景をぼかさない",
        "图片转场：none 硬切 / fade 淡入淡出": "画像の切り替え：none ハードカット / fade フェード",
        "[1/3] 取音轨：{url}": "[1/3] 音声を取得：{url}",
        "          失败：{msg}": "          失敗：{msg}",
        "[2/3] {n} 张图，总长 {total}s，{durs}": "[2/3] {n} 枚、合計 {total}s、{durs}",
        "[3/3] 渲染 {w}x{h} {fps}fps {fit}…": "[3/3] 書き出し {w}x{h} {fps}fps {fit}…",
        "完成：{path}  ({secs}s, {mb} MB)": "完了：{path}  ({secs}s, {mb} MB)",
        "视频不存在或已被删除": "動画が存在しないか削除されています",
        "稿件不可见（已删除/仅自己可见/审核中）": "動画を表示できません（削除・非公開・審査中）",
        "稿件审核中": "動画は審査中です",
        "没有访问权限（可能是付费/充电专属内容）": "アクセス権がありません（有料・メンバー限定の可能性）",
        "请求参数有误，链接可能不是有效的视频地址": "リクエストが不正です。有効な動画リンクではない可能性",
        "请求被 B 站风控拦截，请稍后重试或降低频率": "Bilibili のレート制限で遮断されました。時間をおいて再試行",
        "请求被风控拦截（IP 层面），请稍后再试": "レート制限で遮断されました（IP 単位）。時間をおいて再試行",
        "该内容有地区限制，当前网络无法观看": "地域制限があります。この回線では視聴できません",
        "该内容有版权限制，无法观看": "著作権の制限があり視聴できません",
        "充电专属视频，需要给 UP 主充电后才能观看": "支援者限定動画です（Bilibili の投げ銭が必要）",
        "付费视频，需要购买后才能观看": "有料動画です。購入が必要です",
        "该内容在当前地区不可观看": "この地域では視聴できません",
        "这是会员/付费内容的试看片段，完整音轨需要大会员账号": "会員・有料コンテンツの試聴用クリップです。全音声にはプレミアム会員が必要",
        "直播流没有点播音轨，本工具只支持点播视频/番剧": "ライブ配信にはオンデマンド音声がありません。VOD の動画/アニメのみ対応",
        "付费课程需要登录且已购买，本工具暂不支持": "有料講座はログインと購入が必要です。未対応",
        "网络请求失败，请检查网络或稍后重试": "ネットワーク要求に失敗しました。接続を確認してください",
        "ffmpeg 调用失败": "ffmpeg の呼び出しに失敗",
        "B 站返回了非 JSON 内容（可能被风控或需要验证）": "Bilibili が JSON 以外を返しました（制限または認証要求）",
    },
    "ko": {
        "{w} x {h}（{ratio}）": "{w} x {h} ({ratio})",
        "取消": "취소",
        "原图铺底": "원본 이미지 배경",
        "纯色留白": "단색 여백",
        "自定义背景图": "사용자 배경 이미지",
        "自定义背景视频": "사용자 배경 동영상",
        "裁切铺满": "잘라서 채우기",
        "自定义": "사용자 지정",
        "高": "높음",
        "标准": "표준",
        "小体积": "용량 작게",
        "跟 BGM": "BGM 길이",
        "自设总长": "총 길이 지정",
        "逐张秒数": "장당 초",
        " 秒": " 초",
        "未检测到输出设备": "출력 장치 없음",
        "（跟随 Windows 默认，换设备自动跟）": " (Windows 기본 장치를 따르며 자동 전환)",
        "设置": "설정",
        "留空 = 每张图所在目录（每次导出还会弹框确认）": "비우면 각 이미지 폴더 (내보낼 때마다 확인)",
        "浏览…": "찾아보기…",
        "默认导出目录": "기본 내보내기 폴더",
        "界面语言": "표시 언어",
        "跟随系统": "시스템 따르기",
        "简洁模式": "간단 모드",
        "专业模式": "전문가 모드",
        "点一下切换：简洁模式只留必要设置，专业模式显示全部选项": "클릭하여 전환: 간단 모드는 필수 설정만, 전문가 모드는 전체 옵션",
        "画面比例": "화면 비율",
        "（选比例会自动填，也可手改）": "（비율을 고르면 자동 입력, 직접 수정 가능）",
        "分辨率（宽×高）": "해상도 (가로×세로)",
        "成片尺寸": "출력 크기",
        "帧率": "프레임 레이트",
        "画质": "화질",
        "画面铺底": "배경 방식",
        "纯色留白用的底色": "단색 여백에 쓸 색",
        "留白颜色": "여백 색",
        "背景图片或视频（「自定义背景」才用）": "배경 이미지 또는 동영상 (사용자 배경일 때만)",
        "背景文件": "배경 파일",
        "背景模糊（原图铺底与自定义背景都适用）": "배경 흐리게 (원본 배경과 사용자 배경 모두)",
        "背景处理": "배경 처리",
        "默认节奏": "기본 길이 방식",
        "逐张模式默认": "장당 기본 초",
        "音乐淡入": "음악 페이드 인",
        "音乐淡出": "음악 페이드 아웃",
        "硬切": "하드 컷",
        "淡入淡出（每张首尾淡黑）": "페이드 (각 장면 앞뒤 검정)",
        "图片转场": "장면 전환",
        "转场时长": "전환 시간",
        "预览音频输出": "미리보기 오디오 출력",
        "选留白颜色": "여백 색 선택",
        "选背景视频": "배경 동영상 선택",
        "选背景图片": "배경 이미지 선택",
        "视频 (*.mp4 *.mov *.mkv *.webm)": "동영상 (*.mp4 *.mov *.mkv *.webm)",
        "图片 (*.jpg *.jpeg *.png *.bmp *.webp)": "이미지 (*.jpg *.jpeg *.png *.bmp *.webp)",
        "视频 (*.mp4)": "동영상 (*.mp4)",
        "选默认导出目录": "기본 내보내기 폴더 선택",
        "图片出片 —— 图片 + B站 BGM → 视频": "이미지로 영상 — 이미지 + Bilibili BGM → 영상",
        "加图片": "이미지 추가",
        "上移": "위로",
        "下移": "아래로",
        "删除": "삭제",
        "导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏": "내보내기 폴더, 화면 비율, 해상도, 프레임 레이트, 화질, 배경, 전환, 기본 길이 방식",
        "刷新预览": "미리보기 새로고침",
        "刷新预览 ●": "미리보기 새로고침 ●",
        "导出视频": "영상 내보내기",
        "预览：改完参数点右上角「刷新预览」": "미리보기: 설정을 바꾼 뒤 오른쪽 위 「미리보기 새로고침」을 누르세요",
        "B 站链接": "Bilibili 링크",
        "粘贴视频链接、BV 号或 b23.tv 短链，自动取整条音轨当背景音乐": "영상 링크, BV 번호, b23.tv 단축 링크를 붙여넣으면 전체 오디오 트랙을 BGM으로 씁니다",
        "重试": "다시 시도",
        "拖图片进来开始": "이미지를 끌어다 놓으면 시작",
        "参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果": "업데이트됨: {w}x{h} {fps}fps — 「미리보기 새로고침」으로 확인",
        "设置已保存": "설정이 저장되었습니다",
        "音频：{name}": "오디오: {name}",
        "选图片": "이미지 선택",
        "{name} 读不了，已跳过": "{name} 을(를) 읽을 수 없어 건너뜀",
        "已加 {n} 张图": "{n}장 추가됨",
        "这张图显示多久": "이 이미지 표시 시간",
        "第 {row} 张（秒）：": "{row}번째 이미지 (초):",
        "{n} 张图 · 总长 {total} · {src}": "이미지 {n}장 · 총 {total} · {src}",
        "{n} 张图 · 总长 {total}": "이미지 {n}장 · 총 {total}",
        "无音乐": "음악 없음",
        "{i}.  {dur} 秒   {name}": "{i}.  {dur}초   {name}",
        "先加图片": "먼저 이미지 추가",
        "生成预览…": "미리보기 생성 중…",
        "预览已更新（点画面播放/暂停，点色带跳转）": "미리보기 갱신됨 (영상 클릭 = 재생/일시정지, 바 클릭 = 이동)",
        "预览：{name}": "미리보기: {name}",
        "预览失败": "미리보기 실패",
        "正在从 B 站取音轨…": "Bilibili에서 오디오를 가져오는 중…",
        "BGM 已就绪：{total} · {name}": "BGM 준비 완료: {total} · {name}",
        "取音轨失败": "오디오 가져오기 실패",
        "导出到…": "내보낼 위치…",
        "正在导出…": "내보내는 중…",
        "导出完成：{path}": "내보내기 완료: {path}",
        "导出完成": "내보내기 완료",
        "视频已生成：\n{path}": "영상이 만들어졌습니다:\n{path}",
        "打开所在文件夹": "폴더 열기",
        "好": "확인",
        "导出失败": "내보내기 실패",
        "把图片拖进来，或点「加图片」": "이미지를 끌어다 놓거나 「이미지 추가」를 누르세요",
        "没有图片": "이미지가 없습니다",
        "选的背景文件不存在，去设置里重新选一个": "선택한 배경 파일이 없습니다. 설정에서 다시 고르세요",
        "背景图格式不支持，用 jpg / png / webp / bmp": "배경 이미지 형식을 지원하지 않습니다 (jpg / png / webp / bmp)",
        "ffmpeg 退出码 {rc}": "ffmpeg 종료 코드 {rc}",
        "图片 + BGM 合成视频（无窗口）": "이미지 + BGM 으로 영상 만들기 (창 없음)",
        "输出 mp4 路径": "출력 mp4 경로",
        "本地音频文件": "로컬 오디오 파일",
        "bilibili 链接或 BV 号": "bilibili 링크 또는 BV 번호",
        "每张图片的秒数（默认均分音乐）": "장당 초 (기본: 음악 길이 균등 분할)",
        "保留取下来的音频文件": "내려받은 오디오 파일 유지",
        "帧率：30 或 60": "프레임 레이트: 30 또는 60",
        "铺底：own 原图 / color 纯色 / image 背景图 / video 背景视频 / cover 裁切": "배경: own 원본 / color 단색 / image 배경 이미지 / video 배경 동영상 / cover 잘라내기",
        "画面比例（默认按短边 1080 算尺寸）": "화면 비율 (기본: 짧은 변 1080 기준)",
        "成片宽度（不给就按比例）": "출력 너비 (생략 시 비율 기준)",
        "成片高度（不给就按比例）": "출력 높이 (생략 시 비율 기준)",
        "自定义背景图片或视频": "사용자 배경 이미지 또는 동영상",
        "纯色留白的颜色，如 #ffffff": "단색 여백 색, 예: #ffffff",
        "背景不模糊": "배경을 흐리게 하지 않음",
        "图片转场：none 硬切 / fade 淡入淡出": "전환: none 하드 컷 / fade 페이드",
        "[1/3] 取音轨：{url}": "[1/3] 오디오 가져오기: {url}",
        "          失败：{msg}": "          실패: {msg}",
        "[2/3] {n} 张图，总长 {total}s，{durs}": "[2/3] 이미지 {n}장, 총 {total}s, {durs}",
        "[3/3] 渲染 {w}x{h} {fps}fps {fit}…": "[3/3] 렌더링 {w}x{h} {fps}fps {fit}…",
        "完成：{path}  ({secs}s, {mb} MB)": "완료: {path}  ({secs}s, {mb} MB)",
        "视频不存在或已被删除": "영상이 없거나 삭제되었습니다",
        "稿件不可见（已删除/仅自己可见/审核中）": "영상을 볼 수 없습니다 (삭제/비공개/심사 중)",
        "稿件审核中": "영상이 심사 중입니다",
        "没有访问权限（可能是付费/充电专属内容）": "접근 권한이 없습니다 (유료/멤버 전용일 수 있음)",
        "请求参数有误，链接可能不是有效的视频地址": "요청이 잘못되었습니다. 유효한 영상 링크가 아닐 수 있습니다",
        "请求被 B 站风控拦截，请稍后重试或降低频率": "Bilibili 요청 제한에 걸렸습니다. 잠시 후 다시 시도하세요",
        "请求被风控拦截（IP 层面），请稍后再试": "요청 제한에 걸렸습니다 (IP 기준). 잠시 후 다시 시도하세요",
        "该内容有地区限制，当前网络无法观看": "지역 제한이 있습니다. 현재 네트워크에서는 볼 수 없습니다",
        "该内容有版权限制，无法观看": "저작권 제한이 있어 볼 수 없습니다",
        "充电专属视频，需要给 UP 主充电后才能观看": "후원 전용 영상입니다 (Bilibili 충전 필요)",
        "付费视频，需要购买后才能观看": "유료 영상입니다. 구매 후 시청할 수 있습니다",
        "该内容在当前地区不可观看": "현재 지역에서는 볼 수 없습니다",
        "这是会员/付费内容的试看片段，完整音轨需要大会员账号": "회원/유료 콘텐츠의 미리보기 클립입니다. 전체 오디오는 프리미엄 계정이 필요합니다",
        "直播流没有点播音轨，本工具只支持点播视频/番剧": "라이브 방송에는 다시보기 오디오가 없습니다. VOD 영상/애니메이션만 지원합니다",
        "付费课程需要登录且已购买，本工具暂不支持": "유료 강좌는 로그인과 구매가 필요합니다. 지원하지 않습니다",
        "网络请求失败，请检查网络或稍后重试": "네트워크 요청 실패 — 연결을 확인하세요",
        "ffmpeg 调用失败": "ffmpeg 호출 실패",
        "B 站返回了非 JSON 内容（可能被风控或需要验证）": "Bilibili가 JSON이 아닌 응답을 반환했습니다 (요청 제한 또는 인증 필요)",
    },
}


# ---- 追加词条：多平台音源 / 主题配色 / 色环取色 ----
_TR_EXTRA = {
    "zh-TW": {
        "从本地文件抽音轨失败：{msg}": "從本地檔案抽音軌失敗：{msg}",
        "点开从色环里选主色，对撞色自动算": "點開從色環裡選主色，對撞色自動算",
        "下载": "下載",
        "{a}（对撞色 {b}）": "{a}（對撞色 {b}）",
        "选主色": "選主色",
        "正在下载音源组件…": "正在下載音源元件…",
        "音源组件": "音源元件",
        "音乐链接": "音樂連結",
        "B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨": "B 站 / 抖音 / YouTube / 快手等連結，或把本地影音拖進來，自動取整條音軌",
        "正在取音轨…": "正在取音軌…",
        "需要音源组件": "需要音源元件",
        "这个平台的链接要靠 yt-dlp 解析（约 15 MB，装在工具目录里，随时可删）。现在下载吗？": "這個平台的連結要靠 yt-dlp 解析（約 15 MB，裝在工具目錄裡，隨時可刪）。現在下載嗎？",
        "下载并继续": "下載並繼續",
        "当前颜色": "目前顏色",
        "自动对撞色": "自動對撞色",
        "已安装 {v} · {mb} MB（占地方可以随时删）": "已安裝 {v} · {mb} MB（佔空間可隨時刪）",
        "没装：抖音 / YouTube / 快手等要靠它取音轨": "沒裝：抖音 / YouTube / 快手等要靠它取音軌",
        "浅色": "淺色",
        "深色": "深色",
        "这个链接不支持（可能是私密/直播/需要登录）": "這個連結不支援（可能是私密/直播/需要登入）",
        "该站点要求登录，暂时取不到音轨": "該站點要求登入，暫時取不到音軌",
        "视频是私密的，取不到音轨": "影片是私密的，取不到音軌",
        "该视频有地区限制": "該影片有地區限制",
        "网络连不上该站点（可能要代理）": "網路連不上該站點（可能要代理）",
        "下载被站点拒绝（可能需要登录或频繁重试）": "下載被站點拒絕（可能需要登入或頻繁重試）",
        "取音轨失败（详情见设置里重试一次）": "取音軌失敗（詳情見設定裡重試一次）",
        "正在下载音源组件（yt-dlp）…": "正在下載音源元件（yt-dlp）…",
        "下载音源组件失败（检查网络 / pip）": "下載音源元件失敗（檢查網路 / pip）",
        "先给个链接或本地文件": "先給個連結或本地檔案",
        "这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载": "這個平台要先下載音源元件：設定 → 「抖音 / YouTube 支援」點下載",
        "正在解析链接…": "正在解析連結…",
        "找不到 Python，无法调用 yt-dlp": "找不到 Python，無法呼叫 yt-dlp",
        "解析超时（网络太慢或站点限流），稍后再试": "解析逾時（網路太慢或站點限流），稍後再試",
        "下载音源组件超时，检查网络后再试": "下載音源元件逾時，檢查網路後再試",
        "音源组件已就绪": "音源元件已就緒",
        "音源组件已就绪：{v}": "音源元件已就緒：{v}",
        "正在从本地视频里抽音轨…": "正在從本地影片裡抽音軌…",
        "正在取 B 站音轨…": "正在取 B 站音軌…",
    },
    "en": {
        "从本地文件抽音轨失败：{msg}": "Couldn't extract the audio track from this file: {msg}",
        "点开从色环里选主色，对撞色自动算": "Pick a main color on the wheel; the accent is computed for you",
        "下载": "Install",
        "{a}（对撞色 {b}）": "{a} (accent {b})",
        "选主色": "Pick main color",
        "正在下载音源组件…": "Downloading the audio module…",
        "音源组件": "Audio module",
        "音乐链接": "Music link",
        "B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨": "Bilibili / Douyin / YouTube / Kuaishou links, or drop a local audio/video file — the whole track becomes the BGM",
        "正在取音轨…": "Fetching the audio track…",
        "需要音源组件": "Audio module needed",
        "这个平台的链接要靠 yt-dlp 解析（约 15 MB，装在工具目录里，随时可删）。现在下载吗？": "This site's links need yt-dlp (about 15 MB in the tool folder, deletable anytime). Download it now?",
        "下载并继续": "Download and continue",
        "当前颜色": "Current color",
        "自动对撞色": "Auto accent",
        "已安装 {v} · {mb} MB（占地方可以随时删）": "Installed {v} · {mb} MB (deletable anytime)",
        "没装：抖音 / YouTube / 快手等要靠它取音轨": "Not installed: Douyin / YouTube / Kuaishou need it for the audio",
        "浅色": "Light",
        "深色": "Dark",
        "这个链接不支持（可能是私密/直播/需要登录）": "This link isn't supported (private, live, or login required)",
        "该站点要求登录，暂时取不到音轨": "This site wants you logged in; can't get the audio",
        "视频是私密的，取不到音轨": "This video is private; can't get the audio",
        "该视频有地区限制": "This video is region-locked",
        "网络连不上该站点（可能要代理）": "Can't reach this site (a proxy may be needed)",
        "下载被站点拒绝（可能需要登录或频繁重试）": "The site refused the download (login or retries may be needed)",
        "取音轨失败（详情见设置里重试一次）": "Couldn't fetch the audio track (retry from Settings for details)",
        "正在下载音源组件（yt-dlp）…": "Downloading the audio module (yt-dlp)…",
        "下载音源组件失败（检查网络 / pip）": "Couldn't download the audio module (check network / pip)",
        "先给个链接或本地文件": "Give me a link or a local file first",
        "这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载": "This site needs the audio module: Settings → “Douyin / YouTube support” → Install",
        "正在解析链接…": "Resolving the link…",
        "找不到 Python，无法调用 yt-dlp": "Python not found; can't run yt-dlp",
        "解析超时（网络太慢或站点限流），稍后再试": "Timed out resolving the link (slow network or rate limiting); try later",
        "下载音源组件超时，检查网络后再试": "Timed out downloading the audio module; check the network and retry",
        "音源组件已就绪": "Audio module ready",
        "音源组件已就绪：{v}": "Audio module ready: {v}",
        "正在从本地视频里抽音轨…": "Extracting the audio track from the video…",
        "正在取 B 站音轨…": "Fetching the Bilibili audio track…",
    },
    "ja": {
        "从本地文件抽音轨失败：{msg}": "ローカルファイルからの音声取り出しに失敗しました：{msg}",
        "点开从色环里选主色，对撞色自动算": "開いてカラーホイールからメインカラーを選ぶと、アクセントは自動で決まります",
        "下载": "ダウンロード",
        "{a}（对撞色 {b}）": "{a}（アクセント {b}）",
        "选主色": "メインカラーを選ぶ",
        "正在下载音源组件…": "音源コンポーネントをダウンロード中…",
        "音源组件": "音源コンポーネント",
        "音乐链接": "音楽リンク",
        "B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨": "Bilibili / Douyin / YouTube / Kuaishou などのリンク、またはローカルの音声・動画をドロップすると、音声トラック全体を BGM にします",
        "正在取音轨…": "音声トラックを取得中…",
        "需要音源组件": "音源コンポーネントが必要です",
        "这个平台的链接要靠 yt-dlp 解析（约 15 MB，装在工具目录里，随时可删）。现在下载吗？": "このサイトのリンクは yt-dlp で解析します（約 15 MB、ツールフォルダに保存、いつでも削除可）。今ダウンロードしますか？",
        "下载并继续": "ダウンロードして続行",
        "当前颜色": "現在の色",
        "自动对撞色": "自動アクセントカラー",
        "已安装 {v} · {mb} MB（占地方可以随时删）": "インストール済み {v} · {mb} MB（いつでも削除できます）",
        "没装：抖音 / YouTube / 快手等要靠它取音轨": "未インストール：Douyin / YouTube / Kuaishou などはこれが必要です",
        "浅色": "ライト",
        "深色": "ダーク",
        "这个链接不支持（可能是私密/直播/需要登录）": "このリンクは未対応です（限定公開・ライブ・ログインが必要かもしれません）",
        "该站点要求登录，暂时取不到音轨": "このサイトはログインが必要で、今は音声を取得できません",
        "视频是私密的，取不到音轨": "この動画は非公開のため音声を取得できません",
        "该视频有地区限制": "この動画は地域制限があります",
        "网络连不上该站点（可能要代理）": "このサイトに接続できません（プロキシが必要かもしれません）",
        "下载被站点拒绝（可能需要登录或频繁重试）": "サイトにダウンロードを拒否されました（ログインや再試行が必要かもしれません）",
        "取音轨失败（详情见设置里重试一次）": "音声の取得に失敗しました（設定からもう一度お試しください）",
        "正在下载音源组件（yt-dlp）…": "音源コンポーネント（yt-dlp）をダウンロード中…",
        "下载音源组件失败（检查网络 / pip）": "音源コンポーネントのダウンロードに失敗（ネットワーク / pip を確認）",
        "先给个链接或本地文件": "先にリンクかローカルファイルを指定してください",
        "这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载": "このサイトは先に音源コンポーネントが必要です：設定 → 「Douyin / YouTube 対応」→ ダウンロード",
        "正在解析链接…": "リンクを解析中…",
        "找不到 Python，无法调用 yt-dlp": "Python が見つからず yt-dlp を実行できません",
        "解析超时（网络太慢或站点限流），稍后再试": "解析がタイムアウトしました（回線が遅いか制限中）。後でもう一度",
        "下载音源组件超时，检查网络后再试": "音源コンポーネントのダウンロードがタイムアウト。ネットワークを確認して再試行",
        "音源组件已就绪": "音源コンポーネントの準備ができました",
        "音源组件已就绪：{v}": "音源コンポーネントの準備完了：{v}",
        "正在从本地视频里抽音轨…": "ローカル動画から音声を抽出中…",
        "正在取 B 站音轨…": "Bilibili の音声トラックを取得中…",
    },
    "ko": {
        "从本地文件抽音轨失败：{msg}": "로컬 파일에서 오디오 트랙을 추출하지 못했습니다: {msg}",
        "点开从色环里选主色，对撞色自动算": "열어서 색상환에서 메인 색을 고르면 액센트 색은 자동으로 계산됩니다",
        "下载": "다운로드",
        "{a}（对撞色 {b}）": "{a} (액센트 {b})",
        "选主色": "메인 색 고르기",
        "正在下载音源组件…": "오디오 구성 요소 다운로드 중…",
        "音源组件": "오디오 구성 요소",
        "音乐链接": "음악 링크",
        "B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨": "Bilibili / Douyin / YouTube / Kuaishou 링크를 붙여넣거나 로컬 오디오·영상을 끌어다 놓으면 전체 오디오 트랙이 BGM이 됩니다",
        "正在取音轨…": "오디오 트랙을 가져오는 중…",
        "需要音源组件": "오디오 구성 요소가 필요합니다",
        "这个平台的链接要靠 yt-dlp 解析（约 15 MB，装在工具目录里，随时可删）。现在下载吗？": "이 사이트의 링크는 yt-dlp로 해석합니다(약 15 MB, 도구 폴더에 저장, 언제든 삭제 가능). 지금 다운로드할까요?",
        "下载并继续": "다운로드하고 계속",
        "当前颜色": "현재 색",
        "自动对撞色": "자동 액센트 색",
        "已安装 {v} · {mb} MB（占地方可以随时删）": "설치됨 {v} · {mb} MB (언제든 삭제 가능)",
        "没装：抖音 / YouTube / 快手等要靠它取音轨": "미설치: Douyin / YouTube / Kuaishou 등은 이 구성 요소가 필요합니다",
        "浅色": "라이트",
        "深色": "다크",
        "这个链接不支持（可能是私密/直播/需要登录）": "지원하지 않는 링크입니다(비공개·라이브·로그인 필요일 수 있음)",
        "该站点要求登录，暂时取不到音轨": "이 사이트는 로그인이 필요해 지금은 오디오를 가져올 수 없습니다",
        "视频是私密的，取不到音轨": "비공개 영상이라 오디오를 가져올 수 없습니다",
        "该视频有地区限制": "이 영상은 지역 제한이 있습니다",
        "网络连不上该站点（可能要代理）": "이 사이트에 연결할 수 없습니다(프록시가 필요할 수 있음)",
        "下载被站点拒绝（可能需要登录或频繁重试）": "사이트가 다운로드를 거부했습니다(로그인이나 재시도가 필요할 수 있음)",
        "取音轨失败（详情见设置里重试一次）": "오디오 트랙을 가져오지 못했습니다(설정에서 다시 시도해 보세요)",
        "正在下载音源组件（yt-dlp）…": "오디오 구성 요소(yt-dlp) 다운로드 중…",
        "下载音源组件失败（检查网络 / pip）": "오디오 구성 요소 다운로드 실패(네트워크 / pip 확인)",
        "先给个链接或本地文件": "먼저 링크나 로컬 파일을 주세요",
        "这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载": "이 사이트는 먼저 오디오 구성 요소가 필요합니다: 설정 → 「Douyin / YouTube 지원」 → 다운로드",
        "正在解析链接…": "링크를 해석하는 중…",
        "找不到 Python，无法调用 yt-dlp": "Python을 찾을 수 없어 yt-dlp를 실행할 수 없습니다",
        "解析超时（网络太慢或站点限流），稍后再试": "링크 해석 시간 초과(느린 네트워크 또는 사이트 제한). 나중에 다시 시도하세요",
        "下载音源组件超时，检查网络后再试": "오디오 구성 요소 다운로드 시간 초과. 네트워크를 확인하고 재시도하세요",
        "音源组件已就绪": "오디오 구성 요소 준비됨",
        "音源组件已就绪：{v}": "오디오 구성 요소 준비됨: {v}",
        "正在从本地视频里抽音轨…": "로컬 영상에서 오디오 트랙을 추출하는 중…",
        "正在取 B 站音轨…": "Bilibili 오디오 트랙을 가져오는 중…",
    },
}
for _lang, _extra in _TR_EXTRA.items():
    TR[_lang].update(_extra)


# ---- 追加词条 2：设置窗新增行 ----
_TR_EXTRA2 = {
    "zh-TW": {
        "主色": "主色",
        "界面主题": "介面主題",
        "抖音 / YouTube 支持": "抖音 / YouTube 支援",
    },
    "en": {
        "主色": "Main color",
        "界面主题": "Theme",
        "抖音 / YouTube 支持": "Douyin / YouTube support",
    },
    "ja": {
        "主色": "メインカラー",
        "界面主题": "テーマ",
        "抖音 / YouTube 支持": "Douyin / YouTube 対応",
    },
    "ko": {
        "主色": "메인 색",
        "界面主题": "테마",
        "抖音 / YouTube 支持": "Douyin / YouTube 지원",
    },
}
for _lang, _extra2 in _TR_EXTRA2.items():
    TR[_lang].update(_extra2)

_TR_EXTRA3 = {
    "zh-TW": {
        "本地音视频文件，或抖音 / YouTube 等视频链接": "本機音訊或影片檔案，或抖音 / YouTube 等影片連結",
        "bilibili 链接或 BV 号（同 --audio）": "bilibili 連結或 BV 號（同 --audio）",
    },
    "en": {
        "本地音视频文件，或抖音 / YouTube 等视频链接": "a local audio/video file, or a Douyin / YouTube link",
        "bilibili 链接或 BV 号（同 --audio）": "bilibili link or BV id (same as --audio)",
    },
    "ja": {
        "本地音视频文件，或抖音 / YouTube 等视频链接": "ローカルの音声・動画ファイル、または Douyin / YouTube のリンク",
        "bilibili 链接或 BV 号（同 --audio）": "bilibili のリンクまたは BV 番号（--audio と同じ）",
    },
    "ko": {
        "本地音视频文件，或抖音 / YouTube 等视频链接": "로컬 오디오/비디오 파일 또는 Douyin / YouTube 링크",
        "bilibili 链接或 BV 号（同 --audio）": "bilibili 링크 또는 BV 번호 (--audio와 동일)",
    },
}
for _lang, _extra3 in _TR_EXTRA3.items():
    TR[_lang].update(_extra3)

_TR_EXTRA4 = {
    "zh-TW": {
        "缺少依赖": "缺少相依元件",
        "没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）":
            "找不到 ffmpeg / ffprobe。\nWindows 上一行裝完：winget install Gyan.FFmpeg\n（裝好後重開這個視窗）",
        "正在取消…": "正在取消…",
        "已取消": "已取消",
        "这张图片读不出来：{name}": "這張圖片讀不出來：{name}",
    },
    "en": {
        "缺少依赖": "Missing dependency",
        "没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）":
            "ffmpeg / ffprobe not found.\nOn Windows one line installs it: winget install Gyan.FFmpeg\n(then reopen this window)",
        "正在取消…": "Cancelling…",
        "已取消": "Cancelled",
        "这张图片读不出来：{name}": "Can't read this image: {name}",
    },
    "ja": {
        "缺少依赖": "依存関係が見つかりません",
        "没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）":
            "ffmpeg / ffprobe が見つかりません。\nWindows では次の一行で入ります：winget install Gyan.FFmpeg\n（入れたらこのウィンドウを開き直してください）",
        "正在取消…": "キャンセルしています…",
        "已取消": "キャンセルしました",
        "这张图片读不出来：{name}": "この画像を読み込めません：{name}",
    },
    "ko": {
        "缺少依赖": "필수 프로그램 없음",
        "没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）":
            "ffmpeg / ffprobe 를 찾을 수 없습니다.\nWindows 에서는 한 줄로 설치합니다: winget install Gyan.FFmpeg\n(설치 후 이 창을 다시 열어 주세요)",
        "正在取消…": "취소하는 중…",
        "已取消": "취소되었습니다",
        "这张图片读不出来：{name}": "이 이미지를 읽을 수 없습니다: {name}",
    },
}
for _lang, _extra4 in _TR_EXTRA4.items():
    TR[_lang].update(_extra4)
_TR_EXTRA5 = {
    "zh-TW": {
        "生成预览… {pct}%": "產生預覽… {pct}%",
        "正在导出… {pct}%": "正在匯出… {pct}%",
        "正在导出… {pct}% · 剩余约 {left}": "正在匯出… {pct}% · 剩餘約 {left}",
        "还在导出": "還在匯出",
        "视频还在导出，现在关掉会丢掉这次的结果。": "影片還在匯出，現在關掉會丟掉這次的結果。",
        "继续导出": "繼續匯出",
        "关掉": "關掉",
    },
    "en": {
        "生成预览… {pct}%": "Building preview… {pct}%",
        "正在导出… {pct}%": "Exporting… {pct}%",
        "正在导出… {pct}% · 剩余约 {left}": "Exporting… {pct}% · about {left} left",
        "还在导出": "Export still running",
        "视频还在导出，现在关掉会丢掉这次的结果。": "The video is still exporting; closing now discards this run.",
        "继续导出": "Keep exporting",
        "关掉": "Close anyway",
    },
    "ja": {
        "生成预览… {pct}%": "プレビュー生成中… {pct}%",
        "正在导出… {pct}%": "書き出し中… {pct}%",
        "正在导出… {pct}% · 剩余约 {left}": "書き出し中… {pct}% · 残り約 {left}",
        "还在导出": "書き出し中です",
        "视频还在导出，现在关掉会丢掉这次的结果。": "まだ書き出し中です。今閉じると今回の結果は失われます。",
        "继续导出": "書き出しを続ける",
        "关掉": "閉じる",
    },
    "ko": {
        "生成预览… {pct}%": "미리보기 생성 중… {pct}%",
        "正在导出… {pct}%": "내보내는 중… {pct}%",
        "正在导出… {pct}% · 剩余约 {left}": "내보내는 중… {pct}% · 약 {left} 남음",
        "还在导出": "내보내는 중",
        "视频还在导出，现在关掉会丢掉这次的结果。": "아직 내보내는 중입니다. 지금 닫으면 이번 결과는 사라집니다.",
        "继续导出": "계속 내보내기",
        "关掉": "닫기",
    },
}
for _lang, _extra5 in _TR_EXTRA5.items():
    TR[_lang].update(_extra5)

_TR_EXTRA6 = {
    "zh-TW": {
        "导出完自动打开所在文件夹": "匯出完自動開啟所在資料夾",
    },
    "en": {
        "导出完自动打开所在文件夹": "Open the folder after exporting",
    },
    "ja": {
        "导出完自动打开所在文件夹": "書き出し後にフォルダーを開く",
    },
    "ko": {
        "导出完自动打开所在文件夹": "내보낸 뒤 폴더 열기",
    },
}
for _lang, _extra6 in _TR_EXTRA6.items():
    TR[_lang].update(_extra6)

_TR_EXTRA7 = {
    "zh-TW": {
        "画面动效": "畫面動效",
        "静止": "靜止",
        "缓慢推近": "緩慢推近",
        "缓慢拉远": "緩慢拉遠",
        "交替推拉": "交替推拉",
        "画面动效：none 静止 / in 缓慢推近 / out 缓慢拉远 / inout 交替": "畫面動效：none 靜止 / in 緩慢推近 / out 緩慢拉遠 / inout 交替",
    },
    "en": {
        "画面动效": "Motion",
        "静止": "Still",
        "缓慢推近": "Slow zoom in",
        "缓慢拉远": "Slow zoom out",
        "交替推拉": "Alternate zoom",
        "画面动效：none 静止 / in 缓慢推近 / out 缓慢拉远 / inout 交替": "Motion: none still / in slow zoom in / out slow zoom out / inout alternate",
    },
    "ja": {
        "画面动效": "映像の動き",
        "静止": "静止",
        "缓慢推近": "ゆっくりズームイン",
        "缓慢拉远": "ゆっくりズームアウト",
        "交替推拉": "交互ズーム",
        "画面动效：none 静止 / in 缓慢推近 / out 缓慢拉远 / inout 交替": "映像の動き：none 静止 / in ズームイン / out ズームアウト / inout 交互",
    },
    "ko": {
        "画面动效": "화면 효과",
        "静止": "정지",
        "缓慢推近": "천천히 확대",
        "缓慢拉远": "천천히 축소",
        "交替推拉": "교차 확대/축소",
        "画面动效：none 静止 / in 缓慢推近 / out 缓慢拉远 / inout 交替": "화면 효과: none 정지 / in 확대 / out 축소 / inout 교차",
    },
}
for _lang, _extra7 in _TR_EXTRA7.items():
    TR[_lang].update(_extra7)

_TR_EXTRA8 = {
    "zh-TW": {
        "音乐音量": "音樂音量",
        "背景音乐音量百分比（默认 100）": "背景音樂音量百分比（預設 100）",
    },
    "en": {
        "音乐音量": "Music volume",
        "背景音乐音量百分比（默认 100）": "Background music volume in percent (default 100)",
    },
    "ja": {
        "音乐音量": "音楽の音量",
        "背景音乐音量百分比（默认 100）": "BGM の音量（％、既定 100）",
    },
    "ko": {
        "音乐音量": "음악 볼륨",
        "背景音乐音量百分比（默认 100）": "배경 음악 볼륨 백분율 (기본 100)",
    },
}
for _lang, _extra8 in _TR_EXTRA8.items():
    TR[_lang].update(_extra8)

_TR_EXTRA9 = {
    "zh-TW": {
        "裁切位置": "裁切位置",
        "居中": "置中",
        "偏上": "偏上",
        "偏下": "偏下",
        "裁切铺满时留住哪里：center 居中 / top 偏上 / bottom 偏下": "裁切鋪滿時保留哪裡：center 置中 / top 偏上 / bottom 偏下",
    },
    "en": {
        "裁切位置": "Crop focus",
        "居中": "Center",
        "偏上": "Top",
        "偏下": "Bottom",
        "裁切铺满时留住哪里：center 居中 / top 偏上 / bottom 偏下": "Crop focus when filling: center / top / bottom",
    },
    "ja": {
        "裁切位置": "トリミング位置",
        "居中": "中央",
        "偏上": "上寄せ",
        "偏下": "下寄せ",
        "裁切铺满时留住哪里：center 居中 / top 偏上 / bottom 偏下": "切り抜き位置：center 中央 / top 上寄せ / bottom 下寄せ",
    },
    "ko": {
        "裁切位置": "자르기 위치",
        "居中": "가운데",
        "偏上": "위쪽",
        "偏下": "아래쪽",
        "裁切铺满时留住哪里：center 居中 / top 偏上 / bottom 偏下": "채울 때 자를 위치: center 가운데 / top 위쪽 / bottom 아래쪽",
    },
}
for _lang, _extra9 in _TR_EXTRA9.items():
    TR[_lang].update(_extra9)

_TR_EXTRA10 = {
    "zh-TW": {
        "音乐起点": "音樂起點",
        "音乐长度": "音樂長度",
        "整首": "整首",
        "背景音乐从第几秒开始用（默认 0）": "背景音樂從第幾秒開始用（預設 0）",
        "背景音乐用多长，0 = 一直用到音乐结尾": "背景音樂用多長，0 = 一直用到音樂結尾",
    },
    "en": {
        "音乐起点": "Music start",
        "音乐长度": "Music length",
        "整首": "Whole track",
        "背景音乐从第几秒开始用（默认 0）": "Start the music at this second (default 0)",
        "背景音乐用多长，0 = 一直用到音乐结尾": "How long to use: 0 = to the end of the track",
    },
    "ja": {
        "音乐起点": "音楽の開始位置",
        "音乐长度": "音楽の長さ",
        "整首": "曲全体",
        "背景音乐从第几秒开始用（默认 0）": "音楽を何秒目から使うか（既定 0）",
        "背景音乐用多长，0 = 一直用到音乐结尾": "音楽をどこまで使うか、0 = 曲の最後まで",
    },
    "ko": {
        "音乐起点": "음악 시작",
        "音乐长度": "음악 길이",
        "整首": "곡 전체",
        "背景音乐从第几秒开始用（默认 0）": "음악을 몇 초부터 사용할지 (기본 0)",
        "背景音乐用多长，0 = 一直用到音乐结尾": "음악을 얼마나 사용할지, 0 = 곡 끝까지",
    },
}
for _lang, _extra10 in _TR_EXTRA10.items():
    TR[_lang].update(_extra10)

_TR_EXTRA11 = {
    "zh-TW": {
        "外观": "外觀",
        "导出": "匯出",
        "画面": "畫面",
        "音乐": "音樂",
        "节奏与动效": "節奏與動效",
        "其它": "其它",
    },
    "en": {
        "外观": "Appearance",
        "导出": "Export",
        "画面": "Picture",
        "音乐": "Music",
        "节奏与动效": "Timing & motion",
        "其它": "Misc",
    },
    "ja": {
        "外观": "外観",
        "导出": "書き出し",
        "画面": "画面",
        "音乐": "音楽",
        "节奏与动效": "テンポと動き",
        "其它": "その他",
    },
    "ko": {
        "外观": "모양",
        "导出": "내보내기",
        "画面": "화면",
        "音乐": "음악",
        "节奏与动效": "템포와 움직임",
        "其它": "기타",
    },
}
for _lang, _extra11 in _TR_EXTRA11.items():
    TR[_lang].update(_extra11)

_TR_EXTRA12 = {
    "zh-TW": {
        "导出时先问我保存到哪（关掉就直接存默认目录）": "匯出時先問我存到哪（關掉就直接存預設資料夾）",
        "留空 = 每张图所在目录（关掉「先问我」时就用这里）": "留空 = 每張圖所在資料夾（關掉「先問我」時就用這裡）",
    },
    "en": {
        "导出时先问我保存到哪（关掉就直接存默认目录）": "Ask where to save on export (off = use the default folder)",
        "留空 = 每张图所在目录（关掉「先问我」时就用这里）": "Empty = folder of the images (used when asking is off)",
    },
    "ja": {
        "导出时先问我保存到哪（关掉就直接存默认目录）": "書き出し先を毎回たずねる（オフなら既定フォルダへ）",
        "留空 = 每张图所在目录（关掉「先问我」时就用这里）": "空 = 画像のあるフォルダ（たずねるのがオフのときに使用）",
    },
    "ko": {
        "导出时先问我保存到哪（关掉就直接存默认目录）": "내보낼 때 저장 위치를 묻기 (끄면 기본 폴더에 저장)",
        "留空 = 每张图所在目录（关掉「先问我」时就用这里）": "비우면 이미지가 있는 폴더 (묻기가 꺼져 있을 때 사용)",
    },
}
for _lang, _extra12 in _TR_EXTRA12.items():
    TR[_lang].update(_extra12)

_TR_EXTRA13 = {
    "zh-TW": {"预览播放出错：{msg}": "預覽播放出錯：{msg}"},
    "en": {"预览播放出错：{msg}": "Preview playback error: {msg}"},
    "ja": {"预览播放出错：{msg}": "プレビュー再生エラー：{msg}"},
    "ko": {"预览播放出错：{msg}": "미리보기 재생 오류: {msg}"},
}
for _lang, _extra13 in _TR_EXTRA13.items():
    TR[_lang].update(_extra13)


_TR_EXTRA14 = {
    "zh-TW": {
        "照各家视频站的品牌色来，也能自己调": "照各家影音平台的品牌色來，也能自己調",
        "哔哩哔哩": "嗶哩嗶哩",
        "抖音": "抖音",
        "快手": "快手",
        "小红书": "小紅書",
        "微博": "微博",
        "腾讯视频": "騰訊視頻",
        "爱奇艺": "愛奇藝",
        "优酷": "優酷",
    },
    "en": {
        "照各家视频站的品牌色来，也能自己调": "Brand colors of video sites - or pick your own",
        "哔哩哔哩": "Bilibili",
        "抖音": "Douyin",
        "快手": "Kuaishou",
        "小红书": "Xiaohongshu",
        "微博": "Weibo",
        "腾讯视频": "Tencent Video",
        "爱奇艺": "iQIYI",
        "优酷": "Youku",
    },
    "ja": {
        "照各家视频站的品牌色来，也能自己调": "各動画サイトのブランドカラー、自分で選ぶことも可",
        "哔哩哔哩": "ビリビリ",
        "抖音": "抖音（Douyin）",
        "快手": "快手（Kuaishou）",
        "小红书": "小紅書（RED）",
        "微博": "微博（Weibo）",
        "腾讯视频": "テンセントビデオ",
        "爱奇艺": "愛奇芸（iQIYI）",
        "优酷": "優酷（Youku）",
    },
    "ko": {
        "照各家视频站的品牌色来，也能自己调": "동영상 사이트 브랜드 색상, 직접 고를 수도 있음",
        "哔哩哔哩": "빌리빌리",
        "抖音": "더우인",
        "快手": "콰이쇼우",
        "小红书": "샤오홍슈",
        "微博": "웨이보",
        "腾讯视频": "텐센트 비디오",
        "爱奇艺": "아이치이",
        "优酷": "유쿠",
    },
}
for _lang, _extra14 in _TR_EXTRA14.items():
    TR[_lang].update(_extra14)


# ---- 组件按需下载（ffmpeg / yt-dlp）相关 ---------------
_TR_EXTRA15 = {
    'ffmpeg 没下成功。检查一下网络再开一次，或者手动装：winget install Gyan.FFmpeg': {
        'zh-TW': 'ffmpeg 沒下載成功。檢查一下網路再開一次，或手動安裝：winget install Gyan.FFmpeg',
        'en': 'ffmpeg download failed. Check your network and open again, or install it manually: winget install Gyan.FFmpeg',
        'ja': 'ffmpeg のダウンロードに失敗しました。ネットワークを確認して開き直すか、手動でインストール：winget install Gyan.FFmpeg',
        'ko': 'ffmpeg 다운로드에 실패했습니다. 네트워크를 확인해 다시 열거나 직접 설치: winget install Gyan.FFmpeg',
    },
    'ffmpeg 装好了但还是找不到，重开一次窗口试试': {
        'zh-TW': 'ffmpeg 裝好了但還是找不到，重開一次視窗試試',
        'en': 'ffmpeg is installed but still not found — try opening again',
        'ja': 'ffmpeg は入りましたが見つかりません。開き直してみてください',
        'ko': 'ffmpeg 설치했지만 여전히 찾을 수 없습니다. 다시 열어 보세요',
    },
    '下载 ffmpeg': {
        'zh-TW': '下載 ffmpeg',
        'en': 'Download ffmpeg',
        'ja': 'ffmpeg をダウンロード',
        'ko': 'ffmpeg 다운로드',
    },
    '下载失败': {
        'zh-TW': '下載失敗',
        'en': 'Download failed',
        'ja': 'ダウンロード失敗',
        'ko': '다운로드 실패',
    },
    '先不用': {
        'zh-TW': '先不用',
        'en': 'Not now',
        'ja': 'あとで',
        'ko': '나중에',
    },
    '先用系统里这份：{path}（想随身带就在这下一份）': {
        'zh-TW': '先用系統裡這份：{path}（想隨身帶就在這下一份）',
        'en': 'Using the system copy: {path} (download one here to carry it along)',
        'ja': 'システムのものを使います：{path}（持ち歩くならここでダウンロード）',
        'ko': '시스템 것을 사용합니다: {path} (들고 다니려면 여기서 다운로드)',
    },
    '出片要靠 ffmpeg（约 120 MB，只下这一次，之后不再问）。\n现在就自动下载吗？': {
        'zh-TW': '出片要靠 ffmpeg（約 120 MB，只下這一次，之後不再問）。\n現在就自動下載嗎？',
        'en': 'Exporting needs ffmpeg (about 120 MB, downloaded once).\nDownload it now?',
        'ja': '書き出しには ffmpeg が必要です（約 120 MB、今回だけ）。\n今すぐダウンロードしますか？',
        'ko': '출력에는 ffmpeg가 필요합니다(약 120 MB, 이번 한 번만).\n지금 내려받을까요?',
    },
    '找不到 yt-dlp，去设置里重新下载': {
        'zh-TW': '找不到 yt-dlp，去設定裡重新下載',
        'en': 'yt-dlp not found — download it again in Settings',
        'ja': 'yt-dlp が見つかりません。設定からもう一度ダウンロードしてください',
        'ko': 'yt-dlp를 찾을 수 없습니다. 설정에서 다시 다운로드하세요',
    },
    '正在下载 ffmpeg…': {
        'zh-TW': '正在下載 ffmpeg…',
        'en': 'Downloading ffmpeg…',
        'ja': 'ffmpeg をダウンロード中…',
        'ko': 'ffmpeg 다운로드 중…',
    },
    '正在下载…': {
        'zh-TW': '正在下載…',
        'en': 'Downloading…',
        'ja': 'ダウンロード中…',
        'ko': '다운로드 중…',
    },
    '没找到 ffmpeg，正在下载（约 120 MB，只下这一次）…': {
        'zh-TW': '沒找到 ffmpeg，正在下載（約 120 MB，只下這一次）…',
        'en': 'ffmpeg not found — downloading (about 120 MB, once)…',
        'ja': 'ffmpeg が見つかりません。ダウンロード中（約 120 MB、今回だけ）…',
        'ko': 'ffmpeg를 찾을 수 없어 다운로드합니다(약 120 MB, 이번 한 번)…',
    },
    '现在下载': {
        'zh-TW': '現在下載',
        'en': 'Download now',
        'ja': '今すぐダウンロード',
        'ko': '지금 다운로드',
    },
    '第一次运行': {
        'zh-TW': '第一次執行',
        'en': 'First run',
        'ja': '初回起動',
        'ko': '첫 실행',
    },
    '还没下载音源组件': {
        'zh-TW': '還沒下載音源元件',
        'en': 'The audio component is not downloaded yet',
        'ja': '音源コンポーネントが未ダウンロードです',
        'ko': '음원 구성 요소가 아직 없습니다',
    },
    '这个平台的链接要靠 yt-dlp 解析（约 25 MB，装在工具目录里，随时可删）。现在下载吗？': {
        'zh-TW': '這個平台的連結要靠 yt-dlp 解析（約 25 MB，裝在工具目錄裡，隨時可刪）。現在下載嗎？',
        'en': 'Links from this site need yt-dlp (about 25 MB, stored in the tool folder, deletable anytime). Download now?',
        'ja': 'このサイトのリンクは yt-dlp で解析します（約 25 MB、ツールフォルダに保存、いつでも削除可）。今ダウンロードしますか？',
        'ko': '이 사이트 링크는 yt-dlp로 분석합니다(약 25 MB, 도구 폴더에 저장, 언제든 삭제 가능). 지금 내려받을까요?',
    },
    '出片引擎，约 120 MB': {
        'zh-TW': '出片引擎，約 120 MB',
        'en': 'Export engine, about 120 MB',
        'ja': '書き出しエンジン（約 120 MB）',
        'ko': '출력 엔진, 약 120 MB',
    },
    '抖音 / YouTube 等链接解析，约 25 MB': {
        'zh-TW': '抖音 / YouTube 等連結解析，約 25 MB',
        'en': 'Douyin / YouTube link parsing, about 25 MB',
        'ja': 'Douyin / YouTube などのリンク解析（約 25 MB）',
        'ko': '더우인 / YouTube 링크 분석, 약 25 MB',
    },
}

for _k, _trs in _TR_EXTRA15.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)

# 自绘标题栏上的三个窗口按钮
_TR_EXTRA16 = {
    "最小化": {
        "zh-TW": "最小化",
        "en": "Minimize",
        "ja": "最小化",
        "ko": "최소화",
    },
    "最大化": {
        "zh-TW": "最大化",
        "en": "Maximize",
        "ja": "最大化",
        "ko": "최대화",
    },
    "还原": {
        "zh-TW": "還原",
        "en": "Restore",
        "ja": "元に戻す",
        "ko": "이전 크기로",
    },
    "关闭": {
        "zh-TW": "關閉",
        "en": "Close",
        "ja": "閉じる",
        "ko": "닫기",
    },
}

for _k, _trs in _TR_EXTRA16.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)



_TR_EXTRA16 = {
    '图片 (*.png *.jpg *.jpeg *.webp *.bmp)': {
        'zh-TW': '圖片 (*.png *.jpg *.jpeg *.webp *.bmp)',
        'en': 'Images (*.png *.jpg *.jpeg *.webp *.bmp)',
        'ja': '画像 (*.png *.jpg *.jpeg *.webp *.bmp)',
        'ko': '이미지 (*.png *.jpg *.jpeg *.webp *.bmp)',
    },
    '导出完成，要不要打开文件夹？': {
        'zh-TW': '匯出完成，要不要開啟資料夾？',
        'en': 'Export finished. Open the folder?',
        'ja': '書き出し完了。フォルダーを開きますか？',
        'ko': '내보내기 완료. 폴더를 열까요?',
    },
    '提示': {
        'zh-TW': '提示',
        'en': 'Notice',
        'ja': 'お知らせ',
        'ko': '알림',
    },
    '秒数': {
        'zh-TW': '秒數',
        'en': 'Seconds',
        'ja': '秒数',
        'ko': '초',
    },
}
for _k, _trs in _TR_EXTRA16.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)


_TR_EXTRA17 = {
    '片头': {
        'zh-TW': '片頭',
        'en': 'Intro',
        'ja': 'オープニング',
        'ko': '오프닝',
    },
    '片尾': {
        'zh-TW': '片尾',
        'en': 'Outro',
        'ja': 'エンディング',
        'ko': '엔딩',
    },
    '音频': {
        'zh-TW': '音訊',
        'en': 'Audio',
        'ja': 'オーディオ',
        'ko': '오디오',
    },
    '淡入淡出': {
        'zh-TW': '淡入淡出',
        'en': 'Fade in · out',
        'ja': 'フェードイン・アウト',
        'ko': '페이드 인/아웃',
    },
    '交叉淡化（与相邻元素）': {
        'zh-TW': '交叉淡化（與相鄰元素）',
        'en': 'Crossfade with neighbours',
        'ja': 'クロスフェード（隣接要素と）',
        'ko': '크로스페이드(인접 요소와)',
    },
    '这张显示多久…': {
        'zh-TW': '這張顯示多久…',
        'en': 'How long should this show…',
        'ja': 'この要素の表示時間…',
        'ko': '이 요소 표시 시간…',
    },
    '设置音量…': {
        'zh-TW': '設定音量…',
        'en': 'Set volume…',
        'ja': '音量を設定…',
        'ko': '볼륨 설정…',
    },
    '音量预设': {
        'zh-TW': '音量預設',
        'en': 'Volume presets',
        'ja': '音量プリセット',
        'ko': '볼륨 프리셋',
    },
    '音量': {
        'zh-TW': '音量',
        'en': 'Volume',
        'ja': '音量',
        'ko': '볼륨',
    },
    '音量（%）：': {
        'zh-TW': '音量（%）：',
        'en': 'Volume (%):',
        'ja': '音量（%）：',
        'ko': '볼륨(%):',
    },
    '音量 {pct}%': {
        'zh-TW': '音量 {pct}%',
        'en': 'Volume {pct}%',
        'ja': '音量 {pct}%',
        'ko': '볼륨 {pct}%',
    },
    '启动时窗口': {
        'zh-TW': '啟動時視窗',
        'en': 'Window on startup',
        'ja': '起動時のウィンドウ',
        'ko': '시작 시 창',
    },
    '固定大小': {
        'zh-TW': '固定大小',
        'en': 'Fixed size',
        'ja': '固定サイズ',
        'ko': '고정 크기',
    },
    '记住上次': {
        'zh-TW': '記住上次',
        'en': 'Remember last',
        'ja': '前回を記憶',
        'ko': '마지막 크기 기억',
    },
    '启动时最大化': {
        'zh-TW': '啟動時最大化',
        'en': 'Start maximized',
        'ja': '最大化で起動',
        'ko': '최대화로 시작',
    },
    '默认片头视频': {
        'zh-TW': '預設片頭影片',
        'en': 'Default intro video',
        'ja': '既定のオープニング動画',
        'ko': '기본 오프닝 영상',
    },
    '默认片尾视频': {
        'zh-TW': '預設片尾影片',
        'en': 'Default outro video',
        'ja': '既定のエンディング動画',
        'ko': '기본 엔딩 영상',
    },
    '视频文件（留空 = 不加片头）': {
        'zh-TW': '影片檔（留空 = 不加片頭）',
        'en': 'Video file (empty = no intro)',
        'ja': '動画ファイル（空 = オープニングなし）',
        'ko': '영상 파일(비우면 오프닝 없음)',
    },
    '视频文件（留空 = 不加片尾）': {
        'zh-TW': '影片檔（留空 = 不加片尾）',
        'en': 'Video file (empty = no outro)',
        'ja': '動画ファイル（空 = エンディングなし）',
        'ko': '영상 파일(비우면 엔딩 없음)',
    },
    '选视频': {
        'zh-TW': '選影片',
        'en': 'Choose a video',
        'ja': '動画を選ぶ',
        'ko': '영상 선택',
    },
    '视频 (*.mp4 *.mov *.mkv *.webm *.avi)': {
        'zh-TW': '影片 (*.mp4 *.mov *.mkv *.webm *.avi)',
        'en': 'Video (*.mp4 *.mov *.mkv *.webm *.avi)',
        'ja': '動画 (*.mp4 *.mov *.mkv *.webm *.avi)',
        'ko': '영상 (*.mp4 *.mov *.mkv *.webm *.avi)',
    },
    '（含片头片尾）': {
        'zh-TW': '（含片頭片尾）',
        'en': '(intro/outro included)',
        'ja': '（オープニング・エンディング含む）',
        'ko': '(오프닝·엔딩 포함)',
    },
}
for _k, _trs in _TR_EXTRA17.items():
    for _lang, _v in _trs.items():
        TR[_lang][_k] = _v


_TR_EXTRA18 = {
    '已加音频 {name}': {
        'zh-TW': '已加音訊 {name}',
        'en': 'Audio added: {name}',
        'ja': 'オーディオを追加: {name}',
        'ko': '오디오 추가: {name}',
    },
    '添加音频文件': {
        'zh-TW': '新增音訊檔',
        'en': 'Add audio file',
        'ja': 'オーディオを追加',
        'ko': '오디오 파일 추가',
    },
    '添加音频文件…': {
        'zh-TW': '新增音訊檔…',
        'en': 'Add audio file…',
        'ja': 'オーディオを追加…',
        'ko': '오디오 파일 추가…',
    },
    '音频 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)': {
        'zh-TW': '音訊 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)',
        'en': 'Audio (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)',
        'ja': 'オーディオ (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)',
        'ko': '오디오 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)',
    },
    '删除这条音频': {
        'zh-TW': '刪除這條音訊',
        'en': 'Remove this audio',
        'ja': 'このオーディオを削除',
        'ko': '이 오디오 삭제',
    },
}
for _k, _trs in _TR_EXTRA18.items():
    for _lang, _v in _trs.items():
        TR[_lang][_k] = _v


_TR_EXTRA19 = {
    '闪退 / 导出失败时先看这份记录：{path}': {
        'zh-TW': '閃退 / 匯出失敗時先看這份記錄：{path}',
        'en': 'Crashes and failed exports are logged here: {path}',
        'ja': 'クラッシュや書き出し失敗の記録はここ：{path}',
        'ko': '크래시 및 내보내기 실패 기록: {path}',
    },
    '打开日志': {
        'zh-TW': '開啟日誌',
        'en': 'Open log',
        'ja': 'ログを開く',
        'ko': '로그 열기',
    },
    '错误日志': {
        'zh-TW': '錯誤日誌',
        'en': 'Error log',
        'ja': 'エラーログ',
        'ko': '오류 로그',
    },
    '版本': {
        'zh-TW': '版本',
        'en': 'Version',
        'ja': 'バージョン',
        'ko': '버전',
    },
}
for _k, _trs in _TR_EXTRA19.items():
    for _lang, _v in _trs.items():
        TR[_lang][_k] = _v

_TR_EXTRA20 = {
    '撤回': {
        'zh-TW': '撤回',
        'en': 'Undo',
        'ja': '元に戻す',
        'ko': '실행 취소',
    },
    '素材': {
        'zh-TW': '素材',
        'en': 'Media',
        'ja': '素材',
        'ko': '소재',
    },
    '历史': {
        'zh-TW': '歷史',
        'en': 'History',
        'ja': '履歴',
        'ko': '기록',
    },
    '打开': {
        'zh-TW': '開啟',
        'en': 'Opened',
        'ja': '開く',
        'ko': '열기',
    },
    '调整顺序': {
        'zh-TW': '調整順序',
        'en': 'Reorder',
        'ja': '順序を変更',
        'ko': '순서 조정',
    },
    '删除素材': {
        'zh-TW': '刪除素材',
        'en': 'Remove media',
        'ja': '素材を削除',
        'ko': '소재 삭제',
    },
    '改时长': {
        'zh-TW': '改時長',
        'en': 'Change duration',
        'ja': '長さを変更',
        'ko': '길이 변경',
    },
    '改节奏': {
        'zh-TW': '改節奏',
        'en': 'Change timing',
        'ja': 'タイミング変更',
        'ko': '타이밍 변경',
    },
    '加音频': {
        'zh-TW': '加音訊',
        'en': 'Add audio',
        'ja': '音声を追加',
        'ko': '오디오 추가',
    },
    '删音频': {
        'zh-TW': '刪音訊',
        'en': 'Remove audio',
        'ja': '音声を削除',
        'ko': '오디오 삭제',
    },
    '移音频': {
        'zh-TW': '移音訊',
        'en': 'Move audio',
        'ja': '音声を移動',
        'ko': '오디오 이동',
    },
    '改音量': {
        'zh-TW': '改音量',
        'en': 'Change volume',
        'ja': '音量を変更',
        'ko': '볼륨 변경',
    },
    '改设置': {
        'zh-TW': '改設定',
        'en': 'Change settings',
        'ja': '設定を変更',
        'ko': '설정 변경',
    },
    '换背景音乐': {
        'zh-TW': '換背景音樂',
        'en': 'Change BGM',
        'ja': 'BGM を変更',
        'ko': '배경 음악 변경',
    },
    '撤销上一步（Ctrl+Z）': {
        'zh-TW': '復原上一步（Ctrl+Z）',
        'en': 'Undo the last step (Ctrl+Z)',
        'ja': '直前の操作を取り消す（Ctrl+Z）',
        'ko': '이전 단계 실행 취소 (Ctrl+Z)',
    },
    '重做（Ctrl+Shift+Z）': {
        'zh-TW': '重做（Ctrl+Shift+Z）',
        'en': 'Redo (Ctrl+Shift+Z)',
        'ja': 'やり直し（Ctrl+Shift+Z）',
        'ko': '다시 실행 (Ctrl+Shift+Z)',
    },
}

for _k, _trs in _TR_EXTRA20.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)

_TR_EXTRA21 = {
    '快捷键': {
        'zh-TW': '快捷鍵',
        'en': 'Shortcuts',
        'ja': 'ショートカット',
        'ko': '단축키',
    },
    '恢复默认': {
        'zh-TW': '恢復預設',
        'en': 'Reset to defaults',
        'ja': '初期値に戻す',
        'ko': '기본값으로',
    },
    '快捷键默认': {
        'zh-TW': '快捷鍵預設',
        'en': 'Shortcut defaults',
        'ja': 'ショートカット初期値',
        'ko': '단축키 기본값',
    },
    '播放 / 暂停': {
        'zh-TW': '播放 / 暫停',
        'en': 'Play / Pause',
        'ja': '再生 / 一時停止',
        'ko': '재생 / 일시정지',
    },
    '默认照剪辑软件的惯例；点一下再按键就能改': {
        'zh-TW': '預設照剪輯軟體的慣例；點一下再按鍵就能改',
        'en': 'Defaults follow editing-software conventions; click a field and press keys to change',
        'ja': '既定は編集ソフトの慣例どおり。欄をクリックしてキーを押すと変更できます',
        'ko': '기본값은 편집 소프트웨어 관례를 따릅니다. 칸을 클릭하고 키를 누르면 바꿀 수 있습니다',
    },
    '快捷键冲突': {
        'zh-TW': '快捷鍵衝突',
        'en': 'Shortcut conflict',
        'ja': 'ショートカットの重複',
        'ko': '단축키 충돌',
    },
    '「{a}」和「{b}」都设成了 {key}，改一个再来。': {
        'zh-TW': '「{a}」和「{b}」都設成了 {key}，改一個再來。',
        'en': '"{a}" and "{b}" are both {key}. Change one and try again.',
        'ja': '「{a}」と「{b}」がどちらも {key} です。片方を変えてください。',
        'ko': "'{a}'와 '{b}'가 모두 {key}입니다. 하나를 바꾸세요.",
    },
}

for _k, _trs in _TR_EXTRA21.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)
_TR_EXTRA22 = {
    '替换素材': {
        'zh-TW': '替換素材',
        'en': 'Replace item',
        'ja': '素材を差し替え',
        'ko': '소재 바꾸기',
    },
    '已换成 {n} 张图': {
        'zh-TW': '已換成 {n} 張圖',
        'en': 'Replaced with {n} image(s)',
        'ja': '{n} 枚の画像に差し替えました',
        'ko': '{n}장의 이미지로 바꿨습니다',
    },
    '已插入 {n} 张图': {
        'zh-TW': '已插入 {n} 張圖',
        'en': 'Inserted {n} image(s)',
        'ja': '{n} 枚の画像を挿入しました',
        'ko': '{n}장의 이미지를 넣었습니다',
    },
}

for _k, _trs in _TR_EXTRA22.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)

_TR_EXTRA23 = {
    '画面铺满这条音频': {
        'zh-TW': '畫面鋪滿這條音訊',
        'en': 'Stretch this visual to the audio',
        'ja': 'この音声いっぱいに映像を合わせる',
        'ko': '이 오디오 길이에 화면 맞추기',
    },
    '铺满音频': {
        'zh-TW': '鋪滿音訊',
        'en': 'Fit to audio',
        'ja': '音声に合わせる',
        'ko': '오디오에 맞추기',
    },
}

for _k, _trs in _TR_EXTRA23.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)

_TR_EXTRA24 = {
    '跟素材总长': {
        'zh-TW': '跟素材總長',
        'en': 'Match footage length',
        'ja': '素材の長さに合わせる',
        'ko': '소재 길이에 맞추기',
    },
}

for _k, _trs in _TR_EXTRA24.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)


_TR_EXTRA25 = {
    ' 等 %d 个': {
        'zh-TW': ' 等 %d 個',
        'en': ' and %d more',
        'ja': ' ほか %d 個',
        'ko': ' 외 %d개',
    },
    '「%s」有改动还没保存，先存下来吗？': {
        'zh-TW': '「%s」有改動還沒儲存，先存下來嗎？',
        'en': '"%s" has unsaved changes - save it first?',
        'ja': '「%s」に未保存の変更があります。保存しますか？',
        'ko': '"%s"에 저장하지 않은 변경 사항이 있습니다. 저장할까요?',
    },
    '上次好像没正常退出': {
        'zh-TW': '上次好像沒有正常結束',
        'en': 'Looks like the app did not close properly last time',
        'ja': '前回は正常に終了しなかったようです',
        'ko': '지난번에 제대로 종료되지 않은 것 같습니다',
    },
    '下面这些素材不在了，成片里会缺画面：\n%s%s\n把它们放回原来的位置，或者重新导入。': {
        'zh-TW': '下面這些素材不見了，成片裡會缺畫面：\n%s%s\n把它們放回原來的位置，或重新匯入。',
        'en': 'These clips are missing, so the video will have gaps:\n%s%s\nPut them back where they were, or import them again.',
        'ja': '次の素材が見つかりません。完成動画に欠けが出ます：\n%s%s\n元の場所に戻すか、読み込み直してください。',
        'ko': '다음 소재를 찾을 수 없습니다. 완성 영상에 빈 화면이 생깁니다:\n%s%s\n원래 위치로 되돌리거나 다시 불러오세요.',
    },
    '不保存': {
        'zh-TW': '不儲存',
        'en': 'Don\'t save',
        'ja': '保存しない',
        'ko': '저장 안 함',
    },
    '不是 JSON，读不出来：': {
        'zh-TW': '不是 JSON，讀不出來：',
        'en': 'Not JSON - can\'t read it: ',
        'ja': 'JSON ではないため読み取れません：',
        'ko': 'JSON이 아니어서 읽을 수 없습니다: ',
    },
    '不要': {
        'zh-TW': '不要',
        'en': 'No',
        'ja': 'いらない',
        'ko': '아니요',
    },
    '保存': {
        'zh-TW': '儲存',
        'en': 'Save',
        'ja': '保存',
        'ko': '저장',
    },
    '保存失败': {
        'zh-TW': '儲存失敗',
        'en': 'Save failed',
        'ja': '保存に失敗しました',
        'ko': '저장 실패',
    },
    '保存工程': {
        'zh-TW': '儲存專案',
        'en': 'Save project',
        'ja': 'プロジェクトを保存',
        'ko': '프로젝트 저장',
    },
    '内容不像本软件的工程：': {
        'zh-TW': '內容不像本軟體的專案：',
        'en': 'Doesn\'t look like a project of this app: ',
        'ja': 'このアプリのプロジェクトではないようです：',
        'ko': '이 앱의 프로젝트가 아닌 것 같습니다: ',
    },
    '写不进去（磁盘满或者没有权限）：': {
        'zh-TW': '寫不進去（磁碟滿或沒有權限）：',
        'en': 'Can\'t write (disk full or no permission): ',
        'ja': '書き込めません（ディスク満杯か権限なし）：',
        'ko': '쓸 수 없습니다 (디스크 가득 참 또는 권한 없음): ',
    },
    '发现一份自动保存的工程（%s）。恢复它吗？': {
        'zh-TW': '發現一份自動儲存的專案（%s）。要恢復嗎？',
        'en': 'Found an auto-saved project (%s). Restore it?',
        'ja': '自動保存されたプロジェクト（%s）があります。復元しますか？',
        'ko': '자동 저장된 프로젝트(%s)를 찾았습니다. 복원할까요?',
    },
    '另存为…': {
        'zh-TW': '另存新檔…',
        'en': 'Save as…',
        'ja': '名前を付けて保存…',
        'ko': '다른 이름으로 저장…',
    },
    '工程': {
        'zh-TW': '專案',
        'en': 'Project',
        'ja': 'プロジェクト',
        'ko': '프로젝트',
    },
    '工程另存为': {
        'zh-TW': '專案另存新檔',
        'en': 'Save project as',
        'ja': 'プロジェクトを別名で保存',
        'ko': '프로젝트 다른 이름으로 저장',
    },
    '工程还没存': {
        'zh-TW': '專案還沒儲存',
        'en': 'Project isn\'t saved',
        'ja': 'プロジェクトが未保存です',
        'ko': '프로젝트가 저장되지 않았습니다',
    },
    '恢复': {
        'zh-TW': '恢復',
        'en': 'Restore',
        'ja': '復元',
        'ko': '복원',
    },
    '恢复失败': {
        'zh-TW': '恢復失敗',
        'en': 'Restore failed',
        'ja': '復元に失敗しました',
        'ko': '복원 실패',
    },
    '恢复自动保存': {
        'zh-TW': '恢復自動儲存',
        'en': 'Restored autosave',
        'ja': '自動保存を復元',
        'ko': '자동 저장 복원',
    },
    '打不开': {
        'zh-TW': '打不開',
        'en': 'Can\'t open',
        'ja': '開けません',
        'ko': '열 수 없습니다',
    },
    '打不开这个工程': {
        'zh-TW': '打不開這個專案',
        'en': 'Can\'t open this project',
        'ja': 'このプロジェクトを開けません',
        'ko': '이 프로젝트를 열 수 없습니다',
    },
    '打开工程': {
        'zh-TW': '開啟專案',
        'en': 'Open project',
        'ja': 'プロジェクトを開く',
        'ko': '프로젝트 열기',
    },
    '打开工程…': {
        'zh-TW': '開啟專案…',
        'en': 'Open project…',
        'ja': 'プロジェクトを開く…',
        'ko': '프로젝트 열기…',
    },
    '文件不在了：': {
        'zh-TW': '檔案不見了：',
        'en': 'File is gone: ',
        'ja': 'ファイルが見つかりません：',
        'ko': '파일이 없습니다: ',
    },
    '新建 / 打开 / 保存工程（Ctrl+S 保存）': {
        'zh-TW': '新增 / 開啟 / 儲存專案（Ctrl+S 儲存）',
        'en': 'New / Open / Save project (Ctrl+S saves)',
        'ja': '新規 / 開く / 保存（Ctrl+S で保存）',
        'ko': '새로 만들기 / 열기 / 저장 (Ctrl+S 저장)',
    },
    '新建工程': {
        'zh-TW': '新增專案',
        'en': 'New project',
        'ja': '新規プロジェクト',
        'ko': '새 프로젝트',
    },
    '最近打开': {
        'zh-TW': '最近開啟',
        'en': 'Recent',
        'ja': '最近開いたもの',
        'ko': '최근 항목',
    },
    '有素材找不到了': {
        'zh-TW': '有素材找不到',
        'en': 'Some clips are missing',
        'ja': '素材が見つかりません',
        'ko': '소재를 찾을 수 없습니다',
    },
    '未命名工程': {
        'zh-TW': '未命名專案',
        'en': 'Untitled project',
        'ja': '無題のプロジェクト',
        'ko': '이름 없는 프로젝트',
    },
    '这个工程文件已经不在了：\n%s': {
        'zh-TW': '這個專案檔已經不在了：\n%s',
        'en': 'That project file is gone:\n%s',
        'ja': 'このプロジェクトファイルはもうありません：\n%s',
        'ko': '이 프로젝트 파일이 더 이상 없습니다:\n%s',
    },
    '这个工程是更新版本的软件存的，先升级软件再打开。': {
        'zh-TW': '這個專案是更新版本的軟體存的，先升級再開啟。',
        'en': 'This project was saved by a newer version - update the app first.',
        'ja': 'このプロジェクトは新しいバージョンで保存されています。先にアプリを更新してください。',
        'ko': '이 프로젝트는 더 새로운 버전에서 저장되었습니다. 먼저 앱을 업데이트하세요.',
    },
    '（路径没记住）': {
        'zh-TW': '（路徑沒記住）',
        'en': '(path unknown)',
        'ja': '（パス不明）',
        'ko': '(경로 없음)',
    },
    '（还没有）': {
        'zh-TW': '（還沒有）',
        'en': '(none yet)',
        'ja': '（まだありません）',
        'ko': '（아직 없음）',
    },
}

for _k, _trs in _TR_EXTRA25.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)


_TR_EXTRA26 = {
    '重新定位…': {
        'zh-TW': '重新定位…',
        'en': 'Locate…',
        'ja': '再指定…',
        'ko': '위치 지정…',
    },
    '先不管': {
        'zh-TW': '先不管',
        'en': 'Not now',
        'ja': 'あとで',
        'ko': '나중에',
    },
    '同一类文件 (*%s)': {
        'zh-TW': '同類檔案 (*%s)',
        'en': 'Same kind (*%s)',
        'ja': '同じ種類 (*%s)',
        'ko': '같은 종류 (*%s)',
    },
    '重新定位：%s': {
        'zh-TW': '重新定位：%s',
        'en': 'Locate: %s',
        'ja': '再指定：%s',
        'ko': '위치 지정: %s',
    },
}

for _k, _trs in _TR_EXTRA26.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)

_TR_EXTRA27 = {
    '切一刀': {
        'zh-TW': '切一刀',
        'en': 'Split here',
        'ja': 'ここで分割',
        'ko': '여기서 분할',
    },
    '设入点': {
        'zh-TW': '設入點',
        'en': 'Set in point',
        'ja': 'イン点を設定',
        'ko': '인점 설정',
    },
    '设出点': {
        'zh-TW': '設出點',
        'en': 'Set out point',
        'ja': 'アウト点を設定',
        'ko': '아웃점 설정',
    },
    '改时长': {
        'zh-TW': '改時長',
        'en': 'Change duration',
        'ja': '長さを変更',
        'ko': '길이 변경',
    },
    '图片段没有入点，只有片头片尾能裁': {
        'zh-TW': '圖片段沒有入點，只有片頭片尾能裁',
        'en': 'Images have no in point; only intro/outro video can be trimmed',
        'ja': '画像にはイン点がありません（トリムできるのはOP/ED動画のみ）',
        'ko': '이미지에는 인점이 없습니다 (앞/뒤 영상만 자를 수 있음)',
    },
    '图片段没有出点，只有片头片尾能裁': {
        'zh-TW': '圖片段沒有出點，只有片頭片尾能裁',
        'en': 'Images have no out point; only intro/outro video can be trimmed',
        'ja': '画像にはアウト点がありません（トリムできるのはOP/ED動画のみ）',
        'ko': '이미지에는 아웃점이 없습니다 (앞/뒤 영상만 자를 수 있음)',
    },
    '这里切不了：离段首或段尾太近': {
        'zh-TW': '這裡切不了：離段首或段尾太近',
        'en': "Can't split here: too close to the start or end of a segment",
        'ja': 'ここでは分割できません（区間の端に近すぎます）',
        'ko': '여기서는 분할할 수 없습니다 (구간 끝에 너무 가까움)',
    },
}

for _k, _trs in _TR_EXTRA27.items():
    for _lang, _v in _trs.items():
        TR[_lang].setdefault(_k, _v)
