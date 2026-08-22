#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出错定位日志（极简）。

用途：脚本出错时把异常与堆栈写进临时目录的日志文件，便于定位原因。
正常执行不产生任何文件。日志按项目根隔离，避免跨项目冲突。
"""

import sys
import hashlib
import tempfile
import traceback
from pathlib import Path


def log_file(root) -> Path:
    r = Path(root).resolve()
    tag = hashlib.sha1(str(r).encode("utf-8")).hexdigest()[:8]
    safe = r.name.replace("\\", "_").replace("/", "_") or "root"
    return Path(tempfile.gettempdir()) / ("llm_wiki_%s_%s.log" % (safe, tag))


def dump_error(root, exc: BaseException) -> None:
    """把异常与堆栈追加到临时日志（仅出错时调用）。"""
    try:
        with log_file(root).open("a", encoding="utf-8") as f:
            f.write("\n=== %s: %s ===\n%s\n" % (type(exc).__name__, exc,
                                                 traceback.format_exc()))
    except OSError as e:
        sys.stderr.write("[wiki_log] 无法写日志 %s: %s\n" % (log_file(root), e))
