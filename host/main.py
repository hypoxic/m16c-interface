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

import argparse
import logging
import sys

import adapter
import serialio


def crack(args, s):
    # Run target clock at 3MHz.
    s.adapter.set_tclk(1)
    # Run serial clock at 1.5MHz.
    s.adapter.set_sclk(127)
    code = []
    while len(code) != 7:
        logging.info("Cracking byte {}/7...".format(len(code)+1, 7))
        byte_times = []
        for try_byte in range(256):
            samples = []
            for _ in range(args.samples):
                # Send code right-padded with 0xDE.
                bin_code = ''.join(chr(c) for c in code) + chr(try_byte)
                # Reset the target before each attempt (unless --no-reset) so
                # the ID check is evaluated fresh. A bootloader that locks
                # after one failed check returns a constant, useless busy time
                # on every later attempt without this. Needs the reset line
                # wired to the target.
                if not args.no_reset:
                    s.adapter.reset_target()
                s.unlock(bin_code.ljust(7, '\xDE'))
                # Measure response time.
                samples.append(s.adapter.busy_timer())
            # Take median time.
            samples = sorted(samples)
            median = samples[args.samples/2]
            logging.debug("Code {}, times {}, median {}".format(try_byte,
                                                                samples,
                                                                median))
            byte_times.append(median)
        # For every byte apart from the last one, the correct byte results in
        # a longer busy time.
        correct = None
        if len(code) == 6:
            correct = byte_times.index(min(byte_times))
        else:
            correct = byte_times.index(max(byte_times))
        logging.info("Byte {}/7 -> {}".format(len(code)+1, correct))
        code.append(correct)
    bin_code = ''.join(chr(c) for c in code).encode('hex')
    logging.info("Finished. Code: {}, {}".format(code, bin_code))


def dump(args, s):
    # Run target clock at 6MHz.
    s.adapter.set_tclk(0)
    # Run target serial clock at 1.5MHz
    s.adapter.set_sclk(127)

    try:
        code = args.code.decode('hex')
    except TypeError:
        logging.fatal("Code must be in hexadecimal format.")
        return
    if len(code) != 7:
        logging.fatal("Code must be 7 bytes long.")
        return

    s.unlock(code)
    status = s.unlock_status()
    if status != serialio.UNLOCK_SUCCESSFUL:
        logging.fatal("Target did not unlock.")
        return
    logging.info("Target unlocked.")

    # Pages are the top 16 bits of the address (256 bytes each). The default
    # range covers the full 384 KB flash of the M16C/62P M30626FHPFP
    # (0xA0000-0xFFFFF). Override with --start-page/--end-page for other parts.
    start = args.start_page
    end = args.end_page

    with open(args.output, 'wb') as f:
        logging.info("Writing pages {:x}-{:x} to {}...".format(start, end,
                                                               args.output))
        for page in range(start, end+1):
            logging.debug("Dumping {:x}00-{:x}ff...".format(page, page))
            data = s.read_page(page)
            f.write(data)


def readstatus(args, s):
    # Diagnostic: unlock with the given code and print the raw status bytes,
    # so the lock state can be read directly instead of trusting the
    # success/fail decode. Useful across bootloader versions.
    s.adapter.set_tclk(1)
    s.adapter.set_sclk(127)
    try:
        code = args.code.decode('hex')
    except TypeError:
        logging.fatal("Code must be in hexadecimal format.")
        return 1
    if len(code) != 7:
        logging.fatal("Code must be 7 bytes long.")
        return 1

    s.unlock(code)
    raw = s.adapter.execute('\x70', 2)
    srd, srd1 = ord(raw[0]), ord(raw[1])
    logging.info("SRD  = 0x%02x  %s" % (srd, format(srd, '08b')))
    logging.info("SRD1 = 0x%02x  %s" % (srd1, format(srd1, '08b')))
    decoded = (srd1 >> 2) & 3
    logging.info("unlock_status decode (SRD1>>2)&3 = %d  (%d == unlocked)"
                 % (decoded, serialio.UNLOCK_SUCCESSFUL))
    if decoded == serialio.UNLOCK_SUCCESSFUL:
        logging.info("-> target reports UNLOCKED with this code.")
    else:
        logging.info("-> target reports LOCKED with this code.")
    return 0


