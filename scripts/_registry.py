# -*- coding: utf-8 -*-
"""
Shared registry for watcher-side handlers. IronPython 2.7 — keep simple.

Handler signature:
    def fn(args_dict) -> result_dict
Raise on error; the dispatcher in watcher.py converts exceptions to error results.
"""

HANDLERS = {}


def handler(op):
    def _wrap(fn):
        if op in HANDLERS:
            raise RuntimeError("duplicate handler registration: " + op)
        HANDLERS[op] = fn
        return fn

    return _wrap


def find(op):
    return HANDLERS.get(op)


def names():
    keys = list(HANDLERS.keys())
    keys.sort()
    return keys
