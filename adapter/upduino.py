# Copyright (c) 2017, Serge 'q3k' Bazanski <serge@bazanski.pl>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""Migen platform for the tinyVision.ai UPduino v3.1 (Lattice iCE40UP5K-SG48).

This mirrors the structure of migen.build.platforms.icestick but targets the
UPduino v3.1 so the M16C adapter can be built for that board. Pin numbers are
iCE40UP5K-SG48 package pins, taken from the official UPduino v3.x constraints
file (tinyvision-ai-inc/UPduino-v3.0, RTL/common/upduino.pcf).

All of the resources the adapter's Top module requests ('serial', 'user_led',
'sio', 'debug', 'spiflash_cs', 'clk12') are defined here, so top.py does not
need to add any board extensions for the UPduino.
"""

from migen.build.generic_platform import Pins, Subsignal, IOStandard, Misc
from migen.build.lattice import LatticePlatform
from migen.build.lattice.programmer import IceStormProgrammer


_io = [
    # On-board 12 MHz oscillator. Reaches the FPGA on the global clock input
    # IOB_25B_G3 (package pin 20) only when the "OSC" solder jumper (R16) is
    # shorted. Do that before flashing; otherwise there is no clock and the
    # design will not run.
    ("clk12", 0, Pins("20"), IOStandard("LVCMOS33")),

    # Host UART, for an EXTERNAL 3.3V USB-to-UART dongle (CP2102/CH340/FT232
    # etc.). The UPduino's on-board FT232H is a USB-to-SPI programmer, not a
    # UART bridge, and its pins are the configuration-flash lines; using it as
    # the host serial forces constant driver swapping and does not cleanly
    # carry the FPGA->host direction. A separate dongle on these spare GPIOs
    # keeps the FT232H as the programmer and needs no driver switching.
    #   Wire: dongle GND -> board GND, dongle RX -> pin 43, dongle TX -> pin 42.
    ("serial", 0,
        Subsignal("tx", Pins("43")),  # FPGA -> dongle RX
        Subsignal("rx", Pins("42")),  # dongle TX -> FPGA
        IOStandard("LVCMOS33"),
    ),

    # Status LEDs / debug outputs. The UPduino's on-board RGB LED sits on
    # dedicated current-driver pads (39/40/41) that need the SB_RGBA_DRV hard
    # macro, so for a toolchain-simple build these are plain GPIO instead. Wire
    # an LED + resistor to any of them if you want a visual indicator; they are
    # optional and not required to read the target.
    #   user_led 0 -> heartbeat (~1 Hz), 1 -> target RxD, 2 -> target TxD,
    #   3 -> target Busy.
    ("user_led", 0, Pins("12"), IOStandard("LVCMOS33")),
    ("user_led", 1, Pins("21"), IOStandard("LVCMOS33")),
    ("user_led", 2, Pins("13"), IOStandard("LVCMOS33")),
    ("user_led", 3, Pins("19"), IOStandard("LVCMOS33")),

    # Debug taps (host UART TX/RX mirrored out), optional.
    ("debug", 0, Pins("18"), IOStandard("LVCMOS33")),
    ("debug", 1, Pins("11"), IOStandard("LVCMOS33")),

    # M16C SerialIO connection to the target. All on the left-side header for
    # easy wiring. Directions are from the FPGA's point of view:
    #   rst  (out)  -> target reset (active low pulse, released high)
    #   txd  (in)   <- target TxD (data from target)
    #   rxd  (out)  -> target RxD (data to target)
    #   sclk (out)  -> target serial clock
    #   busy (in)   <- target busy
    #   tclk (out)  -> target Xin (divided clock supplied to the target)
    ("sio", 0,
        Subsignal("rst", Pins("23")),
        Subsignal("txd", Pins("25")),
        Subsignal("rxd", Pins("26")),
        Subsignal("sclk", Pins("27")),
        Subsignal("busy", Pins("32")),
        Subsignal("tclk", Pins("35")),
        IOStandard("LVCMOS33"),
    ),
]

_connectors = []


class Platform(LatticePlatform):
    default_clk_name = "clk12"
    default_clk_period = 83.333  # 12 MHz

    def __init__(self):
        LatticePlatform.__init__(self, "ice40-up5k-sg48", _io, _connectors,
                                 toolchain="icestorm")

    def create_programmer(self):
        return IceStormProgrammer()
