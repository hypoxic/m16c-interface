> **Mirror notice.** This is a public mirror and fork of q3k's
> `m16c-interface`. The upstream repository lives at
> https://codeberg.org/q3k/m16c-interface and all original work is by
> Serge 'q3k' Bazanski and contributors, released under the BSD 2-clause
> license (see COPYING). This fork adds support for the tinyVision.ai
> UPduino v3.1 (Lattice iCE40UP5K) board; see adapter/README.md for the
> board-specific build and wiring. Please send upstream-relevant changes
> to the Codeberg repository.

Renesas M16C programmer
=======================

This is the code for a Renesas M16C SerialIO programmer, based on an iCEStick FPGA devboard and Python host software.

Its most interesting feature is being able to crack the security PIN of the bootloader using a simple timing attack on the busy line.

To build and connect the adapter to the target, see adapter/README.md.

To run the host software to dump the target flash, see host/README.md.

Supported targets
-----------------

The following targets are know to work with this project:

| Device      | Can dump flash memory | Can recover PIN | Tested by     |
|-------------|-----------------------|-----------------|---------------|
| M306K9FCLRP | YES                   | YES             | q3k, joegrand |
| M30626FHPFP | YES                   | NO (see note)   | HYPOXIC       |

Note on the M30626FHPFP (M16C/62P, bootloader VER.4.04): its flash was dumped
and re-flashed successfully over a UPduino v3.1 build of the adapter, but the
PIN-cracking timing attack does not work. VER.4.04 normalizes the ID-compare
time (constant busy duration regardless of the key), so there is no timing
signal to exploit. It was read using the default all-zero key. See
host/README.md for the details and the connect/probe/status/flash commands,
and adapter/README.md for the UPduino wiring and M16C boot-mode straps.

License
-------

All the code in this repository is licensed under a BSD-style 2-clause license.