def download_exec(args, s):
    """Test the bootloader's 0xFA download-to-RAM-and-execute command.

    Framing (from truhy/m16c-flasher, m16c_cmds.cpp):
        [0xFA][len_lo][len_hi][checksum][program bytes]
    checksum = low 8 bits of the sum of the program bytes. The program's first
    8 bytes land at RAM 0x402-0x409 (they overlap the bootloader version
    string, so they are effectively discarded) and the rest at 0x600. The
    bootloader jumps to 0x600 only if the checksum, and a CRC16 it keeps at RAM
    0x0CFD, are correct.

    On a locked chip this answers the key question: is 0xFA gated by the ID
    code? The download status bits (SRD1 & 0x10 = checksum match, & 0x80 =
    completed, & 0x0C = ID verified) and whether the bootloader keeps
    responding tell us. If the stub runs, the bootloader stops answering
    because the CPU jumped to our code.
    """
    import time
    try:
        with open(args.file, 'rb') as f:
            prog = f.read()
    except IOError as e:
        logging.fatal("Cannot read program file {}: {}".format(args.file, e))
        return 1
    if not prog:
        logging.fatal("Program file is empty.")
        return 1

    length = len(prog)
    checksum = sum(bytearray(prog)) & 0xFF
    logging.info("Download program '{}': {} bytes, checksum=0x{:02x}".format(
        args.file, length, checksum))

    # Faster serial clock for the multi-byte transfer.
    s.adapter.set_sclk(127)

    # Clear the status register so any change is attributable to the download.
    try:
        s.adapter.execute('\x50', 0)   # CLEAR STATUS REGISTER
    except Exception:
        pass
    before = s.adapter.execute('\x70', 2)
    srd0b, srd1b = ord(before[0]), ord(before[1])
    logging.info("Before: SRD=0x{:02x} SRD1=0x{:02x} (ID {})".format(
        srd0b, srd1b, "verified" if (srd1b & 0x0c) == 0x0c else "LOCKED"))

    # Send the download command. No immediate reply is expected. Time it:
    # a ~0.5s send (or a multiple) means the FPGA SEND_WAIT timed out
    # mid-transfer because the target stopped toggling busy, a transport
    # problem rather than the command being rejected.
    cmd = (chr(0xFA) + chr(length & 0xFF) + chr((length >> 8) & 0xFF)
           + chr(checksum) + prog)
    t0 = time.time()
    s.adapter.execute(cmd, 0)
    dt = time.time() - t0
    logging.info("Download send: {} bytes in {:.2f}s".format(len(cmd), dt))
    time.sleep(0.3)  # let it verify the checksum/CRC and possibly jump

    # If the stub executed, the bootloader is gone and this read fails or
    # returns 0xff padding.
    raw = None
    try:
        raw = s.adapter.execute('\x70', 2)
    except Exception as e:
        logging.info("No status response after download ({}).".format(e))

    bootloader_alive = False
    srd1a = None
    if raw is not None:
        srd0, srd1a = ord(raw[0]), ord(raw[1])
        logging.info("After:  SRD=0x{:02x} SRD1=0x{:02x}".format(srd0, srd1a))
        bootloader_alive = not (srd0 == 0xff and srd1a == 0xff)
        if bootloader_alive:
            logging.info("  checksum match (SRD1&0x10): {}".format(bool(srd1a & 0x10)))
            logging.info("  download completed (SRD1&0x80): {}".format(bool(srd1a & 0x80)))
            logging.info("  rx timeout (SRD1&0x02): {}".format(bool(srd1a & 0x02)))
            logging.info("  SRD1 changed by download: {}".format(srd1a != srd1b))

    # Does the bootloader still answer a normal command?
    try:
        v = s.version()
        if v.startswith('VER'):
            bootloader_alive = True
            logging.info("Bootloader still responds to version: {!r}".format(v))
    except Exception:
        pass

    logging.info("----")
    if not bootloader_alive:
        logging.info("RESULT: bootloader stopped responding -> it JUMPED to our")
        logging.info("  code at 0x600. 0xFA EXECUTES on a LOCKED chip: the")
        logging.info("  download-execute bypass is open. Scope P8_0 to confirm,")
        logging.info("  power-cycle to recover.")
    elif srd1a is not None and (srd1a & 0x10):
        logging.info("RESULT: download ACCEPTED (checksum matched), no jump.")
        logging.info("  0xFA is NOT ID-gated. It declined to jump (CRC16 at 0x0CFD")
        logging.info("  or program size). Next: a CRC16-correct reader stub.")
    elif srd1a is not None and (srd1a & 0x02) and not (srd1b & 0x02):
        logging.info("RESULT: the download set the RX-timeout flag.")
        logging.info("  The loader began receiving but did not get all the bytes in")
        logging.info("  time: a TRANSPORT problem, not gating. See the send time")
        logging.info("  above; our sync link likely stalled or aborted mid-transfer.")
    elif srd1a is not None and srd1a == srd1b:
        logging.info("RESULT: status unchanged by the download.")
        logging.info("  The command had no effect, pointing to 0xFA being ID-gated")
        logging.info("  on this locked chip. If the send time was ~instant with no")
        logging.info("  RX timeout, transport is fine and gating is the explanation.")
    else:
        logging.info("RESULT: download not accepted (no checksum match).")
        logging.info("  Gated, or a framing/transport issue. See the bytes above.")
    return 0


