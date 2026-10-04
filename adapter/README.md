Adapter implementation
======================

This adapter can be built for two boards:

 - the original **iCEStick** (Lattice iCE40-HX1K), and
 - the **tinyVision.ai UPduino v3.1** (Lattice iCE40UP5K-SG48).

Both use the open-source IceStorm/Yosys/nextpnr toolchain.

Toolchain
---------

You need:

 - Yosys, nextpnr-ice40, icepack and iceprog. The easiest way to get all of
   them on Windows, Linux or macOS is the YosysHQ OSS CAD Suite:
   https://github.com/YosysHQ/oss-cad-suite-build/releases
   Extract it and add its `bin` directory to your `PATH` (the suite also ships
   an `environment` script that does this for you).
 - Python and the Python dependencies in `requirements.txt`.

Migen 0.9.2 (pinned in `requirements.txt`) uses a bytecode tracer to name
signals that only understands Python 3.5 - 3.10 bytecode. On Python 3.11+ it
cannot name the default clock domain and the build fails with "Cannot extract
clock domain name from code". Use a Python 3.8 - 3.10 interpreter to run the
build:

    py -3.8 -m venv venv            # Windows
    venv\Scripts\pip install -r requirements.txt

    # or on Linux/macOS
    python3.8 -m venv venv
    venv/bin/pip install -r requirements.txt

Building and flashing
---------------------

`top.py` builds the RTL with yosys + nextpnr-ice40 + icepack. The first
argument selects the board (default `upduino`); add `flash` to also program the
board with `iceprog`.

    # Build only (bitstream -> build/top.bin)
    venv/bin/python top.py              # UPduino v3.1 (default)
    venv/bin/python top.py upduino
    venv/bin/python top.py icestick     # original iCEStick

    # Build and flash
    venv/bin/python top.py upduino flash

Note: current yosys nightlies crash in an experimental backend during
`synth_ice40`'s default abc9 LUT mapping. `top.py` therefore passes `-noabc`
(built-in LUT techmapping). This design has large timing margin so mapping
quality is irrelevant; drop it (edit `SYNTH_OPTS` in top.py) with a stable
yosys.

The host link and flashing use two separate USB connections
-----------------------------------------------------------

