"""CLI entry point: `fnb58-log` or `python -m fnb58_logger`."""

from __future__ import annotations

import argparse
import signal
import sys
from contextlib import ExitStack

from . import __version__
from .logger import CsvSink, StatusLine, run_logger
from .transport import TransportError, open_transport


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fnb58-log",
        description=(
            "Log readings from a FNIRSI FNB58 (or FNB48/FNB48S/C1) USB power meter. "
            "Writes CSV to a file when -o is given, otherwise to stdout."
        ),
    )
    p.add_argument("-o", "--output", help="CSV output file (default: stdout)")
    p.add_argument(
        "--backend",
        choices=("auto", "hid", "pyusb"),
        default="auto",
        help="USB backend (default: auto — tries hid first, then pyusb)",
    )
    p.add_argument(
        "-d", "--duration", type=float, default=None,
        help="Stop after N seconds (default: run until Ctrl-C)",
    )
    p.add_argument(
        "--alpha", type=float, default=0.9,
        help="Temperature EMA smoothing factor 0<alpha<=1 (default: 0.9)",
    )
    p.add_argument(
        "--crc", action="store_true",
        help="Verify the CRC-8 of each incoming frame (default: off)",
    )
    p.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress the live status line",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print device details and warnings to stderr",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _install_sigint_handler() -> None:
    # Default handler raises KeyboardInterrupt; be explicit so nested loops behave.
    def handler(signum, frame):  # pragma: no cover
        raise KeyboardInterrupt
    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, AttributeError):  # pragma: no cover
        pass


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not (0.0 < args.alpha <= 1.0):
        print("error: --alpha must be in (0, 1]", file=sys.stderr)
        return 2

    _install_sigint_handler()

    def warn(msg: str) -> None:
        if args.verbose:
            print(f"[warn] {msg}", file=sys.stderr)

    with ExitStack() as stack:
        try:
            transport = open_transport(args.backend)
        except TransportError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        stack.callback(transport.close)

        if args.verbose:
            info = transport.info
            print(
                f"[info] opened {info.model.value} ({info.vid:04x}:{info.pid:04x})",
                file=sys.stderr,
            )

        # Output stream: stdout if no file, else file.
        writing_to_file = bool(args.output)
        if writing_to_file:
            out_stream = open(args.output, "w", newline="", encoding="utf-8")
            stack.callback(out_stream.close)
        else:
            out_stream = sys.stdout

        sink = CsvSink(out_stream, close_stream=False)
        stack.callback(sink.close)

        # Status line goes to stderr, and only when not quiet AND we're writing CSV
        # to a file (otherwise stdout is busy with CSV and stderr might not be a tty).
        status: StatusLine | None = None
        if writing_to_file and not args.quiet:
            status = StatusLine(sys.stderr)
            stack.callback(status.finalize)

        try:
            count = run_logger(
                transport,
                sink,
                status=status,
                verify_crc=args.crc,
                duration_s=args.duration,
                alpha=args.alpha,
                on_warn=warn,
            )
        except KeyboardInterrupt:
            count = -1  # signal clean interrupt
        except TransportError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    if args.verbose:
        if count >= 0:
            print(f"[info] {count} samples written", file=sys.stderr)
        else:
            print("[info] interrupted", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
