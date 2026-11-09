#!/usr/bin/env python3
"""Write a viewer-friendly copy of a VASP POSCAR/CONTCAR with plain element symbols.

Some VASP builds (seen with VASP 6.4.2) write the species line as POTCAR
labels with a hash, e.g. ``Ti_sv/0a1b2c3d4  O/5e6f7a8b9c``,
which VESTA cannot read. This script keeps the original untouched and writes
``<name>_vesta.vasp`` next to it with each label reduced to its element
symbol (``Ti O``). Only the species line changes.

Usage:
    python3 clean_poscar_species.py CONTCAR [POSCAR ...]

For each file it prints one line: WROTE <copy>, CLEAN <file> (already plain
symbols, no copy needed) or ERROR <file>: <reason>. Exit code 0 when no file
had an error, 1 otherwise. Unrecognised labels are errors, never guessed.
Python 3.6 compatible so it can run on HPC login nodes.
"""

import os
import re
import sys

ELEMENTS = set("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu
Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba
La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb
Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr
""".split())

# Element symbol at the start of a POTCAR label: Ti_sv/hash, Fe_pv, O_s, H.75, Ga_d_GW ...
LABEL = re.compile(r"^([A-Z][a-z]?)(?=$|[_./0-9])")


def element_of(label):
    m = LABEL.match(label)
    if not m or m.group(1) not in ELEMENTS:
        raise ValueError("unrecognised species label %r" % label)
    return m.group(1)


def clean(path):
    with open(path) as f:
        lines = f.read().splitlines(True)
    if len(lines) < 7:
        raise ValueError("too short for a POSCAR")
    species = lines[5].split()
    counts = lines[6].split()
    if not species or not all(c.isdigit() for c in counts):
        raise ValueError("line 6/7 are not species names and counts (VASP 4 format?)")
    if len(species) != len(counts):
        raise ValueError("%d species names but %d counts" % (len(species), len(counts)))
    symbols = [element_of(s) for s in species]
    if symbols == species:
        return None
    lines[5] = "  " + "  ".join(symbols) + "\n"
    stem = os.path.basename(path)
    out = os.path.join(os.path.dirname(path), stem + "_vesta.vasp")
    with open(out, "w") as f:
        f.writelines(lines)
    return out


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 1
    failed = False
    for path in argv:
        try:
            out = clean(path)
        except (OSError, ValueError) as e:
            print("ERROR %s: %s" % (path, e))
            failed = True
            continue
        print("CLEAN %s" % path if out is None else "WROTE %s" % out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
