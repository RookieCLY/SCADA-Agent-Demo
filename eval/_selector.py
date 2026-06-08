"""Terminal selection helpers for the interactive runner.

`select_from_list` shows an arrow-key menu (↑/↓ + Enter) when stdin is a real
interactive terminal, and transparently falls back to a numbered prompt when it
is not — piped input, `--command`, or the test suite. The fallback reads a line
from the supplied input stream, so the whole runner stays scriptable.
"""
from __future__ import annotations

import sys
from typing import TextIO

try:  # Windows console key input
	import msvcrt
except ImportError:  # pragma: no cover - non-Windows
	msvcrt = None

try:  # POSIX raw terminal input
	import termios
	import tty
except ImportError:  # pragma: no cover - non-POSIX
	termios = None
	tty = None


def select_from_list(
	out: TextIO,
	inp: TextIO,
	title: str,
	options: list[str],
	current: int = 0,
) -> int | None:
	"""Return the index the user picked, or None if they cancelled.

	Uses an arrow-key menu on an interactive TTY and a numbered prompt
	otherwise. Any failure setting up the raw terminal falls back to the
	numbered prompt rather than raising.
	"""
	if not options:
		return None
	if _can_use_arrow_keys(inp, out):
		try:
			return _arrow_select(out, title, options, current)
		except Exception:
			# Terminal manipulation failed mid-flight; degrade gracefully.
			pass
	return _numbered_select(out, inp, title, options, current)


# --------------------------------------------------------------------------- #
# Numbered fallback (portable, used by tests and piped input)
# --------------------------------------------------------------------------- #
def _numbered_select(
	out: TextIO,
	inp: TextIO,
	title: str,
	options: list[str],
	current: int,
) -> int | None:
	_write(out, title)
	for i, label in enumerate(options):
		marker = "*" if i == current else " "
		_write(out, f"  [{marker}] {i + 1}. {label}")
	while True:
		raw = _read_line(inp, out, f"Select [1-{len(options)}], blank to cancel: ").strip()
		if not raw:
			return None
		if raw.isdigit() and 1 <= int(raw) <= len(options):
			return int(raw) - 1
		_write(out, f"Invalid selection: {raw!r}. Enter a number between 1 and {len(options)}.")


def _read_line(inp: TextIO, out: TextIO, prompt: str) -> str:
	if inp is sys.stdin:
		try:
			return input(prompt)
		except (EOFError, KeyboardInterrupt):
			_write(out, "")
			return ""
	out.write(prompt)
	out.flush()
	return inp.readline()


# --------------------------------------------------------------------------- #
# Arrow-key menu (interactive TTY only)
# --------------------------------------------------------------------------- #
def _arrow_select(out: TextIO, title: str, options: list[str], current: int) -> int | None:
	_write(out, title)
	_write(out, "  (↑/↓ to move · Enter to select · Esc to cancel)")
	selected = current if 0 <= current < len(options) else 0
	out.write("\x1b[?25l")  # hide cursor
	out.flush()
	try:
		_paint(out, options, selected)
		while True:
			try:
				key = _read_key()
			except KeyboardInterrupt:
				return None
			if key == "up":
				selected = (selected - 1) % len(options)
			elif key == "down":
				selected = (selected + 1) % len(options)
			elif key == "enter":
				return selected
			elif key in {"esc", "q"}:
				return None
			else:
				continue
			out.write(f"\x1b[{len(options)}A")  # back to first option line
			_paint(out, options, selected)
	finally:
		out.write("\x1b[?25h")  # restore cursor
		out.flush()


def _paint(out: TextIO, options: list[str], selected: int) -> None:
	for i, label in enumerate(options):
		if i == selected:
			out.write(f"\r\x1b[2K\x1b[7m › {label} \x1b[0m\n")
		else:
			out.write(f"\r\x1b[2K   {label}\n")
	out.flush()


def _can_use_arrow_keys(inp: TextIO, out: TextIO) -> bool:
	if inp is not sys.stdin:
		return False
	if msvcrt is None and termios is None:
		return False
	try:
		if not sys.stdin.isatty():
			return False
		out_isatty = getattr(out, "isatty", None)
		if callable(out_isatty) and not out_isatty():
			return False
	except Exception:
		return False
	return _enable_windows_vt()


def _enable_windows_vt() -> bool:
	"""Enable ANSI escape processing on the Windows console; no-op elsewhere."""
	if msvcrt is None:
		return True
	try:
		import ctypes

		kernel32 = ctypes.windll.kernel32
		handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
		mode = ctypes.c_uint()
		if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
			return False
		enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
		return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
	except Exception:
		return False


def _read_key() -> str:
	"""Block for one keypress and return a normalized name.

	Returns one of: 'up', 'down', 'left', 'right', 'enter', 'esc', or the raw
	character for anything else.
	"""
	if msvcrt is not None:
		return _read_key_windows()
	if termios is not None:
		return _read_key_unix()
	return "esc"  # pragma: no cover - no key backend available


def _read_key_windows() -> str:
	ch = msvcrt.getwch()
	if ch in ("\x00", "\xe0"):  # arrow / function key prefix
		code = msvcrt.getwch()
		return {"H": "up", "P": "down", "K": "left", "M": "right"}.get(code, "")
	if ch in ("\r", "\n"):
		return "enter"
	if ch == "\x1b":
		return "esc"
	if ch == "\x03":
		raise KeyboardInterrupt
	return ch


def _read_key_unix() -> str:  # pragma: no cover - exercised only on POSIX TTYs
	import select

	fd = sys.stdin.fileno()
	old = termios.tcgetattr(fd)
	try:
		tty.setraw(fd)
		ch = sys.stdin.read(1)
		if ch == "\x1b":
			ready, _, _ = select.select([sys.stdin], [], [], 0.05)
			if not ready or sys.stdin.read(1) != "[":
				return "esc"
			code = sys.stdin.read(1)
			return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(code, "")
		if ch in ("\r", "\n"):
			return "enter"
		if ch == "\x03":
			raise KeyboardInterrupt
		return ch
	finally:
		termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _write(out: TextIO, text: str) -> None:
	out.write(text + "\n")
	out.flush()