# M16C/62P 384KB flash blocks (M30626FHPFP): (low_addr, high_addr).
# From truhy/m16c-flasher m16c_mem_map.cpp (flash_blocks_m16c62_384k).
FLASH_BASE = 0xA0000
FLASH_SIZE = 0x100000 - FLASH_BASE  # 0x60000 = 384 KB
VECTOR_BLOCK = (0xFF000, 0xFFFFF)   # holds the ID code and reset vectors
FLASH_BLOCKS_384K = [
    (0xA0000, 0xAFFFF),
    (0xB0000, 0xBFFFF),
    (0xC0000, 0xCFFFF),
    (0xD0000, 0xDFFFF),
    (0xE0000, 0xEFFFF),
    (0xF0000, 0xF7FFF),
    (0xF8000, 0xF9FFF),
    (0xFA000, 0xFBFFF),
    (0xFC000, 0xFDFFF),
    (0xFE000, 0xFEFFF),
    VECTOR_BLOCK,
]


def _block_of(addr):
    for lo, hi in FLASH_BLOCKS_384K:
        if lo <= addr <= hi:
            return (lo, hi)
    return None


def flash(args, s):
    # Load the modified image and the original to diff against.
    try:
        with open(args.image, 'rb') as f:
            mod = f.read()
        with open(args.orig, 'rb') as f:
            orig = f.read()
    except IOError as e:
        logging.fatal("Cannot read image: {}".format(e))
        return 1
    if len(mod) != FLASH_SIZE or len(orig) != FLASH_SIZE:
        logging.fatal("Both images must be exactly 0x{:X} ({}) bytes; got "
                      "image={} orig={}".format(FLASH_SIZE, FLASH_SIZE,
                                                len(mod), len(orig)))
        return 1

    # Find 256-byte pages that differ, grouped by block.
    blocks = {}
    for off in range(0, FLASH_SIZE, 256):
        if mod[off:off + 256] != orig[off:off + 256]:
            b = _block_of(FLASH_BASE + off)
            blocks.setdefault(b, []).append(FLASH_BASE + off)

    if not blocks:
        logging.info("Image matches the original. Nothing to flash.")
        return 0

    logging.info("Changes to write:")
    for b in sorted(blocks):
        logging.info("  block 0x{:05X}-0x{:05X}: {} changed page(s)".format(
            b[0], b[1], len(blocks[b])))
        if b == VECTOR_BLOCK:
            logging.warning("    ^ holds the ID code and reset vectors.")

    if VECTOR_BLOCK in blocks and not args.allow_vector_block:
        logging.fatal("Refusing to erase the ID/vector block {:05X}-{:05X}. "
                      "Re-run with --allow-vector-block to override."
                      .format(*VECTOR_BLOCK))
        return 1

    if not args.write:
        logging.info("Dry run (pass --write to erase and program). Each changed")
        logging.info("block is erased whole, then re-programmed from the image,")
        logging.info("then read back and verified.")
        return 0

    # Unlock.
    try:
        code = args.code.decode('hex')
    except (TypeError, ValueError):
        logging.fatal("Code must be hexadecimal.")
        return 1
    if len(code) != 7:
        logging.fatal("Code must be 7 bytes.")
        return 1
    s.clear_status()
    s.unlock(code)
    if s.unlock_status() != serialio.UNLOCK_SUCCESSFUL:
        logging.fatal("Target did not unlock with the given code.")
        return 1
    logging.info("Target unlocked.")

    # Set the serial clock. The reset default is very slow; a lower divider is
    # much faster. Verify (read-back) is the safety net if a value is too fast.
    s.adapter.set_sclk(args.sclk)

    # Erase each affected block, then program all of its non-blank pages.
    for lo, hi in sorted(blocks):
        logging.info("Erasing block 0x{:05X}-0x{:05X}...".format(lo, hi))
        s.clear_status()
        srd0, _ = s.erase_block(hi)
        if srd0 & 0x20:
            logging.fatal("Erase error on 0x{:05X} (SRD=0x{:02X}).".format(lo, srd0))
            return 1
        logging.info("  programming...")
        for addr in range(lo, hi + 1, 256):
            page = mod[addr - FLASH_BASE: addr - FLASH_BASE + 256]
            if page == '\xff' * 256:
                continue  # erase already left this page blank
            s.clear_status()
            srd0, _ = s.program_page(addr, page)
            if srd0 & 0x10:
                logging.fatal("Program error at 0x{:05X} (SRD=0x{:02X}).".format(
                    addr, srd0))
                return 1

    # Verify by reading the affected blocks back.
    logging.info("Verifying...")
    bad = 0
    for lo, hi in sorted(blocks):
        for addr in range(lo, hi + 1, 256):
            want = mod[addr - FLASH_BASE: addr - FLASH_BASE + 256]
            if want == '\xff' * 256:
                continue  # blank page: erase left it 0xff, nothing programmed
            got = s.read_page(addr >> 8)
            if got != want:
                bad += 1
                logging.error("  mismatch at 0x{:05X}".format(addr))
    if bad:
        logging.fatal("Verify FAILED: {} page(s) differ.".format(bad))
        return 1
    logging.info("Verify OK. Flash updated successfully.")
    return 0


