#!/usr/bin/env python3
"""PRS10 Rubidium Frequency Standard – Textual TUI monitor.

Usage: python3 prs10_monitor.py <device> [interval]
  device   - serial port path (e.g. /dev/ttyUSB0 or /dev/cu.usbserial-1410)
  interval - update interval in seconds (default: 5)
"""

import argparse
import sys
import time
import threading

import serial
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Static

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
    ("ST1", "Power Supplies / Lamp",    ST1_BITS),
    ("ST2", "RF Synthesizer",           ST2_BITS),
    ("ST3", "Temperature Controllers",  ST3_BITS),
    ("ST4", "Frequency Lock-Loop",      ST4_BITS),
    ("ST5", "External 1pps Lock",       ST5_BITS),
    ("ST6", "System Events",            ST6_BITS),
]

# ── Analog channel definitions ────────────────────────────────────────────────
# Format: (channel, name, scale_factor, unit, description)
# scale_factor: multiply raw voltage by this to get actual value

AD_CHANNELS = [
    (0,  "Spare",         1,    "V",  "Spare (J204)"),
    (1,  "+24V Heater",   10,   "V",  "Heater supply"),
    (2,  "+24V Elec",     10,   "V",  "Electronics supply"),
    (3,  "Lamp Drain",    10,   "V",  "Lamp FET drain"),
    (4,  "Lamp Gate",     10,   "V",  "Lamp FET gate"),
    (5,  "Crystal Htr",   1,    "V",  "Crystal heater control"),
    (6,  "Cell Htr",      1,    "V",  "Cell heater control"),
    (7,  "Lamp Htr",      1,    "V",  "Lamp heater control"),
    (8,  "Photosig AC",   1,    "V",  "Amplified AC photosignal"),
    (9,  "Photocell",     4,    "V",  "Photocell I/V converter"),
    (10, "Case Temp",     100,  "°C", "Case temperature"),
    (11, "Crystal Thm",   1,    "V",  "Crystal thermistor"),
    (12, "Cell Thm",      1,    "V",  "Cell thermistor"),
    (13, "Lamp Thm",      1,    "V",  "Lamp thermistor"),
    (14, "Freq Cal",      1,    "V",  "Freq cal pot / ext cal"),
    (15, "Analog GND",    1,    "V",  "Analog ground ref"),
    (16, "VCXO Var",      4,    "V",  "22.48 MHz VCXO varactor"),
    (17, "VCO Var",       4,    "V",  "360 MHz VCO varactor"),
    (18, "Mult Gain",     4,    "V",  "Freq mult gain ctrl"),
    (19, "RF Lock",       1,    "V",  "RF synth lock (~4.8V=OK)"),
]

# ── Serial helpers ─────────────────────────────────────────────────────────────

def _query(port: serial.Serial, cmd: str) -> str:
    port.write((cmd + "\r").encode())
    return port.read_until(b"\r").decode(errors="replace").strip()


def collect_data(port: serial.Serial, lock: threading.Lock) -> dict:
    data: dict = {}
    try:
        with lock:
            data["lo"] = _query(port, "LO?")
            data["fc"] = _query(port, "FC?")
            data["ds"] = _query(port, "DS?")
            data["st"] = _query(port, "ST?")
            # Query all analog channels (AD0-AD19)
            for ch, name, scale, unit, desc in AD_CHANNELS:
                data[f"ad{ch}"] = _query(port, f"AD{ch}?")
    except serial.SerialException as exc:
        data["error"] = str(exc)
    data["time"] = time.strftime("%H:%M:%S")
    return data


def parse_status(raw: str) -> list[int] | None:
    try:
        values = [int(v.strip()) for v in raw.split(",")]
        if len(values) == 6:
            return values
    except ValueError:
        pass
    return None

# ── Widgets ────────────────────────────────────────────────────────────────────

class LockIndicator(Static):
    """Large lock-status badge."""

    def set_state(self, locked: bool | None) -> None:
        if locked is True:
            self.update("● LOCKED")
            self.set_classes("locked")
        elif locked is False:
            self.update("○ NOT LOCKED")
            self.set_classes("unlocked")
        else:
            self.update("? UNKNOWN")
            self.set_classes("unknown")


