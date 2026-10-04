# Diagnostic: characterize the busy-timing side channel used by crack.
#
# For several candidate values of the FIRST code byte, and several target
# clock speeds, reset the target, attempt an unlock, and read the raw
# busy_timer (FPGA 12 MHz ticks that the target held BUSY during the ID
# compare). A working timing attack needs:
#   - busy_timer values large enough to resolve (not a handful of ticks), and
#   - one candidate (the correct first byte) standing clearly above the rest.
# Slowing the target clock (higher divider) stretches the pulses; if the
# magnitudes scale with the divider, the target is clocked by the adapter's
# Xin and we can trade speed for resolution.
#
# Usage:  C:\python27-x64\python.exe timing_probe.py COM13
# Run on a freshly power-cycled target. Each attempt is a failed unlock, so
# after many attempts the chip may lock out; power-cycle and rerun if the
# numbers go flat.

import sys

import adapter
import serialio

# A spread of first-byte candidates. If one of these is the true first byte
# its busy_timer should stand out.
CANDIDATES = [0x00, 0x01, 0x02, 0x10, 0x40, 0x80, 0xAA, 0xDE, 0xFF]

# Target clock dividers to try (higher = slower target = longer pulses).
# set_tclk sample values: 0=6MHz, 1=3MHz, 4=1.2MHz, 11=500kHz.
TCLK_DIVIDERS = [1, 4, 11, 30, 60]


def main():
    if len(sys.argv) < 2:
        print("usage: timing_probe.py <port>")
        return 1
    port = sys.argv[1]
    a = adapter.Adapter(port)
    s = serialio.SerialIO(a)
    a.connect()
    print("adapter version: %s" % a.version())
    a.set_sclk(127)  # 1.5 MHz serial clock, same as crack/dump

    for tclk in TCLK_DIVIDERS:
        a.set_tclk(tclk)
        print("")
        print("=== tclk divider = %d (higher is slower) ===" % tclk)
        times = []
        for c in CANDIDATES:
            a.reset_target()
            code = chr(c) + chr(0xDE) * 6  # candidate first byte, rest padding
            s.unlock(code)
            t = a.busy_timer()
            times.append(t)
            print("  first=0x%02x  busy_timer=%d" % (c, t))
        lo, hi = min(times), max(times)
        spread = hi - lo
        print("  -> min=%d max=%d spread=%d" % (lo, hi, spread))
        if hi:
            print("  -> spread/max = %.1f%%" % (100.0 * spread / hi))

    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
