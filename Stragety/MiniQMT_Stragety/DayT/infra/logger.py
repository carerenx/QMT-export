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


CONSOLE_GRAY = '\033[90m'
CONSOLE_RESET = '\033[0m'


def _write_windows_gray(stream, message):
    """Use the native Windows console color API; return False if unavailable."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class ConsoleScreenBufferInfo(ctypes.Structure):
            _fields_ = [
                ('size', wintypes._COORD),
                ('cursor_position', wintypes._COORD),
                ('attributes', wintypes.WORD),
                ('window', wintypes.SMALL_RECT),
                ('maximum_window_size', wintypes._COORD),
            ]

        handle = msvcrt.get_osfhandle(stream.fileno())
        info = ConsoleScreenBufferInfo()
        kernel32 = ctypes.windll.kernel32
        if not kernel32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
            return False
        if not kernel32.SetConsoleTextAttribute(handle, 8):
            return False
        try:
            try:
                stream.write(message)
            except UnicodeEncodeError:
                encoding = stream.encoding or 'utf-8'
                stream.write(message.encode(
                    encoding, errors='replace').decode(encoding))
        finally:
            kernel32.SetConsoleTextAttribute(handle, info.attributes)
        return True
    except (AttributeError, ImportError, OSError, ValueError):
        return False


def _write_gray_stream(stream, message):
    """Write gray to a real terminal and plain text to redirected streams."""
    is_terminal = bool(getattr(stream, 'isatty', lambda: False)())
    if not is_terminal:
        stream.write(message)
        return
    if os.name == 'nt' and _write_windows_gray(stream, message):
        return
    colored = CONSOLE_GRAY + message + CONSOLE_RESET
    try:
        stream.write(colored)
    except UnicodeEncodeError:
        encoding = stream.encoding or 'utf-8'
        stream.write(colored.encode(
            encoding, errors='replace').decode(encoding))


def _write_gray_console(message):
    _write_gray_stream(sys.stdout, message)


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
        self._file_error_reported = False
        self._write_header()
        self.start_time = start_time

    def _write_header(self):
        self._file.write(f'{"="*60}\n')
        self._file.write(f'  日志文件创建: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        self._file.write(f'  策略版本: {self.version} (for MiniQMT Evn)\n')
        self._file.write(f'  标的: {self.stock_code}\n')
        self._file.write(f'{"="*60}\n')
        self._file.flush()

    def write(self, *args, sep=' ', end='\n', console=True):
        """写入日志；默认双写，也可仅写文件。"""
        msg = sep.join(str(a) for a in args) + end

        if console:
            # 控制台 (TTY 自动行缓冲, 不手动 flush)
            _write_gray_console(msg)

        # 文件: 立即 flush 确保崩溃不丢日志
        try:
            self._file.write(msg)
            self._file.flush()
            if self._file_error_reported:
                _write_gray_stream(
                    sys.stderr,
                    '[LOGGER-FILE-RECOVERED] file logging resumed\n')
                self._file_error_reported = False
        except Exception as error:
            if not self._file_error_reported:
                _write_gray_stream(
                    sys.stderr,
                    '[LOGGER-FILE-ERROR] {}; messages may be missing from {}\n'.format(
                        error, self.log_path))
                self._file_error_reported = True

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


def _format_message(args):
    from core.config import ts_prefix
    ts = ts_prefix()
    if not args:
        return ''
    separator = '' if str(args[0]).startswith('[') else ' '
    msg = f'{ts}{separator}{args[0]}'
    if args[1:]:
        msg += ' ' + ' '.join(str(a) for a in args[1:])
    return msg


def _write_log(args, console):
    msg = _format_message(args)
    if _logger_instance is not None:
        _logger_instance.write(msg, console=console)
    else:
        _write_gray_console(msg + '\n')


def _log(*args):
    """
    全局日志输出 — 自动添加 [HH:MM:SS] 时间戳前缀。
    若未初始化 FileLogger 则回退到 print()。
    """
    _write_log(args, console=True)


def _log_file_only(*args):
    """带时间戳写入日志文件，不在终端打印。"""
    _write_log(args, console=False)
