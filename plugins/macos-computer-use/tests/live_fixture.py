"""Disposable AppKit window used by the opt-in interactive macOS smoke test."""

from __future__ import annotations

import os
import sys

import AppKit  # type: ignore[import-not-found]
import objc  # type: ignore[import-not-found]
from Foundation import NSObject  # type: ignore[import-not-found]


WINDOW_TITLE = "ZCode Computer Use Live Smoke"


class FixtureHandler(NSObject):
    def initWithField_label_sliderLabel_(self, field, label, slider_label):
        self = objc.super(FixtureHandler, self).init()
        if self is None:
            return None
        self.field = field
        self.label = label
        self.slider_label = slider_label
        return self

    @objc.IBAction
    def submit_(self, _sender) -> None:
        self.label.setStringValue_(f"Received: {self.field.stringValue()}")

    @objc.IBAction
    def sliderChanged_(self, sender) -> None:
        self.slider_label.setStringValue_(f"Slider: {int(round(sender.doubleValue()))}")


class ScrollProbeView(AppKit.NSView):
    def initWithFrame_statusLabel_(self, frame, status_label):
        self = objc.super(ScrollProbeView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.status_label = status_label
        self.total_scroll = 0
        self.setAccessibilityLabel_("Scroll probe")
        return self

    def drawRect_(self, _dirty_rect) -> None:
        AppKit.NSColor.systemBlueColor().setFill()
        AppKit.NSBezierPath.fillRect_(self.bounds())

    def scrollWheel_(self, event) -> None:
        self.total_scroll += max(1, int(round(abs(event.scrollingDeltaY()))))
        self.status_label.setStringValue_(f"Scrolled: {self.total_scroll}")


def label(frame, value: str):
    control = AppKit.NSTextField.alloc().initWithFrame_(frame)
    control.setStringValue_(value)
    control.setBezeled_(False)
    control.setDrawsBackground_(False)
    control.setEditable_(False)
    control.setSelectable_(False)
    return control


def main() -> int:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    style = (
        AppKit.NSWindowStyleMaskTitled
        | AppKit.NSWindowStyleMaskClosable
        | AppKit.NSWindowStyleMaskMiniaturizable
    )
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        AppKit.NSMakeRect(180, 180, 640, 300),
        style,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    window.setTitle_(WINDOW_TITLE)
    window.setReleasedWhenClosed_(False)

    content = window.contentView()
    prompt = label(AppKit.NSMakeRect(40, 230, 560, 24), "Type into the field, then press Copy value")
    field = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(40, 170, 560, 32))
    field.setAccessibilityLabel_("Smoke input")
    button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(40, 105, 150, 34))
    button.setTitle_("Copy value")
    button.setBezelStyle_(AppKit.NSBezelStyleRounded)
    slider = AppKit.NSSlider.alloc().initWithFrame_(AppKit.NSMakeRect(240, 105, 360, 34))
    slider.setMinValue_(0)
    slider.setMaxValue_(100)
    slider.setDoubleValue_(0)
    slider.setContinuous_(True)
    slider.setAccessibilityLabel_("Smoke slider")
    slider_result = label(AppKit.NSMakeRect(240, 75, 360, 24), "Slider: 0")
    slider_result.setAccessibilityLabel_("Smoke slider result")
    result = label(AppKit.NSMakeRect(40, 45, 190, 24), "Waiting")
    result.setAccessibilityLabel_("Smoke result")
    scroll_result = label(AppKit.NSMakeRect(240, 45, 170, 24), "Scrolled: 0")
    scroll_result.setAccessibilityLabel_("Smoke scroll result")
    scroll_probe = ScrollProbeView.alloc().initWithFrame_statusLabel_(
        AppKit.NSMakeRect(420, 45, 180, 24),
        scroll_result,
    )

    handler = FixtureHandler.alloc().initWithField_label_sliderLabel_(field, result, slider_result)
    button.setTarget_(handler)
    button.setAction_("submit:")
    slider.setTarget_(handler)
    slider.setAction_("sliderChanged:")
    for control in (
        prompt,
        field,
        button,
        slider,
        slider_result,
        result,
        scroll_result,
        scroll_probe,
    ):
        content.addSubview_(control)

    app.finishLaunching()
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    print(f"READY {os.getpid()} {WINDOW_TITLE}", flush=True)
    app.run()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FIXTURE_ERROR {error}", file=sys.stderr, flush=True)
        raise