parser = argparse.ArgumentParser(
        description='Renesas M16C SerialIO Programmer.')
parser.add_argument('--port', '-p', help='Adapter serial port.',
                    default='/dev/ttyUSB1')
parser.add_argument('--verbose', '-v', help='Increase output verbosity.',
                    action='store_true')
parser.add_argument('--debug-protocol', '-d', help='Log protocol bytes.',
                    action='store_true')
parser.add_argument('--debug-adapter', '-D', help='Log adapter bytes.',
                    action='store_true')
parser.add_argument('--timestamps', '-t', help='Include timestamps in log.',
                    action='store_true')
subparsers = parser.add_subparsers(help='Mode of operation.')

parser_crack = subparsers.add_parser('crack', help='Crack security PIN.')
parser_crack.add_argument('--samples', help='Samples per byte.', type=int,
                          default=3)
parser_crack.add_argument('--no-reset', action='store_true',
                          help='Do not reset the target before each attempt '
                               '(old behaviour; faster but fails on bootloaders '
                               'that lock after one failed check).')
parser_crack.set_defaults(func=crack)

parser_dump = subparsers.add_parser('dump', help='Dump flash memory.')
parser_dump.add_argument('--output', '-o', help='Output file.', type=str,
                         required=True)
parser_dump.add_argument('--code', '-c', help='Unlock code.', type=str,
                         required=True)
parser_dump.add_argument('--start-page', type=lambda x: int(x, 16),
                         default=0x0a00,
                         help='First 256-byte page, hex. Default 0a00 '
                              '(0xA0000, start of M16C/62P 384KB flash).')
parser_dump.add_argument('--end-page', type=lambda x: int(x, 16),
                         default=0x0fff,
                         help='Last page, hex, inclusive. Default 0fff '
                              '(0xFFFF00).')
parser_dump.set_defaults(func=dump)

parser_connect = subparsers.add_parser('connect',
        help='Check the adapter (FPGA) link only, then exit.')
parser_connect.set_defaults(func=lambda args, s: 0, adapter_only=True)

