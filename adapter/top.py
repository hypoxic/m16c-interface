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

"""The main state machine of the adapter."""
__author__ = "Serge 'q3k' Bazanski <serge@bazanski.pl>"

import sys

from migen import *
from migen.genlib.fifo import SyncFIFOBuffered
from migen.build.generic_platform import Subsignal, Pins, IOStandard, ConstraintError
from migen.build.platforms import icestick

import uart

class Top(Module):
    # Board clock frequency.
    CLKFREQ = 12000000
    # Host UART baud rate. 115200 is slow enough to stay reliable over a USB
    # isolator and clip-lead wiring. At CLKFREQ=12 MHz the divisor is 104, for
    # an actual 115384 baud (0.16% error). This does not affect flashing, which
    # uses the on-board FT232H, not this UART.
    BAUDRATE = 115200

    def __init__(self, platform):
        # Instantiate and connect UART cores to host.
        self.submodules.uart_rx = uart.RXFIFO(self.CLKFREQ, self.BAUDRATE)
        self.submodules.uart_tx = uart.TXFIFO(self.CLKFREQ, self.BAUDRATE)
        serial = platform.request('serial')
        self.comb += [
            serial.tx.eq(self.uart_tx.tx),
            self.uart_rx.rx.eq(serial.rx),
        ]

        # If a board exposes a configuration-flash chip-select that must be
        # held inactive during normal operation, drive it high. Boards without
        # this resource simply skip it.
        try:
            spiflash_cs = platform.request('spiflash_cs')
            self.comb += spiflash_cs.eq(1)
        except ConstraintError:
            pass

        # Heartbeat LED.
        led = platform.request('user_led')
        counter = Signal(max=12000000)
        self.sync += If(counter == 11999999,
            counter.eq(0),
            led.eq(~led),
        ).Else(
            counter.eq(counter + 1),
        )

        target = platform.request('sio')
        # Register signals from target because metastability.
        target_txd = Signal()
        target_busy = Signal()
        self.sync += [
            target_txd.eq(target.txd),
            target_busy.eq(target.busy),
        ]

        # More debug LEDs.
        self.comb += [
            platform.request('user_led').eq(target.rxd),
            platform.request('user_led').eq(target.txd),
            platform.request('user_led').eq(target.busy),
        ]

        # Input/output FIFOs.
        self.submodules.txbuffer = SyncFIFOBuffered(8, 512)
        self.submodules.rxbuffer = SyncFIFOBuffered(8, 512)


        # Dispatch and response flops for host communication.
        request = Signal(8)
        response = Signal(8)
        # Generic counter used by a bunch of states.
        # TODO: share the logic that populates this for sending/receiving
        # words.
        counter = Signal(max=120000)

        # Target CLK divider.
        tclk_divider = Signal(max=120, reset=4)
        tclk_counter = Signal(max=121)
        self.sync += [
            If(tclk_counter == tclk_divider,
                tclk_counter.eq(0),
                target.tclk.eq(~target.tclk),
            ).Else(
                tclk_counter.eq(tclk_counter + 1),
            )
        ]

        # Target serial CLK divider, used by the *_EDGE states in the FSM.
        sclk_divider = Signal(max=1024, reset=1023)

        # Target busy timer.
        timer = Signal(32)
        timer_running = Signal(reset=0)
        last_busy = Signal()
        self.sync += last_busy.eq(target_busy)
        self.sync += \
            If(~timer_running,
                If((~last_busy) & target_busy,
                    timer_running.eq(1),
                    timer.eq(0),
                )
            ).Elif(~target_busy,
                timer_running.eq(0),
            ).Else(
                timer.eq(timer + 1),
            )

        
        # Main state machine.
        self.submodules.fsm = FSM(reset_state='IDLE')
        self.fsm.act('IDLE',
            If(self.uart_rx.readable,
                NextState('DISPATCH'),
                NextValue(request, self.uart_rx.dout),
            ),
        )
        self.fsm.act('DISPATCH',
            Case(request, {
                # Get API version of bitstream.
                ord('v'): [
                    NextState('RESPOND_BYTE'),
                    NextValue(response, ord('0')),
                ],
                # Flush both FIFOs.
                ord('f'): [
                    NextState('FIFO_FLUSH'),
                ],
                # Reset target.
                ord('r'): [
                    NextState('RESET_TARGET'),
                    NextValue(target.rst, 0),
                    NextValue(counter, 119999),
                ],
                # Write byte to FIFO.
                ord('w'): [
                    NextState('FIFO_WRITE'),
                ],
                # Perform transaction with target.
                ord('W'): [
                    NextState('SEND_START'),
                ],
                # Read bytes from FIFO.
                ord('R'): [
                    NextState('FIFO_READ_START'),
                    NextValue(counter, 3),
                ],
                # Get timer value.
                ord('t'): [
                    NextState('GET_TIMER'),
                    NextValue(counter, 0),
                ],
                # Get timer status.
                ord('T'): [
                    NextState('RESPOND_BYTE'),
                    If(timer_running,
                        NextValue(response, ord('r'))
                    ).Else(
                        NextValue(response, ord('s'))
                    )
                ],
                # Set target clock.
                ord('s'): [
                    NextState('SET_TCLK'),
                ],
                # Set target serial clock.
                ord('S'): [
                    NextState('SET_SCLK'),
                    NextValue(counter, 1),
                ],
                # Default handler.
                'default': [
                    NextState('RESPOND_BYTE'),
                    NextValue(response, ord('?')),
                ],
            })
        )
        self.fsm.act('RESET_TARGET',
            If(counter == 0,
                NextValue(target.rst, 1),
                NextValue(target.sclk, 1),
                NextValue(response, ord('.')),
                NextState('RESPOND_BYTE'),
            ).Else(
                NextValue(counter, counter-1),
            )
        )
        self.fsm.act('GET_TIMER',
            If(counter == 3,
                NextState('IDLE'),
            ),
            NextValue(counter, counter+1),
        )
        self.fsm.act('FIFO_WRITE',
            If(self.uart_rx.readable,
                If(self.txbuffer.writable,
                    NextValue(response, ord('.')),
                    NextState('RESPOND_BYTE'),
                ).Else(
                    NextValue(response, ord('!')),
                    NextState('RESPOND_BYTE'),
                )
            )
        )
        self.fsm.act('SET_TCLK',
            If(self.uart_rx.readable,
                NextValue(tclk_divider, self.uart_rx.dout),
                NextValue(response, ord('.')),
                NextState('RESPOND_BYTE'),
            )
        )
        self.fsm.act('SET_SCLK',
            If(self.uart_rx.readable,
                NextValue(sclk_divider, (sclk_divider >> 8) | (self.uart_rx.dout << 8)),
                If(counter == 0,
                    NextValue(response, ord('.')),
                    NextState('RESPOND_BYTE'),
                ).Else(
                    NextValue(counter, counter-1),
                )
            )
        )

        # Transaction signals.
        # Byte to be sent to target.
        send_byte = Signal(8)
        # Byte being received from target.
        receive_byte = Signal(8)
        # Index into both send and receive bytes.
        bit_index = Signal(max=8)
        # Downounter for clock rise/fall edges, set to sclk.
        bit_counter = Signal(max=1024)
        # Rising/falling edge over, move to next state.
        bit_strobe = Signal()
        self.comb += bit_strobe.eq(bit_counter == 0)
        next_bit = Signal()

        # Timeout for SEND_WAIT. If the target never deasserts busy (for
        # example no target is connected, so the busy input floats high), abort
        # the transaction and return to IDLE instead of hanging forever. About
        # 0.5s at 12 MHz, which is far longer than any legitimate inter-byte
        # busy, so it only ever fires on a missing or stuck target.
        send_wait_timeout = self.CLKFREQ // 2
        send_wait_timer = Signal(max=send_wait_timeout + 1)

        self.fsm.act('SEND_START',
            NextState('SEND_PREPARE'),
            NextValue(bit_index, 0),
        )
        # Prepare next byte to send or finish transaction.
        self.fsm.act('SEND_PREPARE',
            If(self.txbuffer.readable,
                NextValue(send_byte, self.txbuffer.dout),
                NextValue(send_wait_timer, 0),
                NextState('SEND_WAIT'),
            ).Else(
                NextValue(response, ord('.')),
                NextState('RESPOND_BYTE'),
            )
        )

        # Wait for target to not be busy, or time out and abort.
        self.fsm.act('SEND_WAIT',
            If(~target_busy,
                NextValue(bit_counter, sclk_divider),
                NextState('SEND_FALLING'),
            ).Elif(send_wait_timer == send_wait_timeout,
                NextState('SEND_ABORT'),
            ).Else(
                NextValue(send_wait_timer, send_wait_timer + 1),
            )
        )

        # Downcount bit_counter, send data to target.
        self.fsm.act('SEND_FALLING',
            If(bit_counter == 0,
                NextValue(target.sclk, 0),
                NextState('SEND_RISING'),
                NextValue(bit_counter, sclk_divider),
                NextValue(target.rxd, (send_byte >> bit_index) & 1),
            ).Else(
                NextValue(bit_counter, bit_counter-1),
            )
        )
        # Downcount bit_counter, receive data from target.
        self.fsm.act('SEND_RISING',
            If(bit_counter == 0,
                NextValue(receive_byte, (target_txd << 7) | (receive_byte >> 1)),
                NextValue(target.sclk, 1),
                If(bit_index == 7,
                    NextValue(bit_index, 0),
                    NextState('SEND_WRITEBACK'),
                ).Else(
                    NextValue(bit_index, bit_index+1),
                    NextState('SEND_FALLING'),
                    NextValue(bit_counter, sclk_divider),
                )
            ).Else(
                NextValue(bit_counter, bit_counter-1),
            )
        )
        # Write received byte to read FIFO.
        self.fsm.act('SEND_WRITEBACK',
            NextState('SEND_PREPARE')
        )

        # Transaction timed out waiting for the target's busy line. Drain any
        # unsent bytes from the TX FIFO and ack the host so it does not hang.
        # Whatever was received so far stays in the RX FIFO; bytes the host
        # reads beyond that come back as 0xff, and the next transaction starts
        # with a flush. This returns the FSM to IDLE instead of wedging.
        self.fsm.act('SEND_ABORT',
            If(~self.txbuffer.readable,
                NextValue(response, ord('.')),
                NextState('RESPOND_BYTE'),
            )
        )

        # Downcounter for requested bytes to read from FIFO.
        fifo_read_counter = Signal(32)

        # Set downcounter based on host request.
        self.fsm.act('FIFO_READ_START',
            If(self.uart_rx.readable,
                If(counter == 0,
                    NextState('FIFO_READ'),
                ).Else(
                    NextValue(counter, counter-1)
                ),
                NextValue(fifo_read_counter, (fifo_read_counter >> 8) | (self.uart_rx.dout << 24))
            )
        )

        # Downcount fifo_read_counter, send FIFO bytes to host.
        self.fsm.act('FIFO_READ',
            If(fifo_read_counter == 0,
                NextState('IDLE'),
            ).Else(
                NextValue(fifo_read_counter, fifo_read_counter-1),
            )
        )
        # Whether the read FIFO should emit a byte - somewhat of a hack.
        fifo_read = Signal()
        self.comb += fifo_read.eq(self.fsm.ongoing('FIFO_READ') & (fifo_read_counter != 0))

        self.fsm.act('FIFO_FLUSH',
            If((~self.rxbuffer.readable) & (~self.txbuffer.readable),
                NextValue(response, ord('.')),
                NextState('RESPOND_BYTE'),
            )
        )

        # Enables and data connections for FIFOs.
        self.comb += [
            self.txbuffer.we.eq(
                self.fsm.ongoing('FIFO_WRITE') & self.uart_rx.readable
            ),
            self.txbuffer.re.eq(
                self.fsm.ongoing('SEND_PREPARE') |
                self.fsm.ongoing('SEND_ABORT') |
                self.fsm.ongoing('FIFO_FLUSH')
            ),
            self.txbuffer.din.eq(self.uart_rx.dout),

            self.rxbuffer.we.eq(self.fsm.ongoing('SEND_WRITEBACK')),
            self.rxbuffer.re.eq(
                fifo_read |
                self.fsm.ongoing('FIFO_FLUSH')
            ),
            self.rxbuffer.din.eq(receive_byte),
        ]

        # Generic 1-byte response state.
        self.fsm.act('RESPOND_BYTE',
            If(self.uart_tx.writable,
                NextState('IDLE'),
            ),
        )

        # Host UART enables.
        self.comb += [
            self.uart_rx.re.eq(
                self.fsm.ongoing('IDLE') |
                self.fsm.ongoing('FIFO_WRITE') |
                self.fsm.ongoing('SET_TCLK') |
                self.fsm.ongoing('SET_SCLK') |
                self.fsm.ongoing('FIFO_READ_START')
            ),
            self.uart_tx.we.eq(
                self.fsm.ongoing('RESPOND_BYTE') |
                self.fsm.ongoing('GET_TIMER') |
                fifo_read
            ),
            If(self.fsm.ongoing('RESPOND_BYTE'),
                self.uart_tx.din.eq(response),
            ).Elif(self.fsm.ongoing('GET_TIMER'),
                self.uart_tx.din.eq(timer >> (counter * 8)),
            ).Elif(fifo_read,
                If(self.rxbuffer.readable,
                    self.uart_tx.din.eq(self.rxbuffer.dout),
                ).Else(
                    self.uart_tx.din.eq(0xff),
                ),
            )
        ]

        # Expose Host UART on debug pins.
        self.comb += [
            platform.request('debug').eq(self.uart_tx.tx),
            platform.request('debug').eq(self.uart_rx.rx),
        ]


