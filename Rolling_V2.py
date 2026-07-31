#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rolling Anti-Lag CMD Utility for Bosch ME7/ME7.5 1 MB.

Interactive front-end based on rolling_chain.py / rollingv3.php logic.
Supports SOLO installation and CHAIN installation before an existing Launch Control hook.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ECUMap = Dict[str, List[str]]
TRIGGERS = {
    "brake": ("Brake", ["b_br"]),
    "clutch": ("Clutch", ["b_kuppl"]),
    "cruise_set": ("Cruise SET", ["b_fgrsec", "s_fgrsv", "b_fgrtdc"]),
    "cruise_res": ("Cruise RES", ["b_fgrwac", "b_fgrtuc", "s_fgrwb"]),
}


def info(message: str) -> None:
    print(f"[INFO] {message}")


def ok(message: str) -> None:
    print(f"[ OK ] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)


def banner() -> None:
    line = "=" * 64
    print(line)
    print("ME7 Rolling Anti-Lag CMD V2".center(64))
    print("Bosch ME7 / ME7.5 Rolling Anti-Lag Installer".center(64))
    print(line)


def safe_input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise RuntimeError(
            "Console input is unavailable. Run under Wine with: "
            "wineconsole --backend=curses Rolling_V2.exe test.bin test.ecu"
        ) from exc


def prompt_yes_no(label: str, current: bool = True) -> bool:
    default = "Y" if current else "N"
    while True:
        raw = safe_input(f"{label} [Y/N] [{default}]: ").strip().lower()
        if not raw:
            return current
        if raw in {"y", "yes", "t", "tak"}:
            return True
        if raw in {"n", "no", "nie"}:
            return False
        print("  Enter Y or N.")


def die(msg: str) -> None:
    print(f"\nERROR: {msg}")
    raise SystemExit(1)


def parse_hex(value: object) -> int:
    s = str(value).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    return int(s, 16)


def prepare_ecu(text: str) -> ECUMap:
    result: ECUMap = {}
    for raw in text.splitlines():
        line = raw.replace("\r", "")
        if not line or line[0] in ";#/[":
            continue
        line = line.replace("\t", "").replace(" ", "")
        cols = line.split(",")
        if len(cols) >= 10:
            result[cols[0].lower()] = cols[1:]
    return result


def ecu_addr(ecu: ECUMap, name: str) -> Optional[str]:
    row = ecu.get(name.lower())
    if not row or len(row) < 2 or not row[1]:
        return None
    return row[1][2:] if row[1].lower().startswith("0x") else row[1]


def mask_to_bit(value: str) -> int:
    v = parse_hex(value)
    if v <= 0 or v & (v - 1):
        raise ValueError(f"invalid bit mask {value}")
    return v.bit_length() - 1


def ecu_bit(ecu: ECUMap, name: str) -> Optional[int]:
    row = ecu.get(name.lower())
    if not row or len(row) < 4 or not row[3]:
        return None
    try:
        return mask_to_bit(row[3])
    except ValueError:
        return None


def available_triggers(ecu: ECUMap) -> Dict[str, Tuple[str, str, int, str]]:
    found: Dict[str, Tuple[str, str, int, str]] = {}
    for key, (label, candidates) in TRIGGERS.items():
        for symbol in candidates:
            addr, bit = ecu_addr(ecu, symbol), ecu_bit(ecu, symbol)
            if addr is not None and bit is not None:
                found[key] = (label, addr, bit, symbol)
                break
    return found


def print_trigger_addresses(
    choices: Dict[str, Tuple[str, str, int, str]]
) -> None:
    """Show every supported trigger found in the ECU definition."""
    print()
    print("Available activation triggers:")
    preferred_order = ["cruise_res", "cruise_set", "brake", "clutch"]
    ordered_keys = [key for key in preferred_order if key in choices]
    ordered_keys.extend(key for key in choices if key not in ordered_keys)

    for key in ordered_keys:
        label, address, bit, symbol = choices[key]
        print(
            f"{label + ':':<13} 0x{parse_hex(address):06X}.{bit} "
            f"({symbol})"
        )


def ecu_required(ecu: ECUMap, name: str) -> str:
    value = ecu_addr(ecu, name)
    if value is None:
        die(f"{name} not found in ECU definition")
    return value


def find_ftomn(data: bytearray) -> List[int]:
    found: List[int] = []
    for i in range(len(data) - 26):
        if data[i] == 0x05 and data[i + 1] != 0x05 and data[i + 11] == 0x05 \
                and data[i + 24] == 0x08 and data[i + 25] == 0x05:
            found.append(i + 22)
    if not found:
        for i in range(len(data) - 13):
            if data[i] == 0x05 and data[i + 1] != 0x05 and data[i + 11] == 0x05 and data[i + 12] == 0x07:
                found.append(i + 11)
    return found


def find_hook(data: bytearray) -> int:
    pattern = b"\xD7\x40\x06\x02\x03\xF8"
    positions: List[int] = []
    start = 0
    while True:
        pos = data.find(pattern, start)
        if pos < 0:
            break
        if pos >= 4:
            positions.append(pos - 4)
        start = pos + 1
    if not positions:
        die("main hook pattern D7 40 06 02 03 F8 not found")
    return positions[-1]


def find_hole(data: bytearray, size: int, start: int = 0, end: int = 0,
              avoid: Optional[Tuple[int, int]] = None) -> Optional[int]:
    if end <= 0 or end >= len(data):
        end = len(data) - 64
    run_end = -1
    i = end
    while i >= start:
        if data[i] == 0xFF:
            if run_end < 0:
                run_end = i + 1
        elif run_end >= 0:
            run_start = i + 1
            aligned = (run_start + 15) & ~15
            if run_end - aligned >= size:
                if not avoid or aligned + size <= avoid[0] or aligned >= avoid[1]:
                    return aligned
            run_end = -1
        i -= 1
    return None


def da_bytes(addr: int) -> Tuple[int, int, int]:
    hx = f"{addr + 0x800000:06X}"
    return int(hx[0:2], 16), int(hx[4:6], 16), int(hx[2:4], 16)


def read_da(data: bytearray, offset: int) -> Optional[int]:
    if offset < 0 or offset + 3 >= len(data) or data[offset] != 0xDA:
        return None
    return int(f"{data[offset+1]:02X}{data[offset+3]:02X}{data[offset+2]:02X}", 16) - 0x800000


def write_da(data: bytearray, offset: int, target: int) -> None:
    b1, b2, b3 = da_bytes(target)
    data[offset:offset + 4] = bytes((0xDA, b1, b2, b3))


def is_rolling_code(data: bytearray, addr: Optional[int]) -> bool:
    if addr is None or addr < 0 or addr + 18 >= len(data):
        return False
    return (data[addr] == 0x9A and data[addr + 2] == 0x12 and
            data[addr + 4:addr + 6] == b"\xC2\xF4" and
            data[addr + 8:addr + 14] == b"\xD7\x00\x81\x00\xC2\xF9")


def find_setzi_launch(data: bytearray) -> Tuple[Optional[int], Optional[int]]:
    """Find existing Setzi Launch function and its configuration block."""
    function_len = 144
    refs_at = {"speed": 14, "launch": 30, "rpm": 60, "pedal": 76, "ign": 96}
    scan_from = 0x70000 if len(data) > 0x80000 else 0
    scan_to = len(data) - function_len
    for off in range(scan_from, scan_to):
        if data[off] not in (0x9A, 0x8A):
            continue
        if data[off + function_len - 2:off + function_len] != b"\xFF\xFF":
            continue
        if data[off + 4:off + 6] != b"\xF2\xF4":
            continue
        if data[off + 16:off + 20] != b"\x40\x49\x9D\x0B":
            continue
        values = {name: int.from_bytes(data[off + pos:off + pos + 2], "little") for name, pos in refs_at.items()}
        base = values["speed"]
        if not (values["launch"] == base + 2 and values["ign"] == base + 4 and values["rpm"] == base + 6 and values["pedal"] == base + 8):
            continue
        candidates = [base + 0x10000, base] if base < 0x10000 else [base]
        for cfg in candidates:
            if 0 <= cfg <= len(data) - 16:
                return off, cfg
        return off, None
    return None, None


def overlaps(start: int, size: int, ranges: List[Tuple[int, int]]) -> bool:
    end = start + size
    return any(start < r_end and end > r_start for r_start, r_end in ranges)


def find_hole_separate(
    data: bytearray,
    size: int,
    start: int = 0,
    end: int = 0,
    excluded: Optional[List[Tuple[int, int]]] = None,
) -> Optional[int]:
    """Find aligned FF space that does not overlap Launch or other reserved areas."""
    excluded = excluded or []
    if end <= 0 or end > len(data):
        end = len(data)
    i = end - 1
    while i >= start:
        if data[i] != 0xFF:
            i -= 1
            continue
        run_end = i + 1
        while i >= start and data[i] == 0xFF:
            i -= 1
        run_start = i + 1
        aligned = (run_start + 15) & ~15
        while aligned + size <= run_end:
            if not overlaps(aligned, size, excluded):
                return aligned
            aligned += 16
    return None


def put_word(data: bytearray, pos: int, value: int) -> int:
    data[pos:pos + 2] = struct.pack("<H", value & 0xFFFF)
    return pos + 2


def offset2bit(addr: str) -> int:
    return (parse_hex(addr) - 0xFD00) // 2


def encode_throttle(percent: float) -> int:
    raw = max(0, min(255, round(percent * 2.55)))
    return (raw - 256) & 0xFFFF


def decode_throttle(word: int) -> float:
    signed = word if word < 0x8000 else word - 0x10000
    raw = max(0, min(255, signed + 256))
    return raw / 2.55


def write_config(data: bytearray, vars_addr: int, rpm: int, throttle: float) -> None:
    data[vars_addr:vars_addr + 2] = struct.pack("<H", encode_throttle(throttle))
    data[vars_addr + 2:vars_addr + 4] = struct.pack("<H", max(0, min(65535, rpm * 4)))
    data[vars_addr + 4:vars_addr + 9] = b"\xFF" * 5


def normalize_vars_address(data: bytearray, reference: int) -> int:
    """Convert the 16-bit flash reference used by C167 code to a BIN offset."""
    candidates = [reference]
    if reference < 0x10000:
        candidates.insert(0, reference + 0x10000)

    for candidate in candidates:
        if not (0 <= candidate <= len(data) - 9):
            continue
        block = bytes(data[candidate:candidate + 4])
        if block not in (b"\x00" * 4, b"\xFF" * 4):
            return candidate

    # New/default variable areas may still be blank before installation.
    for candidate in candidates:
        if 0 <= candidate <= len(data) - 9:
            return candidate
    die(f"invalid Rolling variables reference: 0x{reference:X}")
    raise AssertionError("unreachable")


def read_config(data: bytearray, vars_addr: int) -> Tuple[int, float]:
    vars_addr = normalize_vars_address(data, vars_addr)
    throttle_word = struct.unpack_from("<H", data, vars_addr)[0]
    rpm_raw = struct.unpack_from("<H", data, vars_addr + 2)[0]
    return round(rpm_raw / 4), decode_throttle(throttle_word)


def emit_code(data: bytearray, cave: int, vars_addr: int, trigger_addr: str, trigger_bit: int,
              wped: str, nmot: str, tsrldyn: str, old_tail: bytes,
              chain_target: Optional[int]) -> int:
    c = cave
    seq = [0x9A, offset2bit(trigger_addr) & 0xFF, 0x12, (trigger_bit << 4) & 0xFF, 0xC2, 0xF4]
    data[c:c + len(seq)] = bytes(seq); c += len(seq)
    c = put_word(data, c, parse_hex(wped) + 0x8000)
    data[c:c + 6] = b"\xD7\x00\x81\x00\xC2\xF9"; c += 6
    c = put_word(data, c, vars_addr)
    data[c:c + 6] = b"\x40\x49\xFD\x0A\xF2\xF4"; c += 6
    c = put_word(data, c, parse_hex(nmot))
    data[c:c + 6] = b"\xD7\x00\x81\x00\xF2\xF9"; c += 6
    c = put_word(data, c, vars_addr + 2)
    data[c:c + 6] = b"\x40\x49\xFD\x02\xF7\x8E"; c += 6
    c = put_word(data, c, parse_hex(tsrldyn) + 0x8000)
    if chain_target is not None:
        b1, b2, b3 = da_bytes(chain_target)
        data[c:c + 6] = bytes((0xDA, b1, b2, b3, 0xDB, 0x00)); c += 6
    else:
        data[c:c + 6] = bytes((0xF3, 0xF8, old_tail[0], old_tail[1], 0xDB, 0x00)); c += 6
    return c - cave


def detect_trigger(data: bytearray, cave: int, choices: Dict[str, Tuple[str, str, int, str]]) -> str:
    encoded_addr, encoded_bit = data[cave + 1], data[cave + 3] >> 4
    for key, (_, addr, bit, _) in choices.items():
        if offset2bit(addr) & 0xFF == encoded_addr and bit == encoded_bit:
            return key
    return "unknown"


def ask_int(label: str, current: int, low: int, high: int) -> int:
    while True:
        value = safe_input(f"{label} [{current}]: ").strip()
        if not value:
            return current
        try:
            number = int(value)
            if low <= number <= high:
                return number
        except ValueError:
            pass
        print(f"Enter a value from {low} to {high}.")


def ask_float(label: str, current: float, low: float, high: float) -> float:
    while True:
        value = safe_input(f"{label} [{current:.1f}]: ").strip().replace(",", ".")
        if not value:
            return current
        try:
            number = float(value)
            if low <= number <= high:
                return number
        except ValueError:
            pass
        print(f"Enter a value from {low:.1f} to {high:.1f}.")


def choose_trigger_menu(choices: Dict[str, Tuple[str, str, int, str]], current: str) -> str:
    preferred_order = ["cruise_res", "cruise_set", "brake", "clutch"]
    keys = [key for key in preferred_order if key in choices]
    keys.extend(key for key in choices if key not in keys)

    print()
    print("ACTIVATION TRIGGER")
    for index, key in enumerate(keys, 1):
        label, _, _, _ = choices[key]
        print(f"  {index}. {label}")

    default_index = keys.index(current) + 1 if current in keys else 1
    while True:
        raw = safe_input(
            f"Select Activation Trigger [1-{len(keys)}] [{default_index}]: "
        ).strip().lower()
        if not raw:
            return current if current in keys else keys[0]
        if raw.isdigit() and 1 <= int(raw) <= len(keys):
            return keys[int(raw) - 1]
        for key in keys:
            label = choices[key][0].lower()
            if raw in {key.lower(), label}:
                return key
        print(f"  Enter a value from 1 to {len(keys)}.")


def configure(
    rpm: int,
    throttle: float,
    trigger: str,
    choices: Dict[str, Tuple[str, str, int, str]],
) -> Tuple[int, float, str]:
    print()
    print("=" * 64)
    print("ROLLING ANTI-LAG CONFIGURATION".center(64))
    print("=" * 64)
    print()
    print("Current configuration:")
    print(f"  Rolling RPM             : {rpm} rpm")
    print(f"  Throttle Threshold      : {throttle:.1f} %")
    print(f"  Activation Trigger      : {choices.get(trigger, ('Unknown',))[0]}")
    print()
    print("Press ENTER to keep the current value.")
    print()

    configured_rpm = ask_int("Rolling RPM", rpm, 1000, 8000)
    configured_throttle = ask_float(
        "Throttle Threshold (%)", throttle, 10.0, 100.0
    )
    configured_trigger = choose_trigger_menu(choices, trigger)

    print()
    print("=" * 64)
    print("CONFIGURATION SUMMARY".center(64))
    print("=" * 64)
    print(f"  Rolling RPM             : {configured_rpm} rpm")
    print(f"  Throttle Threshold      : {configured_throttle:.1f} %")
    print(
        f"  Activation Trigger      : "
        f"{choices[configured_trigger][0]}"
    )
    print()

    if not prompt_yes_no("Save this configuration?", True):
        raise RuntimeError("Operation cancelled by the user.")

    return configured_rpm, configured_throttle, configured_trigger


def main() -> None:
    ap = argparse.ArgumentParser(description="ME7 Rolling Anti-Lag CMD Utility")
    ap.add_argument("bin", type=Path)
    ap.add_argument("ecu", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    banner()

    if not args.bin.exists() or not args.ecu.exists():
        die("BIN or ECU file does not exist")
    data = bytearray(args.bin.read_bytes())
    if len(data) != 1048576:
        die(f"expected 1 MB BIN (1048576 bytes), received {len(data)} bytes")
    ecu = prepare_ecu(args.ecu.read_text(errors="replace"))

    tsrldyn = ecu_required(ecu, "tsrldyn")
    nmot = ecu_required(ecu, "nmot_w")
    wped = ecu_addr(ecu, "wped")
    if wped is None:
        dwped = ecu_addr(ecu, "dwped")
        if dwped is None:
            die("wped and dwped not found")
        wped = f"{parse_hex(dwped) + 2:X}"

    choices = available_triggers(ecu)
    if not choices:
        die("no supported trigger found in ECU definition")

    hook = find_hook(data)
    hook_target = read_da(data, hook)
    installed = is_rolling_code(data, hook_target)
    launch_function, launch_config = find_setzi_launch(data)

    print(f"\nBIN:       {args.bin.name}")
    print(f"Hook:      0x{hook:X}")
    print(f"nmot_w:    0x{parse_hex(nmot):06X}")
    print(f"wped:      0x{parse_hex(wped):06X}")
    print(f"tsrldyn:   0x{parse_hex(tsrldyn):06X}")
    print_trigger_addresses(choices)

    if installed and hook_target is not None:
        cave = hook_target
        vars_reference = struct.unpack_from("<H", data, cave + 14)[0]
        vars_addr = normalize_vars_address(data, vars_reference)
        rpm, throttle = read_config(data, vars_addr)
        trigger = detect_trigger(data, cave, choices)
        ok("Rolling Anti-Lag detected")
        print(f"Code cave: 0x{cave:X}")
        print(f"Variables: 0x{vars_addr:X}")
    else:
        chain_target = hook_target
        mode = "CHAIN" if chain_target is not None else "SOLO"
        info(f"Rolling Anti-Lag not detected ({mode} mode available)")

        excluded_code: List[Tuple[int, int]] = []
        excluded_vars: List[Tuple[int, int]] = []

        if chain_target is not None:
            print(f"Existing Launch/ALS target: 0x{chain_target:X}")
            info("existing DA hook detected -> CHAIN MODE")
            info(f"old launch/ALS code cave target: 0x{chain_target:X}")
            excluded_code.append((chain_target, chain_target + 512))

            if launch_function is not None:
                excluded_code.append((launch_function, launch_function + 512))
                print(f"Detected Launch code cave: 0x{launch_function:X}")
            if launch_config is not None:
                # Launch_V2 reserves a 64-byte configuration/metadata area.
                excluded_vars.append((launch_config, launch_config + 64))
                print(f"Detected Launch configuration: 0x{launch_config:X}-0x{launch_config + 63:X}")

        cave = find_hole_separate(data, 256, 0, len(data) - 64, excluded_code)
        vars_addr = find_hole_separate(data, 32, 0x17000, 0x18000, excluded_vars)
        if cave is None:
            die("cannot find a separate free code cave for Rolling")
        if vars_addr is None:
            die("cannot find a separate free Rolling configuration area between 0x17000 and 0x18000")

        rpm, throttle, trigger = 3500, 90.2, "cruise_res" if "cruise_res" in choices else next(iter(choices))
        print(f"Allocated Rolling code cave: 0x{cave:X}")
        print(f"Allocated Rolling variables: 0x{vars_addr:X}-0x{vars_addr + 31:X}")
        info(f"Rolling code cave: 0x{cave:X}")
        info(f"Rolling vars: 0x{vars_addr:X}")

    info("Entering configuration mode.")
    rpm, throttle, trigger = configure(rpm, throttle, trigger, choices)

    label, trig_addr, trig_bit, symbol = choices[trigger]
    if not installed:
        chain_target = hook_target
        old_tail = bytes(data[hook + 2:hook + 4])
        write_da(data, hook, cave)
        written = emit_code(data, cave, vars_addr, trig_addr, trig_bit, wped, nmot, tsrldyn, old_tail, chain_target)
        print(f"Rolling code installed: {written} bytes ({'CHAIN' if chain_target is not None else 'SOLO'})")
    else:
        data[cave + 1] = offset2bit(trig_addr) & 0xFF
        data[cave + 3] = (trig_bit << 4) & 0xFF
        print("Existing Rolling configuration updated.")

    write_config(data, vars_addr, rpm, throttle)
    ftomn = find_ftomn(data)
    if ftomn:
        data[ftomn[0]] = 0x00
        print(f"FTOMN set to 0x00 at 0x{ftomn[0]:X}")
    else:
        print("WARNING: FTOMN was not found and was not changed.")

    # Final verification before saving.
    verified_rpm, verified_throttle = read_config(data, vars_addr)
    verified_trigger = detect_trigger(data, cave, choices)
    if verified_rpm != rpm:
        die(f"Rolling RPM verification failed: expected {rpm}, detected {verified_rpm}")
    if abs(verified_throttle - throttle) > 0.5:
        die(
            f"Throttle verification failed: expected {throttle:.1f} %, "
            f"detected {verified_throttle:.1f} %"
        )
    if verified_trigger != trigger:
        die(
            f"Trigger verification failed: expected {trigger}, "
            f"detected {verified_trigger}"
        )

    ok("Configuration written")
    ok(f"Rolling RPM verified: {verified_rpm} rpm")
    ok(f"Throttle Threshold verified: {verified_throttle:.1f} %")
    ok(f"Activation Trigger verified: {label}")

    # Verify the final execution chain.
    if read_da(data, hook) != cave:
        die("main hook verification failed: it does not point to Rolling")
    if not installed and chain_target is not None:
        chained = read_da(data, cave + 40)
        if chained != chain_target:
            die("CHAIN verification failed: Rolling does not continue to Launch")
        ok(f"Execution chain verified: hook -> Rolling 0x{cave:X} -> Launch 0x{chain_target:X}")
    elif not installed:
        ok(f"SOLO Rolling hook verified at 0x{cave:X}")

    out = args.output or args.bin.with_name(args.bin.stem + "_rolling_mod.bin")
    out.write_bytes(data)
    if out.read_bytes() != bytes(data):
        die("Output verification failed after writing the file")
    ok("Output file verified")
    ok(f"Saved: {out}")
    info(f"Result written successfully: {out}")
    print()
    print(f"Output file: {out}")
    warn("Checksums are NOT calculated by this program.")
    warn("Correct and verify checksums before flashing.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        fail("Operation cancelled by the user.")
        raise SystemExit(130)
    except (RuntimeError, OSError, ValueError) as exc:
        fail(str(exc))
        raise SystemExit(1)
