Host software
=============

You'll need Python 2.7 and pyserial.

    C:\python27-x64\python.exe main.py --help

Use `-p` to select the adapter's serial port (default `/dev/ttyUSB1`). On
Windows that is the COM port of the USB-UART dongle, e.g. `-p COM13`. See
`adapter/README.md` for how the UPduino rig exposes that port (a separate
dongle, not the FPGA's own USB) and for the target wiring and M16C boot-mode
straps (CNVss, CE, EPM).

Global options
--------------

 - `-p PORT` / `--port`   adapter serial port (default `/dev/ttyUSB1`).
 - `-v` / `--verbose`     debug logging, including per-candidate crack times.
 - `-d` / `--debug-protocol`  log the FPGA<->M16C protocol bytes.
 - `-D` / `--debug-adapter`   log the host<->FPGA bytes.
 - `-t` / `--timestamps`  prefix log lines with timestamps.

Commands
--------

Every command first does the adapter handshake and, except for `connect`,
resets the target and reads its bootloader version.

### connect

Checks only the link to the FPGA adapter and exits. Use it to confirm the
dongle/UART path works without needing the target connected.

    main.py -p COM13 connect
    -> "Connected to adapter version 0" means the serial path is good.

### probe

Resets the target and reads its SerialIO version, reporting whether a valid
target responded. Safe to run with no target: the FPGA SEND_WAIT state times
out (~0.5s) and the read comes back as 0xff instead of hanging.

    main.py -p COM13 probe
    -> "Target present. SerialIO version: 'VER.4.04'"

### status

Unlocks with a candidate code and prints the raw status register bytes
(SRD, SRD1) and the decoded lock state. A diagnostic for checking a key
without dumping. The ID-verified field is SRD1 bits 2-3 (both set = unlocked).

    main.py -p COM13 status -c 00000000000000
    -> "-> target reports UNLOCKED with this code."

Most devices ship with the default key, all `00` or all `FF`, so try those
first:

    main.py -p COM13 status -c 00000000000000
    main.py -p COM13 status -c ffffffffffffff

### crack

The timing attack on the busy line. For each of the 7 code bytes it sweeps
all 256 values and picks the one whose busy time stands out.

    main.py -v -p COM13 crack --samples 20

 - `--samples N`   measurements per candidate (median); more averages out noise.
 - `--no-reset`    do not reset before each attempt (old behaviour; faster but
   fails on bootloaders that lock after one failed check).

After a crack you must power-cycle the target before it will unlock, even with
the correct code.

Note: this attack does NOT work on bootloader VER.4.04. See the note below.

### dump

Unlocks with the given code and reads the flash to a file.

    main.py -p COM13 dump -c 00000000000000 -o dump.bin

 - `-c CODE`         7-byte unlock code, hex (e.g. `00000000000000`).
 - `-o FILE`         output file (binary).
 - `--start-page`    first 256-byte page, hex. Default `0a00` (0xA0000).
 - `--end-page`      last page, hex, inclusive. Default `0fff` (0xFFFF00).

The default range covers the full 384 KB flash of the M16C/62P M30626FHPFP
(0xA0000-0xFFFFF). Use `--start-page`/`--end-page` for other parts.

### flash

Writes a modified image back. It diffs the modified image against the
original, erases only the flash blocks that changed, reprograms them from the
image, and verifies by reading back.

    # Dry run first (lists the blocks that would change, writes nothing):
    main.py -p COM13 flash --image modified.bin

    # Then actually write:
    main.py -p COM13 flash --image modified.bin --write --sclk 15

 - `--image FILE` / `-i`  modified full-flash image (must be exactly 384 KB).
 - `--orig FILE`          original to diff against (default `dump.bin`).
 - `-c CODE`              unlock code (default `00000000000000`).
 - `--write`              actually erase/program; omit for a dry run.
 - `--allow-vector-block` permit erasing block 0xFF000-0xFFFFF, which holds the
   ID code and reset vectors. Refused by default.
 - `--sclk N`             serial clock divider, 0-1023, lower is faster
   (default 127). Try 15 or 7 to speed up; verify catches a too-fast value.

Flash erases a whole block at a time, so a one-byte change means the entire
block that contains it (up to 64 KB on this part) is erased and reprogrammed
from the image. Only the affected block is touched. Keep your original
`dump.bin` as a golden backup. This is recoverable: even a bad write leaves
the chip able to re-enter the serial bootloader via the mode straps.

### download

Diagnostic that tests the bootloader's 0xFA download-to-RAM-and-execute
command. It does not program flash; it loads a small program to RAM and the
bootloader jumps to it. On a locked chip this command is ID-gated and has no
effect, which is what the test reports.

    main.py -p COM13 download

Typical session (M16C/62P, default key)
---------------------------------------

    main.py -p COM13 connect                         # dongle/FPGA link OK?
    main.py -p COM13 probe                            # target in boot mode?
    main.py -p COM13 status -c 00000000000000         # default key unlocks?
    main.py -p COM13 dump -c 00000000000000 -o dump.bin
    # edit a copy of dump.bin -> modified.bin
    main.py -p COM13 flash --image modified.bin       # dry run
    main.py -p COM13 flash --image modified.bin --write --sclk 15

A note on bootloader VER.4.04 (timing attack is defeated)
---------------------------------------------------------

The PIN-cracking timing attack relies on the target holding its busy line for
a data-dependent length of time during the ID-code compare: a correct byte
makes the compare run longer. On older bootloaders (e.g. VER.1.01) this leaks
the key one byte at a time.

On bootloader VER.4.04 (as on the M30626FHPFP tested here) the busy time is
constant regardless of the ID bytes, roughly 1.69 ms every time. We confirmed
this is not a measurement limit: the times are identical across all 256
candidates, do not change when the target clock is varied, and do not change
when resetting before each attempt. In other words the compare is
time-normalized (constant-time, or padded with a fixed delay), so there is no
timing signal to exploit and `crack` will return wrong, non-reproducible keys.

The `0xFA` download-execute command is also ID-gated on a locked 4.04 chip, so
it is not a bypass either.

Practical consequences for a 4.04 part:

 - Try the default keys first (`00000000000000`, `ffffffffffffff`); most
   devices never change them, and that is how the M30626FHPFP here was read.
 - If the key is genuinely unknown and non-default, the remaining avenues are
   invasive: correlation power analysis to recover the key, or voltage/clock
   glitching to bypass the ID check. A blind brute force of the 56-bit key is
   not feasible (~millions of years at this link's rate).

Example (older bootloader, from the original project)
-----------------------------------------------------

    q3k@anathema ~/Projects/renesasif/host $ sudo python2 main.py crack
    Connected to adapter version 0
    Connected to target version VER.1.01
    Cracking byte 1/7...
    [...]
    Finished. Code: [77, ...], 4ddeadbeefcafe

    q3k@anathema ~/Projects/renesasif/host $ sudo python2 main.py dump -o /tmp/bin.bin -c 4ddeadbeefcafe
    Connected to adapter version 0
    Connected to target version VER.1.01
    Target unlocked.
    Writing pages e00-fff to /tmp/bin.bin...
