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

Protocol & Architecture
-----------------------

The adapter uses a simple/simplistic serial-based protocol. See the state machine in main.py. It does not implement any application layer code for the Simple Serial I/O - that is done by the host software.

The main component of the logic are two FIFOs for command input and data results, and a state machine to read/write data to those FIFOs from UART, and to perform a Serial I/O transaction with the target.
