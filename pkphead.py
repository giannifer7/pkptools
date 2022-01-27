#!/usr/bin/env python3

"""
Write the first (default 8k) bytes of the head
of uncompressed pkp files to one file.
The head is the initial part before pdf or python parts.

Author: gianni ferrarotti <gianni.ferrarotti@gmail.com>
License: MIT
"""

import re
import sys
import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import cast, BinaryIO, List, TextIO

from pkpextract import (
    PkpToolsOptions,
    PkpTools,
    PartType,
    pdfFinder,
    pythonFinder,
)


@dataclass
class PkpHeadOptions(PkpToolsOptions):
    """Options for PkpHead."""

    head: bytes
    headsize: int
    byversion: bool

    def __init__(self, args: argparse.Namespace):
        super().__init__(args)
        self.head = args.head
        self.headsize = args.headsize
        self.byversion = args.byversion


def openHeadFile(fileName: str) -> BinaryIO:
    """Open a binary file for output in outdir,
    with the same name as the fileName param.
    Create the needed directories."""

    opath = Path(fileName)
    odir = opath.parent
    if not odir.is_dir():
        odir.mkdir(parents=True)
    return open(opath, "wb")


_versionRE = re.compile(b"Version=([^,]+),")


def extractVersion(buf: bytes) -> bytes:
    mo = _versionRE.search(buf)
    if mo is None:
        return b"<unknown-version>"
    return mo.group(1)


class PkpHead(PkpTools):
    """PkpHead app class.
    Process the input files according to the options."""

    def __init__(
        self,
        options: PkpHeadOptions,
        statsFile: TextIO,
        inputFiles: List[str],
        outdir: str,
    ):
        """Setup the options, stats, input files, and out dir."""
        super().__init__(options, statsFile, inputFiles, outdir)
        print(cast(PkpHeadOptions, self.options).head)  # bah
        print(self.options.headsize)

    def processBuf(
        self, fileName: str, buf: bytes, bufLen: int
    ) -> tuple[PartType, int, int]:
        pdfSize, _, pdfSlices = pdfFinder(buf)
        pythonSize, _, pythonSlices = pythonFinder(pdfSlices.head)
        if pythonSize > 0:
            comeFirst = PartType.python
            # --- pdf.head ---
            # head, python, body, pdf, tail = pythonSlices + pdfSlices[1:]
            head = pythonSlices[0]
        else:  # python not in the head
            comeFirst = PartType.pdf
            # pythonSize, _, pythonSlices = pythonFinder(pdfSlices.tail)
            #          ---- pdf.tail ----
            # head, pdf, body, python, tail = pdfSlices[:2] + pythonSlices
            head = pdfSlices[0]
        if self.options.head:
            self.writeHead(head)
        if self.options.byversion:
            self.writeByversion(fileName, head)
        return comeFirst, pdfSize, pythonSize

    def writeByversion(self, fileName: str, head: bytes) -> None:
        version = extractVersion(head).decode("utf-8")
        opath = self.outdir / version
        # / Path(fileName).stem
        # odir = opath.parent
        if not opath.is_dir():
            opath.mkdir(parents=True)
        with open(opath / Path(fileName).stem, "wb") as ostream:
            ostream.write(head)

    def writeHead(self, head):
        hsz = self.options.headsize
        lh = len(head)
        if lh < hsz:
            self.headfh.write(head)
            self.headfh.write(b"\0" * (hsz - lh))
        else:
            self.headfh.write(head[:hsz])

    def run(self) -> None:
        if self.options.head:
            with openHeadFile(self.options.head) as self.headfh:
                super().run()
        else:
            super().run()


def run(statsFile: TextIO, args: argparse.Namespace) -> None:
    options = PkpHeadOptions(args)
    app = PkpHead(options, statsFile, args.inputFiles, args.outdir)
    app.run()


def main():
    arg_parser = argparse.ArgumentParser()
    _stats_default = "pkpextract.stats.txt"
    _outdir_default = "."
    arg_parser.add_argument(
        "--stats",
        default=_stats_default,
        help=f"Statistics file, defaults to '{_stats_default}'.",
    )
    arg_parser.add_argument(
        "--outdir",
        default=_outdir_default,
        help=f"Where to write the results, defaults to '{_outdir_default}'.",
    )
    arg_parser.add_argument(
        "--quiet", action="store_true", help="Suppress the progressbar."
    )
    arg_parser.add_argument(
        "--write_unzipped",
        action="store_true",
        help="Write decompressed copies of inputs.",
    )
    arg_parser.add_argument(
        "--nopdf", action="store_true", help="Don't generate the pdf file."
    )
    arg_parser.add_argument(
        "--nopython",
        action="store_true",
        help="Don't generate the python file.",
    )
    arg_parser.add_argument(
        "--head",
        default="",
        help="Where to write all the initial <headsize> bytes of the unzipped \
inputs concatenated.",
    )
    arg_parser.add_argument(
        "--headsize",
        type=int,
        default=8 * 1024,
        help="Size of the initial part of the unzipped inputs \
to store in head.",
    )
    arg_parser.add_argument(
        "inputFiles", nargs="*", help="The '.pkp' files to process."
    )
    arg_parser.add_argument(
        "--byversion",
        action="store_true",
        help="Write the heads in different directories by version.",
    )
    args = arg_parser.parse_args()
    if not args.inputFiles:
        arg_parser.print_help()
        sys.exit(2)
    with open(args.stats, "w") as statsFile:
        try:
            run(statsFile, args)
        except OSError as err:
            print(f"OS error: {err}")
        except BaseException as err:
            print(f"Unexpected {err=}, {type(err)=}")
            raise


if __name__ == "__main__":
    main()