def build_icestick():
    plat = icestick.Platform()
    debugpins = [119, 118, 117, 116, 115, 114, 113, 112]
    plat.add_extension([
        ('sio', 0,
            Subsignal('rst', Pins('48')),
            Subsignal('txd', Pins('56')),
            Subsignal('rxd', Pins('60')),
            Subsignal('sclk', Pins('61')),
            Subsignal('busy', Pins('62')),
            Subsignal('tclk', Pins('47')),
            IOStandard('LVCMOS33'),
        ),
    ] + [
        ('debug', i, Pins(str(p)), IOStandard('LVCMOS33')) for i, p in enumerate(debugpins)
    ])
    # Randomize seed because it doesn't get routed with the default of 1.
    plat.toolchain.pnr_opt = "-q -r"
    return plat


def build_upduino():
    # Imported lazily so the iCEStick build does not require this module.
    import upduino
    # All adapter resources ('serial', 'user_led', 'sio', 'debug',
    # 'spiflash_cs') are defined in the UPduino platform itself, so no board
    # extension is needed here.
    return upduino.Platform()


BOARDS = {
    'icestick': build_icestick,
    'upduino': build_upduino,
}

# synth_ice40's default abc9 LUT mapping crashes current yosys nightlies in the
# experimental aiger2 writer ("Assert data_start == f->tellp() failed"). The
# built-in LUT techmapper (-noabc) avoids it; this design has huge timing margin
# so the slightly worse mapping does not matter. Remove it with a stable yosys.
SYNTH_OPTS = "-noabc"