The UPduino's on-board FT232H is a USB-to-SPI programmer (that is how `iceprog`
talks to it), not a UART bridge. Rather than fight its single USB channel, this
port keeps the two jobs on two cables:

 - **Flashing** uses the on-board FT232H (the UPduino's own USB connector).
 - **The host UART** uses a separate 3.3V USB-to-UART dongle (CP2102, CH340,
   FT232, ...) wired to spare FPGA GPIOs.

Because they are different devices, nothing is shared and no driver juggling is
needed once flashing is set up. On Windows `iceprog` needs the FT232H on the
WinUSB driver, which you install once with Zadig and then leave in place:

 1. Plug in the UPduino. It appears as "USB Serial Converter" (VID 0403,
    PID 6014).
 2. In Zadig (https://zadig.akeo.ie) enable *Options -> List All Devices*,
    select "USB Serial Converter", pick the **WinUSB** driver and click
    *Replace Driver*. Done once; leave it like this.
 3. Flash whenever you like: `python top.py upduino flash` (or
    `iceprog build/top.bin`).

The USB-UART dongle enumerates as its own COM port, which the host software
uses (see host/README.md). On Linux no Zadig step is needed; `iceprog` detaches
the kernel driver on its own.

UPduino v3.1 notes
------------------

 - **Enable the on-board clock.** The 12 MHz oscillator only reaches the FPGA
   when the `OSC` solder jumper (R16) is shorted. Do this before flashing;
   without it the design has no clock and will not run.
 - **The host UART needs an external 3.3V USB-to-UART dongle** on pins 43/42
   (see the wiring table). The on-board FT232H is used only for flashing.
 - The on-board RGB LED is left unused (its pins need the SB_RGBA_DRV current
   driver). Four ordinary GPIOs carry optional status signals instead; wire an
   LED and resistor to any of them if you want a visual indicator.

Connection to target
--------------------

The target Renesas microcontroller should be connected to the following pins.
Directions are from the adapter's point of view.

UPduino v3.1 (numbers are the iCE40UP5K-SG48 package pins, which match the
`gpioNN` silkscreen labels on the board header):

| Signal        | Dir | UPduino pin |
|---------------|-----|-------------|
| Reset         | out | 23          |
| TXD (target)  | in  | 25          |
| RXD (target)  | out | 26          |
| SCLK          | out | 27          |
| Busy          | in  | 32          |
| Xin           | out | 35          |
| Xout          | -   | disconnected|

Host USB-to-UART dongle (separate 3.3V dongle, not the on-board FT232H):

| Dongle pin | UPduino pin |
|------------|-------------|
| dongle RX  | 43          |
| dongle TX  | 42          |
| GND        | any GND     |

Optional status GPIOs on the UPduino: heartbeat = 12, target RxD = 21,
target TxD = 13, target Busy = 19.

iCEStick:

| Signal        | iCEStick pin |
|---------------|--------------|
| Reset         | 48           |
| TXD           | 56           |
| RXD           | 60           |
| SCLK          | 61           |
| Busy          | 62           |
| Xin           | 47           |
| Xout          | disconnected |

The target should be run at 3v3. It can be powered from the board's 3V3/GND
header pins (mind the regulator's current limit). Share a common ground
between the board and the target.

Putting the M16C into serial I/O mode
-------------------------------------

The six signals above are only the dynamic part of the link. The M16C also
has static strap pins that select the on-chip bootloader's standard serial
I/O (synchronous) mode. The adapter does not drive these; you wire them to
fixed levels and they stay put for the whole session. For the M16C/60-62
family the straps are:

| M16C pin   | Level      | Purpose                                         |
|------------|------------|-------------------------------------------------|
| CNVss      | Vcc (high) | Run the boot-area serial rewrite program        |
| CE (P5_0)  | Vcc (high) | Chip enable for boot mode                       |
| EPM (P5_5) | Vss (low)  | Program-mode select                             |

The adapter's dynamic signals map to the M16C UART1 pins used for the
synchronous (clock-synchronous) protocol:

| Adapter signal    | M16C pin        |
|-------------------|-----------------|
| SCLK              | CLK1 (P6_5)     |
| RXD (to target)   | RxD1 (P6_6)     |
| TXD (from target) | TxD1 (P6_7)     |
| Busy              | RTS1/BUSY (P6_4)|

plus Reset to the target's reset and Xin to its clock input.

Notes:

 - Pin and port numbers (P5_0, P5_5, P6_x) are for the M16C/62 group. The
   M306K9FCLRP is in the M16C/26A group and its exact assignments can differ.
   Confirm CNVss, CE and EPM against that device's datasheet, in the
   "standard serial I/O mode" pin-connection table, before wiring.
 - CNVss must be at its boot level when reset is released; the adapter pulses
   reset, so the straps must already be set before a transaction.
 - Only drive Xin from the adapter if the target is not already clocked. If
   the target board has its own crystal running on Xin/Xout, leave the
   adapter's Xin disconnected so it does not fight the oscillator.
 - The direction labels are from the target's point of view: "TXD (from
   target)" is the target's TxD1 output that the adapter reads, and "RXD (to
   target)" is the target's RxD1 input that the adapter drives. Do not tie
   TXD-to-TXD; the data lines cross over.

Protocol & Architecture
-----------------------

The adapter uses a simple/simplistic serial-based protocol. See the state machine in main.py. It does not implement any application layer code for the Simple Serial I/O - that is done by the host software.

The main component of the logic are two FIFOs for command input and data results, and a state machine to read/write data to those FIFOs from UART, and to perform a Serial I/O transaction with the target.
