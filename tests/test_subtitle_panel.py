# -*- coding: utf-8 -*-
"""字幕面板冒烟：离屏把窗口起起来，真点一遍字幕页（阶段 3）。

不是单元测试的替代：这里验的是「控件接上线了」——列表、属性区、五项编辑、
撤销、时间线信号。数据层的规则在 test_project.py / test_subtitle_style.py。

离屏跑要设 QT_QPA_PLATFORM=offscreen；窗口建好就把 QApplication.exec 短路掉，
不进事件循环。跑完会把 settings.json 还原，不会把你的界面设置改掉。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, ROOT)

try:
    import pic2video as p
    from PyQt6.QtWidgets import QApplication
except Exception as exc:                                   # noqa: BLE001
    print("SKIP 没有 PyQt6 或导入失败：%s" % exc)
    sys.exit(0)


def main() -> int:
    fails = []

    def check(name, cond, extra=""):
        print(("PASS " if cond else "FAIL ") + name
              + (("  " + str(extra)) if extra else ""))
        if not cond:
            fails.append(name)

    # 界面设置会被窗口读写，先备份，跑完还原
    spath = p.SETTINGS_PATH
    backup = None
    if os.path.isfile(spath):
        with open(spath, "rb") as fh:
            backup = fh.read()

    keep = {}

    def fake_exec(self):
        keep["app"] = self
        for wid in QApplication.topLevelWidgets():
            if wid.__class__.__name__ == "Window":
                keep["win"] = wid
        return 0

    old_exec = QApplication.exec
    QApplication.exec = fake_exec
    try:
        rc = p.run_gui()
        w = keep.get("win")
        check("窗口起得来", w is not None and rc == 0, (rc, w))
        if w is None:
            return 1

        w.project.add_images([os.path.join(FIXTURES, "land.png")] * 2)
        p.set_mode(w.project, "free")
        for i in range(2):
            w.project.set_duration(i, 4.0)
        w._refresh_all()
        w.project.add_cue(1.0)
        w._refresh_subtitles()
        check("新增后列表有一行", w.sub_list.count() == 1, w.sub_list.count())

        w.sub_list.setCurrentRow(0)
        check("入出点填进数值框",
              abs(w.sub_in.value() - 1.0) < 0.01
              and abs(w.sub_out.value() - 3.0) < 0.01,
              (w.sub_in.value(), w.sub_out.value()))

        w.sub_text.setPlainText("你好字幕")
        check("改字写回数据", w.project.cues[0]["text"] == "你好字幕",
              w.project.cues[0]["text"])

        w.sub_size.setValue(72)
        check("改字号记成差异",
              w.project.cues[0]["overrides"].get("size") == 72
              and "已改" in w.lbl_sub_ovr.text(),
              (w.project.cues[0]["overrides"], w.lbl_sub_ovr.text()))
        w.on_sub_reset_style()
        check("还原样式清空差异", w.project.cues[0]["overrides"] == {},
              w.project.cues[0]["overrides"])

        w.sub_in.setValue(0.0)
        w.on_sub_span_edited()
        check("数值框改入点生效", abs(w.project.cues[0]["start"]) < 1e-6,
              w.project.cues[0]["start"])

        w.timeline.play_pos = 1.0
        w.on_sub_split()
        check("播放头拆分成功", len(w.project.cues) == 2, len(w.project.cues))
        w.sub_list.setCurrentRow(0)
        w.on_sub_merge()
        check("合并回一条", len(w.project.cues) == 1, len(w.project.cues))

        w.on_sub_add()
        check("再新增一条并选中",
              len(w.project.cues) == 2 and w.sub_list.currentRow() >= 0,
              (len(w.project.cues), w.sub_list.currentRow()))
        w.on_sub_del()
        check("删除生效", len(w.project.cues) == 1, len(w.project.cues))

        # 撤销：两次编辑各记一步（1.2 秒内的同类操作本来会合并，这里错开）
        w.sub_list.setCurrentRow(0)
        w.sub_text.setPlainText("第一版")
        w._sub_history("改字幕文字", debounce=False)
        w._sub_flush_history()
        w.history[-1]["t"] -= 2.0
        w.sub_text.setPlainText("第二版")
        w._sub_flush_history()
        check("两次编辑进历史", w.project.cues[0]["text"] == "第二版",
              w.project.cues[0]["text"])
        w.on_undo()
        check("撤销回上一版", w.project.cues[0]["text"] == "第一版",
              w.project.cues[0]["text"])
        w.on_redo()
        check("重做回第二版", w.project.cues[0]["text"] == "第二版",
              w.project.cues[0]["text"])

        # 时间线的三个信号都要接到 App 上（拖动中只刷预览，松手才记一步）
        w.timeline.subtitleDragged.emit()
        w.timeline.subtitleTrimmed.emit(0)
        check("拖端松手记一步", w.history[-1]["label"] == "改字幕时间",
              w.history[-1]["label"])
        w.timeline.subtitleMoved.emit(0, w.project.cues[0]["start"])
        check("整句挪松手也记一步", w.history[-1]["label"] == "改字幕时间",
              w.history[-1]["label"])

        w.on_sub_action("add")
        check("快捷键新增可用", len(w.project.cues) == 2, len(w.project.cues))
        w.on_sub_action("delete")
        check("快捷键删除可用", len(w.project.cues) == 1, len(w.project.cues))
    finally:
        QApplication.exec = old_exec
        if backup is not None:
            try:
                with open(spath, "wb") as fh:
                    fh.write(backup)
            except OSError:
                pass

    print("")
    if fails:
        print("RESULT: %d FAILED — %s" % (len(fails), ", ".join(fails)))
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())