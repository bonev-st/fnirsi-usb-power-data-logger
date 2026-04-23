"""CLI entry point: `fnb58-log` or `python -m fnb58_logger`."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import ExitStack

from . import __version__
from .logger import CsvSink, StatusLine, SummaryAccumulator, run_logger
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
    p.add_argument(
        "--summary", action="store_true",
        help=(
            "After the run, print a one-line JSON summary "
            "(mean/median/p95 current, energy, charge, etc.) to stderr — or to "
            "stdout if CSV is being written to a file."
        ),
    )
    p.add_argument(
        "--marker-port",
        help=(
            "Serial port to read firmware state markers from "
            "(e.g. COM5 on Windows, /dev/ttyUSB0 on Linux). Lines matching "
            "'#STATE:NAME' update the current state, which is written to the "
            "'state' CSV column. Requires pyserial."
        ),
    )
    p.add_argument(
        "--marker-baud", type=int, default=115200,
        help="Baud rate for --marker-port (default: 115200)",
    )
    p.add_argument(
        "--refresh-ms", type=float, default=None,
        help=(
            "REFRESH write cadence in ms. Default: 1000 for FNB58/FNB48S, "
            "3 for FNB48/C1. Override only if you know what you're doing."
        ),
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

        summary = SummaryAccumulator() if args.summary else None

        # Optional UART state-marker reader.
        state_provider = None
        if args.marker_port:
            try:
                from .markers import MarkerReader
            except ImportError as e:  # pragma: no cover
                print(f"error: {e}", file=sys.stderr)
                return 1
            try:
                mr = MarkerReader(args.marker_port, baud=args.marker_baud, on_warn=warn)
                mr.start()
            except Exception as e:
                print(f"error: failed to open marker port {args.marker_port}: {e}",
                      file=sys.stderr)
                return 1
            stack.callback(mr.stop)
            state_provider = mr.current_state

        try:
            count = run_logger(
                transport,
                sink,
                status=status,
                verify_crc=args.crc,
                duration_s=args.duration,
                on_warn=warn,
                summary=summary,
                state_provider=state_provider,
                refresh_period_s=(
                    args.refresh_ms / 1000.0 if args.refresh_ms is not None else None
                ),
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

    if summary is not None:
        # CSV goes to stdout when -o is absent, so put the summary on stderr
        # there; when CSV goes to a file, stdout is free for JSON.
        out = sys.stdout if writing_to_file else sys.stderr
        out.write(json.dumps(summary.to_dict(), separators=(",", ":")) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