parser_probe = subparsers.add_parser('probe',
        help='Probe the target (DUT): report its SerialIO version, then exit.')
parser_probe.set_defaults(func=lambda args, s: 0, probe_target=True)

parser_status = subparsers.add_parser('status',
        help='Unlock with a code and print the raw status bytes (diagnostic).')
parser_status.add_argument('--code', '-c', help='Unlock code.', type=str,
                           required=True)
parser_status.set_defaults(func=readstatus)

parser_download = subparsers.add_parser('download',
        help='Test the 0xFA download-to-RAM-and-execute command (bypass probe).')
parser_download.add_argument('--file', '-f', type=str,
                             default='boot_dl_p8_0.bin',
                             help='Program binary to download. Default is the '
                                  'bundled P8_0-toggle stub.')
parser_download.set_defaults(func=download_exec)

parser_flash = subparsers.add_parser('flash',
        help='Write a modified image back (erase+program changed blocks).')
parser_flash.add_argument('--image', '-i', required=True,
                          help='Modified full-flash image (384KB).')
parser_flash.add_argument('--orig', default='dump.bin',
                          help='Original image to diff against (default dump.bin).')
parser_flash.add_argument('--code', '-c', default='00000000000000',
                          help='Unlock code (default all zeros).')
parser_flash.add_argument('--write', action='store_true',
                          help='Actually erase/program. Default is a dry run.')
parser_flash.add_argument('--allow-vector-block', action='store_true',
                          help='Permit erasing block 0xFF000-0xFFFFF (ID/vectors).')
parser_flash.add_argument('--sclk', type=int, default=127,
                          help='Serial clock divider, 0-1023, lower is faster '
                               '(default 127). flash previously ran at the slow '
                               'reset default; try 15 or 7 for more speed, '
                               'verify will catch a too-fast setting.')
parser_flash.set_defaults(func=flash)


if __name__ == '__main__':
    args = parser.parse_args()

    fmt = '%(message)s'
    if args.timestamps:
        fmt = '%(asctime)-15s %(levelname)s %(message)s'
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format=fmt)
    else:
        logging.basicConfig(level=logging.INFO, format=fmt)

    adapter_logger, protocol_logger = None, None
    if args.debug_adapter:
        adapter_logger = logging
    if args.debug_protocol:
        protocol_logger = logging

    if not hasattr(args, 'func'):
        parser.print_help()
        sys.exit(1)

    a = adapter.Adapter(args.port, logger=adapter_logger)
    s = serialio.SerialIO(a, logger=protocol_logger)

    # Adapter (FPGA) link. Needed by everything; works without the target.
    try:
        s.adapter.connect()
    except Exception as e:
        logging.fatal("No response from adapter on {}: {}".format(args.port, e))
        sys.exit(1)
    logging.info("Connected to adapter version {}".format(s.adapter.version()))

    # The 'connect' check stops here, at the adapter handshake, so it verifies
    # the serial path to the FPGA without touching the target at all.
    if getattr(args, 'adapter_only', False):
        logging.info("Adapter link OK (target not probed).")
        sys.exit(0)

    # The 'probe' check resets the target and reads its SerialIO version,
    # reporting whether a valid target is present. A missing target no longer
    # hangs the FPGA: the SEND_WAIT state times out (~0.5s) and the read comes
    # back as 0xff bytes.
    if getattr(args, 'probe_target', False):
        # Use the same clocking as crack/dump so this is a faithful test.
        s.adapter.set_tclk(1)    # ~3 MHz target clock (Xin)
        s.adapter.set_sclk(127)  # ~1.5 MHz serial clock
        s.adapter.reset_target()
        v = s.version()
        if v.startswith('VER'):
            logging.info("Target present. SerialIO version: {}".format(v))
            sys.exit(0)
        logging.warning("No valid target response (got {!r}). Check DUT power, "
                        "wiring, and that the busy line is driven.".format(v))
        sys.exit(2)

    # Target (M16C) link, for crack/dump.
    try:
        s.connect()
        logging.info("Connected to target version {}".format(s.version()))
    except Exception as e:
        logging.fatal("Target not responding: {}".format(e))
        sys.exit(1)

    sys.exit(args.func(args, s) or 0)