class MetricsPanel(Static):
    """Key readings summary."""

    def refresh_data(self, data: dict) -> None:
        rows: list[str] = []

        fc = data.get("fc")
        if fc:
            rows.append(f"[bold]FC hi,lo   [/] {fc}")

        ds = data.get("ds")
        if ds:
            rows.append(f"[bold]Det signals[/] {ds}  (ω, 2ω)")

        rows.append(f"[bold]Updated    [/] {data.get('time', '?')}")
        self.update("\n".join(rows))


class AnalogPanel(Static):
    """All analog channel readings."""

    def refresh_data(self, data: dict) -> None:
        lines: list[str] = []

        # Group channels for better organization
        groups = [
            ("Power Supplies", [1, 2]),
            ("Lamp", [3, 4, 7, 13]),
            ("Temperatures", [10, 5, 11, 6, 12]),
            ("Photosignal", [8, 9]),
            ("RF Synth", [16, 17, 18, 19]),
            ("Calibration", [14, 15, 0]),
        ]

        for group_name, channels in groups:
            lines.append(f"[bold cyan]{group_name}[/]")
            for ch in channels:
                # Find channel definition
                ch_def = next((c for c in AD_CHANNELS if c[0] == ch), None)
                if not ch_def:
                    continue
                _, name, scale, unit, desc = ch_def
                raw = data.get(f"ad{ch}")
                if raw:
                    try:
                        val = float(raw) * scale
                        # Format based on unit type
                        if unit == "°C":
                            val_str = f"{val:5.1f} {unit}"
                        elif unit == "V" and scale > 1:
                            val_str = f"{val:5.1f} {unit}"
                        else:
                            val_str = f"{val:5.3f} {unit}"
                        lines.append(f"  {name:<12} {val_str}")
                    except ValueError:
                        lines.append(f"  {name:<12} [red]ERR[/]")
                else:
                    lines.append(f"  {name:<12} [dim]--[/]")
            lines.append("")  # Blank line between groups

        self.update("\n".join(lines).rstrip())


