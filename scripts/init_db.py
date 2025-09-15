#!/usr/bin/env python3

import os
import sys

from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("usage: ./init_db.py /path/to/open5gs")
        return 1
    open5gs = Path(sys.argv[1])
    dbctl = open5gs.joinpath("misc", "db", "open5gs-dbctl")
    if not dbctl.is_file():
        print(f"{dbctl}: no such script file")
        return 1

    imsi = 999700000000001
    for i in range(1000):
        os.system(f"{dbctl} add {imsi + i} 465B5CE8B199B49FAA5F0A2EE238A6BC "
                   "E8ED289DEBA952E4283B54E88E6183CA")

    return 0


if __name__ == "__main__":
    sys.exit(main())
