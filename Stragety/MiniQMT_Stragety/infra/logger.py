# -*- coding: utf-8 -*-
"""
infra/logger.py — 文件日志系统
===============================
FileLogger: 双写 (控制台 + 文件), 文件名含时间戳, 每次 flush
_log()    : 全局日志函数, 依赖 FileLogger 实例
"""
import os
import sys
from datetime import datetime


class FileLogger:
    """
    日志系统: 同时写入控制台和文件。

    特性:
      - 文件名: DayTradeing_v15_{stock_code}_{YYYYMMDD_HHMMSS}.log
      - 每次 _log() 调用后立即 flush, 崩溃不丢日志
      - 日志目录自动创建
      - UTF-8 编码
    """

    def __init__(self, stock_code='601869', log_dir=None, version='v15'):
        self.stock_code = stock_code
        self.version = version

        if log_dir is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(script_dir, '..', 'logs')

        self.log_dir = os.path.abspath(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        start_time = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_filename = f'DayTradeing_{version}_{stock_code}_{start_time}.log'
        self.log_path = os.path.join(self.log_dir, self.log_filename)

        self._file = open(self.log_path, 'w', encoding='utf-8')
        self._write_header()
        self.start_time = start_time

    def _write_header(self):
        self._file.write(f'{"="*60}\n')
        self._file.write(f'  日志文件创建: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        self._file.write(f'  策略版本: {self.version} (for MiniQMT Evn)\n')
        self._file.write(f'  标的: {self.stock_code}\n')
        self._file.write(f'{"="*60}\n')
        self._file.flush()

    def write(self, *args, sep=' ', end='\n'):
        """写入日志 (控制台 + 文件), 用法与 print() 一致"""
        msg = sep.join(str(a) for a in args) + end

        # 控制台 (TTY 自动行缓冲, 不手动 flush)
        sys.stdout.write(msg)

        # 文件: 立即 flush 确保崩溃不丢日志
        try:
            self._file.write(msg)
            self._file.flush()
        except Exception:
            pass

    def close(self):
        """关闭日志文件"""
        try:
            self._file.write(f'\n{"="*60}\n')
            self._file.write(f'  日志结束: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
            self._file.write(f'{"="*60}\n')
            self._file.flush()
            self._file.close()
        except Exception:
            pass


# ============================================================================
# 全局日志函数
# ============================================================================

_logger_instance: 'FileLogger | None' = None


def set_logger(logger: FileLogger):
    """注册全局日志实例"""
    global _logger_instance
    _logger_instance = logger


def get_logger() -> 'FileLogger | None':
    return _logger_instance


def _log(*args):
    """
    全局日志输出 — 自动添加 [HH:MM:SS] 时间戳前缀。
    若未初始化 FileLogger 则回退到 print()。
    """
    from core.config import ts_prefix
    ts = ts_prefix()

    if args:
        msg = f'{ts} {args[0]}'
        extra = args[1:]
        if extra:
            msg += ' ' + ' '.join(str(a) for a in extra)
    else:
        msg = ''

    if _logger_instance is not None:
        _logger_instance.write(msg)
    else:
        print(msg)