def run_flow(plat, build_dir="build", do_flash=False):
    """Build the bitstream by invoking the tools directly.

    Migen's own Windows script runner ("cmd /c build_top.bat") is unreliable
    here, so we generate the sources (run=False) and then run yosys,
    nextpnr-ice40 and icepack ourselves. yosys, nextpnr-ice40, icepack and
    iceprog must be on PATH (e.g. from the YosysHQ OSS CAD Suite).
    """
    import os
    import subprocess

    plat.build(Top(plat), build_dir=build_dir, run=False, synth_opts=SYNTH_OPTS)

    _, series_size, package = plat.toolchain.parse_device_string(plat.device)
    pnr_pkg_opts = ["--" + series_size, "--package", package]

    steps = [
        ["yosys", "-q", "-l", "top.rpt", "top.ys"],
        ["nextpnr-ice40"] + pnr_pkg_opts +
            ["--pcf", "top.pcf", "--json", "top.json",
             "--asc", "top.txt", "--pre-pack", "top_pre_pack.py"],
        ["icepack", "top.txt", "top.bin"],
    ]
    if do_flash:
        # Writes to the configuration flash at offset 0.
        steps.append(["iceprog", "top.bin"])

    cwd = os.path.join(os.getcwd(), build_dir)
    for cmd in steps:
        print("+ " + " ".join(cmd))
        subprocess.run(cmd, cwd=cwd, check=True)


def main():
    # Usage: top.py [board] [flash]
    #   board: 'upduino' (default) or 'icestick'
    #   flash: append 'flash' to also program the board with iceprog
    # Building and flashing are separated because flashing needs the board
    # present and (on Windows) its FTDI bound to WinUSB; see adapter/README.md.
    args = [a for a in sys.argv[1:]]
    do_flash = 'flash' in args
    args = [a for a in args if a != 'flash']
    board = args[0] if args else 'upduino'
    if board not in BOARDS:
        print('Unknown board "{}". Choose one of: {}'.format(
            board, ', '.join(sorted(BOARDS))))
        return 1
    plat = BOARDS[board]()
    run_flow(plat, do_flash=do_flash)


if __name__ == '__main__':
    sys.exit(main() or 0)
