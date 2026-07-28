"""Disposable AppKit window used by the opt-in interactive macOS smoke test."""

from __future__ import annotations

import os
import sys

import AppKit  # type: ignore[import-not-found]
import objc  # type: ignore[import-not-found]
from Foundation import NSObject  # type: ignore[import-not-found]


WINDOW_TITLE = "ZCode Computer Use Live Smoke"


class FixtureHandler(NSObject):
    def initWithField_label_(self, field, label):
        self = objc.super(FixtureHandler, self).init()
        if self is None:
            return None
        self.field = field
        self.label = label
        return self

    @objc.IBAction
    def submit_(self, _sender) -> None:
        self.label.setStringValue_(f"Received: {self.field.stringValue()}")


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
    result = label(AppKit.NSMakeRect(40, 55, 560, 28), "Waiting")
    result.setAccessibilityLabel_("Smoke result")

    handler = FixtureHandler.alloc().initWithField_label_(field, result)
    button.setTarget_(handler)
    button.setAction_("submit:")
    for control in (prompt, field, button, result):
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