class StatusPanel(Static):
    """Six status bytes with flag descriptions."""

    def refresh_data(self, values: list[int] | None, raw: str | None) -> None:
        if values is None:
            self.update(f"[red]Could not parse status[/]  (raw: {raw!r})")
            return

        lines: list[str] = []
        for (label, desc, bits), val in zip(STATUS_BYTES, values):
            byte_hex = f"0x{val:02X}"

            # ST5 special handling: bit 2 (PLL active) is GOOD, not an error
            if label == "ST5":
                pll_active = (val >> 2) & 1  # bit 2
                # Error bits are 0, 3, 4, 5, 6, 7 (exclude bit 1 which is informational)
                error_bits = [bits[i] for i in [0, 3, 4, 5, 6, 7] if (val >> i) & 1 and bits[i]]
                info_bits = [bits[1]] if (val >> 1) & 1 and bits[1] else []

                if error_bits:
                    # Has errors - show yellow with flags
                    lines.append(
                        f"[bold yellow]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                    )
                    if pll_active:
                        lines.append(f"     [green]✓[/]  PLL active")
                    for b in error_bits:
                        lines.append(f"     [red]⚑[/]  {b}")
                    for b in info_bits:
                        lines.append(f"     [dim]○[/]  {b}")
                elif pll_active:
                    # PLL active, no errors - green
                    lines.append(
                        f"[bold green]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                        f"  [green]✓ PLL active[/]"
                    )
                elif val == 0:
                    # No PLL, no errors (1pps not in use)
                    lines.append(
                        f"[bold green]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                        f"  [green]✓ OK[/]"
                    )
                else:
                    # Only informational bits set
                    lines.append(
                        f"[bold yellow]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                    )
                    for b in info_bits:
                        lines.append(f"     [dim]○[/]  {b}")
            else:
                # Standard handling for other status bytes
                set_bits = [bits[i] for i in range(8) if (val >> i) & 1 and bits[i]]
                if set_bits:
                    lines.append(
                        f"[bold yellow]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                    )
                    for b in set_bits:
                        lines.append(f"     [red]⚑[/]  {b}")
                else:
                    lines.append(
                        f"[bold green]{label}[/]  [dim]{desc:<26}[/]  {byte_hex}"
                        f"  [green]✓ OK[/]"
                    )
        self.update("\n".join(lines))

# ── App ────────────────────────────────────────────────────────────────────────

CSS = """
Screen {
    background: $surface;
}

Header {
    background: $primary-darken-2;
}

#left {
    width: 38;
    padding: 1 1 1 1;
}

#right {
    width: 1fr;
    padding: 1 1 1 1;
}

LockIndicator {
    height: 3;
    content-align: center middle;
    text-style: bold;
    border: round $primary;
    margin-bottom: 1;
}

LockIndicator.locked {
    color: $success;
    border: round $success;
}

LockIndicator.unlocked {
    color: $error;
    border: round $error;
}

LockIndicator.unknown {
    color: $warning;
    border: round $warning;
}

MetricsPanel {
    height: auto;
    border: round $primary;
    padding: 0 1;
    margin-bottom: 1;
}

#analog-heading {
    text-style: bold;
    padding: 0 0 0 0;
    color: $text-muted;
}

AnalogPanel {
    height: 1fr;
    border: round $primary;
    padding: 0 1;
    overflow-y: auto;
}

#status-heading {
    text-style: bold;
    padding: 0 0 1 0;
    color: $text-muted;
}

StatusPanel {
    height: 1fr;
    border: round $primary;
    padding: 0 1;
}
"""


class PRS10App(App):
    CSS = CSS

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "force_refresh", "Refresh now"),
    ]

    def __init__(self, device: str, interval: float) -> None:
        super().__init__()
        self.device = device
        self.interval = interval
        self.port: serial.Serial | None = None
        self._serial_lock = threading.Lock()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left"):
                yield LockIndicator("? UNKNOWN", id="lock")
                yield MetricsPanel("Connecting…", id="metrics")
                yield Static("[bold]Analog Channels[/]", id="analog-heading")
                yield AnalogPanel("Connecting…", id="analog")
            with Vertical(id="right"):
                yield Static("[bold]Status Bytes[/]", id="status-heading")
                yield StatusPanel("Connecting…", id="status")
        yield Footer()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        try:
            self.port = serial.Serial(
                port=self.device,
                baudrate=9600,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                xonxoff=True,
                timeout=2.0,
            )
        except serial.SerialException as exc:
            self.notify(f"Cannot open port: {exc}", severity="error", timeout=10)
            return

        time.sleep(0.5)
        self.port.reset_input_buffer()

        ident = _query(self.port, "ID?") or "unknown"
        self.title = "PRS10 Rubidium Frequency Standard"
        self.sub_title = f"{ident}  ·  {self.device}  ·  {self.interval} s"

        self.set_interval(self.interval, self.do_poll)
        self.do_poll()

    def on_unmount(self) -> None:
        if self.port:
            self.port.close()

    # ── Actions ────────────────────────────────────────────────────────────────

    def action_force_refresh(self) -> None:
        self.do_poll()

    # ── Polling ────────────────────────────────────────────────────────────────

    @work(thread=True)
    def do_poll(self) -> None:
        if self.port is None:
            return
        data = collect_data(self.port, self._serial_lock)
        self.call_from_thread(self._apply, data)

    def _apply(self, data: dict) -> None:
        if "error" in data:
            self.notify(f"Serial error: {data['error']}", severity="error")
            return

        lo = data.get("lo")
        self.query_one("#lock", LockIndicator).set_state(
            True if lo == "1" else (False if lo == "0" else None)
        )
        self.query_one("#metrics", MetricsPanel).refresh_data(data)
        self.query_one("#analog", AnalogPanel).refresh_data(data)

        st_raw = data.get("st")
        values = parse_status(st_raw) if st_raw else None
        self.query_one("#status", StatusPanel).refresh_data(values, st_raw)

# ── Entry point ────────────────────────────────────────────────────────────────

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
    PRS10App(device=args.device, interval=args.interval).run()


if __name__ == "__main__":
    main()
