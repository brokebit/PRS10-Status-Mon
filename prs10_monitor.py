#!/usr/bin/env python3
"""PRS10 Rubidium Frequency Standard monitor.

Usage: python3 prs10_monitor.py <device> [interval]
  device   - serial port path (e.g. /dev/ttyUSB0 or /dev/cu.usbserial-1410)
  interval - update interval in seconds (default: 5)
"""

import argparse
import sys
import time
import serial

# ── Status byte definitions ────────────────────────────────────────────────────

ST1_BITS = [
    "+24V electronics < +22 V (low)",
    "+24V electronics > +30 V (high)",
    "+24V heaters < +22 V (low)",
    "+24V heaters > +30 V (high)",
    "Lamp light level too low",
    "Lamp light level too high",
    "Gate voltage too low",
    "Gate voltage too high",
]

ST2_BITS = [
    "RF synthesizer PLL unlocked",
    "RF crystal varactor too low",
    "RF crystal varactor too high",
    "RF VCO control too low",
    "RF VCO control too high",
    "RF AGC control too low",
    "RF AGC control too high",
    "Bad PLL parameter",
]

ST3_BITS = [
    "Lamp temp below set point",
    "Lamp temp above set point",
    "Crystal temp below set point",
    "Crystal temp above set point",
    "Cell temp below set point",
    "Cell temp above set point",
    "Case temperature too low",
    "Case temperature too high",
]

ST4_BITS = [
    "Frequency lock control is off",
    "Frequency lock is disabled",
    "10 MHz EFC too high",
    "10 MHz EFC too low",
    "Analog cal voltage > 4.9 V",
    "Analog cal voltage < 0.1 V",
    "",
    "",
]

ST5_BITS = [
    "1pps PLL disabled",
    "< 256 good 1pps inputs received",
    "1pps PLL active",
    "> 256 bad 1pps inputs",
    "Excessive time interval",
    "1pps PLL restarted",
    "Frequency control saturated",
    "No 1pps input",
]

ST6_BITS = [
    "Lamp restart",
    "Watchdog timeout and reset",
    "Bad interrupt vector",
    "EEPROM write failure",
    "EEPROM data corruption",
    "Bad command syntax",
    "Bad command parameter",
    "Unit has been reset",
]

STATUS_BYTES = [
    ("ST1", "Power Supplies / Lamp",     ST1_BITS),
    ("ST2", "RF Synthesizer",            ST2_BITS),
    ("ST3", "Temperature Controllers",   ST3_BITS),
    ("ST4", "Frequency Lock-Loop",       ST4_BITS),
    ("ST5", "External 1pps Lock",        ST5_BITS),
    ("ST6", "System Events",             ST6_BITS),
]

# ── Serial helpers ─────────────────────────────────────────────────────────────

def send_command(port: serial.Serial, cmd: str) -> str:
    """Send a command and return the response line (without CR)."""
    port.write((cmd + "\r").encode())
    response = port.readline().decode(errors="replace").strip()
    return response


def query(port: serial.Serial, cmd: str) -> str | None:
    """Send a query command and return the response, or None on timeout/error."""
    try:
        return send_command(port, cmd)
    except serial.SerialException as exc:
        print(f"  [serial error: {exc}]")
        return None

# ── Display helpers ────────────────────────────────────────────────────────────

def parse_status(raw: str) -> list[int] | None:
    """Parse ST? response '16,3,21,1,2,129' into a list of 6 ints."""
    try:
        values = [int(v.strip()) for v in raw.split(",")]
        if len(values) == 6:
            return values
    except ValueError:
        pass
    return None


def format_status_bytes(values: list[int]) -> str:
    lines = []
    any_flag = False
    for (label, desc, bits), val in zip(STATUS_BYTES, values):
        set_bits = [bits[i] for i in range(8) if (val >> i) & 1 and bits[i]]
        if set_bits:
            any_flag = True
            lines.append(f"  {label} ({desc}): {val}")
            for b in set_bits:
                lines.append(f"    ⚑  {b}")
        else:
            lines.append(f"  {label} ({desc}): {val}  [OK]")
    return "\n".join(lines)


def print_update(port: serial.Serial, identifier: str) -> None:
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    width = 60
    print("=" * width)
    print(f"  PRS10  {identifier}   {now}")
    print("=" * width)

    # Lock status
    lo = query(port, "LO?")
    locked = lo == "1" if lo is not None else None
    lock_str = "LOCKED" if locked else ("NOT LOCKED" if locked is not None else "?")
    print(f"  Rb Lock:      {lock_str}")

    # Frequency control
    fc = query(port, "FC?")
    if fc:
        print(f"  FC (hi,lo):   {fc}")

    # Case temperature  AD10 returns volts at 10 mV/°C
    ad10 = query(port, "AD10?")
    if ad10:
        try:
            temp_c = float(ad10) * 100.0   # 10 mV/°C → V * 100 = °C
            print(f"  Case temp:    {temp_c:.1f} °C  (raw {ad10} V)")
        except ValueError:
            print(f"  Case temp:    {ad10} (raw)")

    # Detected signals (error signal and 2ω)
    ds = query(port, "DS?")
    if ds:
        print(f"  Det. signals: {ds}  (ω, 2ω)")

    # Status bytes
    st = query(port, "ST?")
    print()
    if st:
        values = parse_status(st)
        if values:
            print(format_status_bytes(values))
        else:
            print(f"  ST? raw: {st}")
    else:
        print("  ST? -- no response")

    print()

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor a PRS10 Rubidium Frequency Standard via RS-232."
    )
    parser.add_argument("device", help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument(
        "interval",
        nargs="?",
        type=float,
        default=5.0,
        help="Update interval in seconds (default: 5)",
    )
    args = parser.parse_args()

    print(f"Connecting to {args.device} at 9600 8N1 …")
    try:
        port = serial.Serial(
            port=args.device,
            baudrate=9600,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=True,       # XON/XOFF software flow control per manual
            timeout=2.0,
        )
    except serial.SerialException as exc:
        sys.exit(f"Cannot open {args.device}: {exc}")

    # Give the device a moment, then drain any startup message
    time.sleep(0.5)
    port.reset_input_buffer()

    # Fetch identifier once
    identifier = query(port, "ID?") or "unknown"
    print(f"ID: {identifier}\n")

    print(f"Polling every {args.interval} s  (Ctrl-C to stop)\n")
    try:
        while True:
            print_update(port, identifier)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        port.close()


if __name__ == "__main__":
    main()
