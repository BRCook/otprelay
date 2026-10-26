#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verify the GPG signature embedded in an AppImage.

appimagetool signs the hex SHA-256 of the file as it was before the signature
and signing key were written into the ELF sections .sha256_sig and .sig_key.
So verification means: read those two sections out, hash the file with both
sections treated as zero bytes, and check the signature against that hex
digest with gpg. Nothing outside the Python standard library and gpg is used.

Usage: verify-appimage.py FILE.AppImage [--key PUBLIC_KEY_FILE]

With --key, the key is imported into a throwaway keyring first, so the check
passes only if that exact key signed the file. Without it, your own keyring
is used.
"""

import argparse
import hashlib
import os
import struct
import subprocess
import sys
import tempfile


def elf_sections(path):
    """Return {name: (offset, size)} for every section in an ELF64 file."""
    with open(path, "rb") as f:
        ident = f.read(16)
        if ident[:4] != b"\x7fELF" or ident[4] != 2:
            raise SystemExit("not a 64-bit ELF file (is this an AppImage?)")
        little = ident[5] == 1
        e = ("<" if little else ">")
        f.seek(0x28)
        shoff = struct.unpack(e + "Q", f.read(8))[0]
        f.seek(0x3A)
        shentsize, shnum, shstrndx = struct.unpack(e + "HHH", f.read(6))
        headers = []
        for i in range(shnum):
            f.seek(shoff + i * shentsize)
            name_idx, _type, _flags, _addr, offset, size = struct.unpack(
                e + "IIQQQQ", f.read(40))
            headers.append((name_idx, offset, size))
        _, str_off, str_size = headers[shstrndx]
        f.seek(str_off)
        strtab = f.read(str_size)
    out = {}
    for name_idx, offset, size in headers:
        end = strtab.index(b"\0", name_idx)
        out[strtab[name_idx:end].decode()] = (offset, size)
    return out


def section_text(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(size).rstrip(b"\0")


def digest_without(path, holes):
    """SHA-256 of the file with the given (offset, size) ranges zeroed."""
    holes = sorted(holes)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        pos = 0
        for offset, size in holes:
            h.update(f.read(offset - pos))
            h.update(b"\0" * size)
            f.seek(offset + size)
            pos = offset + size
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("appimage")
    ap.add_argument("--key",
                    help="public key file the signature must come from")
    args = ap.parse_args()

    sections = elf_sections(args.appimage)
    try:
        sig_off, sig_len = sections[".sha256_sig"]
        key_off, key_len = sections[".sig_key"]
    except KeyError:
        raise SystemExit(
            "no signature sections in this file; not a type-2 AppImage")
    signature = section_text(args.appimage, sig_off, sig_len)
    if not signature:
        raise SystemExit("this AppImage is not signed")

    hexdigest = digest_without(args.appimage,
                               [(sig_off, sig_len), (key_off, key_len)])

    with tempfile.TemporaryDirectory() as tmp:
        sig_path = os.path.join(tmp, "sig.asc")
        msg_path = os.path.join(tmp, "digest")
        with open(sig_path, "wb") as f:
            f.write(signature)
        with open(msg_path, "w") as f:
            f.write(hexdigest)
        env = dict(os.environ)
        if args.key:
            env["GNUPGHOME"] = os.path.join(tmp, "gnupg")
            os.mkdir(env["GNUPGHOME"], 0o700)
            subprocess.run(["gpg", "--batch", "--quiet", "--import", args.key],
                           env=env, check=True)
        result = subprocess.run(
            ["gpg", "--batch", "--verify", sig_path, msg_path],
            env=env, capture_output=True, text=True)
        print(result.stderr.strip())
        if result.returncode != 0:
            raise SystemExit("\nBAD: the signature does not verify")
        print("\nOK: the signature verifies for digest", hexdigest)


if __name__ == "__main__":
    main()