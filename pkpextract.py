#!/usr/bin/env python3

"""Extract information from a .pkp file.
usage: pkpextract.py [-h] [--stats STATS] [--outdir OUTDIR] [--write_unzipped]
    [--nopdf] [--nopython] [inputFiles ...]

pkpextract.py filename.pkp generate filename.py and filename.pdf
in the directory specified by --outdir, which defaults to ".",
unless --nopython or --nopdf are specified.
The --write_unzipped option produces filename,
containing the uncompressed filename.pkp,
equivalent to gunzip --suffix=.pkp --keep filename.pkp.
Some statistics are generated, by default in pkpextract.stats.txt,
but you can change it with the --stats=<somefile> option.
The stat file is a csv with the following fields:
filename, unzipped_len, status, pdf_size, python_size, time_elapsed.
status is pdfFirst, pythonFirst, or ERROR.
The python part is checked only if

Author: gianni ferrarotti <gianni.ferrarotti@gmail.com>
License: MIT
"""

import argparse
import ast
from dataclasses import dataclass
from enum import auto, IntEnum
import gzip
import re
import sys
from pathlib import Path
from time import perf_counter
from typing import BinaryIO, List, TextIO, NamedTuple, Callable

BROKEN_PYTHON_FILE_EXT = ".with-errors.py"

IntPair = tuple[int, int]


# Types of the functions that recognize the file parts.
StartFinder = Callable[[bytes], int]
EndFinder = Callable[[bytes, int], int]


class WeirdFileError(RuntimeError):
    pass


class Status(IntEnum):
    pythonFirst = auto()
    pdfFirst = auto()
    ERROR = auto()


# The pdf part starts with %PDF- and ends with the second %%EOF.
_pdfEndRE = re.compile(b"%%EOF")


def _pdfPartMaybeEnd(buf: bytes, *args) -> int:
    """Match an %%EOF in a pdf part.
    return -1 if not found."""

    mo = _pdfEndRE.search(buf, *args)
    if mo is None:
        return -1
    return mo.end()


def pdfPartEnd(buf: bytes, *args) -> int:
    """Match the end of a pdf part.
    return -1 if not found."""

    halfEnd = _pdfPartMaybeEnd(buf, *args)
    if halfEnd == -1:
        return -1
    return _pdfPartMaybeEnd(buf, halfEnd)


# The python part starts with a 'from xxx import', 'import yyy'
# and ends at a binary 1.
_pythonStartRE = re.compile(rb'(from (\w|\.)+ import)|(import (\w|\.)+)', re.ASCII)


def pythonPartStart(buf: bytes) -> int:
    """Match the start of a python part.
    return -1 if not found."""
    BACKWARD_LOOKUP_LEN = 25
    importPos = buf.find(b'import ')
    if importPos == -1:
        return -1
    if importPos < BACKWARD_LOOKUP_LEN:
        raise WeirdFileError()

    mo = _pythonStartRE.search(buf, importPos - BACKWARD_LOOKUP_LEN)
    if mo is None:
        return -1
    return mo.start()


def find_range(buf: bytes, find_start: StartFinder, find_end: EndFinder) -> IntPair:
    """Find the range in buf enclosed by
    the matches of find_start and find_end."""

    start = find_start(buf)
    if start == -1:
        return -1, -1
    return start, find_end(buf, start)


class BufTriplet(NamedTuple):
    """A triplet of buffers."""

    head: bytes
    body: bytes
    tail: bytes


def splitBuf(buf: bytes, _range: IntPair) -> BufTriplet:
    """Split buf in 3 parts, the middle one being spanned by _range."""

    a, b = _range
    return BufTriplet(buf[:a], buf[a:b], buf[b:])


class SplittingFinder:
    """A function class for splitting a buf in 3 parts,
    according to the finder functions given in __init__."""

    def __init__(self, find_start: StartFinder, find_end: EndFinder):
        """Set the finder functions to use in  __call__."""

        self.find_start: StartFinder = find_start
        self.find_end: EndFinder = find_end

    def __call__(self, buf: bytes) -> tuple[int, BufTriplet]:
        """Return size of the span, span and a triplet of buffers
        in which the span splits buf."""

        span = find_range(buf, self.find_start, self.find_end)
        return rangeSize(span), splitBuf(buf, span)


# function objects to find the span and corresponding subdivisions of a buffer
pdfFinder = SplittingFinder(lambda x: x.find(b"%PDF-"), pdfPartEnd)
pythonFinder = SplittingFinder(pythonPartStart, lambda x, start: x.find(b"\x01", start))


def rangeSize(x: IntPair) -> int:
    return x[1] - x[0]


def progress(count, total, status=""):
    """Progressbar.
    by Vladimir Ignatyev, License: MIT
    https://gist.github.com/vladignatyev/06860ec2040cb497f0f3"""

    bar_len = 60
    filled_len = int(round(bar_len * count / float(total)))
    percents = round(100.0 * count / float(total), 1)
    bar = "=" * filled_len + "-" * (bar_len - filled_len)
    sys.stdout.write("[%s] %s%s %s\r" % (bar, percents, "%", status))
    sys.stdout.flush()


class StatRow(NamedTuple):
    """A row in statsData, for statistics."""

    bufLen: int
    pdfSize: int
    pythonSize: int
    time_elapsed: float


def pythonPartIsbroken(source: bytes, filename) -> str:
    """Check that python part compiles at least.
    Return an error message, empty when no error occurred.
    Beware that it can still be broken in other ways.
    see the warning in https://docs.python.org/3/library/ast.html"""

    baseFilename = Path(filename).name
    result = ""
    try:
        ast.parse(source, baseFilename)
        return result
    except (ValueError, UnicodeDecodeError) as exc:
        result = f"{baseFilename}: {type(exc).__name__}: {exc}"
    except SyntaxError as exc:
        result = f"{exc.filename}, line {exc.lineno}: {type(exc).__name__}: {exc.text}"
    except BaseException as exc:
        result = f"{type(exc).__name__} in {baseFilename}: {exc}"
    return result


@dataclass
class PkpToolsOptions:
    """Options for PkpTools."""

    quiet: bool
    write_unzipped: bool
    write_pdf: bool
    write_python: bool
    check_python: bool

    def __init__(self, args: argparse.Namespace):
        self.quiet = args.quiet
        self.write_unzipped = args.write_unzipped
        self.write_pdf = not args.nopdf
        self.check_python = args.check_python
        self.write_python = not args.nopython and not self.check_python


class PkpTools:
    """PkpTools app class.
    Process the input files according to the options."""

    def __init__(
        self,
        options: PkpToolsOptions,
        statsFile: TextIO,
        inputFiles: List[str],
        outdir: str,
    ):
        """Setup the options, stats, input files, and out dir."""

        self.options: PkpToolsOptions = options
        self.statsFile: TextIO = statsFile
        self.inFileNames: List[str] = inputFiles
        self.outdir: Path = Path(outdir)
        self.statsData: List[StatRow] = []
        self.rejected: List[str] = []

    def outFileName(self, fileName: str, ext) -> str:
        """The file in outdir, with the same name as the fileName param."""
        return str(self.outdir / (Path(fileName).stem + ext))

    def _openOutFile(self, outPath: str) -> BinaryIO:
        """Open a binary file for output in outdir,
        with the same name as the fileName param.
        Create the needed directories."""

        odir = Path(outPath).parent
        if not odir.is_dir():
            odir.mkdir(parents=True)
        return open(outPath, "wb")

    def writeToFile(self, fileName: str, ext: str, buf: bytes) -> None:
        """Write buf to fileName + ext in outdir."""

        with self._openOutFile(self.outFileName(fileName, ext)) as ostream:
            ostream.write(buf)

    def processBuf(
        self, fileName: str, buf: bytes, bufLen: int
    ) -> tuple[Status, int, int]:
        """Process a buffer:
        - write unzipped if given the option --write_unzipped,
        - find pdf and write it unless given --nopdf,
        - look for python in the part before the pdf,
          if not found look for python in the part after the pdf,
        - write python unless given --nopython
        - return info for the statistics."""

        if self.options.write_unzipped:
            self.writeToFile(fileName, ".unzipped", buf)
        pdfSize, pdfSlices = pdfFinder(buf)
        if self.options.write_pdf:
            self.writeToFile(fileName, ".pdf", pdfSlices.body)
        try:
            pythonSize, pythonSlices = pythonFinder(pdfSlices.head)
            if pythonSize > 0:  # python in the head
                status = Status.pythonFirst
                # --- pdf.head ---
                head, python, body, pdf, tail = pythonSlices + pdfSlices[1:]
            else:  # python not in the head
                status = Status.pdfFirst
                pythonSize, pythonSlices = pythonFinder(pdfSlices.tail)
                #          ---- pdf.tail ----
                head, pdf, body, python, tail = pdfSlices[:2] + pythonSlices
            self.writeToFile(fileName, ".head", head)
            if self.options.write_python or self.options.check_python:
                error = pythonPartIsbroken(pythonSlices.body, f"{fileName}.py")
                if error:
                    self.rejected.append(error)
                    status = Status.ERROR
                    if not self.options.quiet:
                        print(error)
                    self.writeToFile(fileName, BROKEN_PYTHON_FILE_EXT, pythonSlices.body)
                else:
                    if self.options.write_python:
                        self.writeToFile(fileName, ".py", pythonSlices.body)
        except WeirdFileError:
            status = Status.ERROR
            error = f'{fileName} has no recognizable python part.'
            if not self.options.quiet:
                print(error)
        return status, pdfSize, pythonSize

    def writeStats(
        self,
        fileName: str,
        bufLen: int,
        status: Status,
        pdfSize: int,
        pythonSize: int,
        time_elapsed: float,
    ) -> None:
        """Write a line in the stats file with:
        filename, unzipped_len, what_come_first, pdf_size, python_size, time_elapsed.
        Store sizes and time in statsData for the summary."""

        self.statsFile.write(
            f"{fileName},{bufLen},{status.name},{pdfSize},{pythonSize},{time_elapsed:.5f}\n"
        )
        self.statsData.append(StatRow(bufLen, pdfSize, pythonSize, time_elapsed))

    def writeStatsSummary(self) -> None:
        """Write a statistics summary based on statsData."""

        self.statsFile.write("=" * 60)
        self.statsFile.write("\n")
        if not self.statsData:
            return
        first_name = self.inFileNames[0]
        first_r = self.statsData[0]
        totBufLen = 0
        maxBufLen = first_r.bufLen, first_name
        minBufLen = maxBufLen
        for name, r in zip(self.inFileNames, self.statsData):
            totBufLen += r.bufLen
            if r.bufLen > maxBufLen[0]:
                maxBufLen = r.bufLen, name
            if r.bufLen < minBufLen[0]:
                minBufLen = r.bufLen, name
        count = len(self.statsData)
        self.statsFile.write(f"number of files = {len(self.inFileNames)}\n")
        self.statsFile.write(f"max bufLen = {maxBufLen[0]:_} in {maxBufLen[1]}\n")
        self.statsFile.write(f"min bufLen = {minBufLen[0]:_} in {minBufLen[1]}\n")
        self.statsFile.write(f"total unzipped size = {totBufLen:_}\n")
        self.statsFile.write(f"average unzipped size = {int(totBufLen / count):_}\n")

    def run(self) -> None:
        """Main loop: unzip the input files and call processBuf for each,
        show a progressbar and write some statistics."""

        total = len(self.inFileNames)
        for count, fileName in enumerate(self.inFileNames):
            timer_start: float = perf_counter()
            if not self.options.quiet:
                progress(count, total)
            with gzip.open(fileName, "rb") as inFile:
                buf = inFile.read()
            bufLen = len(buf)
            status, pdfSize, pythonSize = self.processBuf(fileName, buf, bufLen)
            del buf
            timer_stop = perf_counter()
            self.writeStats(
                fileName,
                bufLen,
                status,
                pdfSize,
                pythonSize,
                timer_stop - timer_start,
            )
        self.writeStatsSummary()


def run(statsFile: TextIO, args: argparse.Namespace) -> None:
    options = PkpToolsOptions(args)
    app = PkpTools(options, statsFile, args.inputFiles, args.outdir)
    app.run()


def main():
    arg_parser = argparse.ArgumentParser()

    class Defaults:
        stats = "pkpextract.stats.txt"
        outdir = "result"

    arg_parser.add_argument(
        "--stats",
        default=Defaults.stats,
        help=f"Statistics file, defaults to '{Defaults.stats}'.",
    )
    arg_parser.add_argument(
        "--outdir",
        default=Defaults.outdir,
        help=f"Where to write the results, defaults to '{Defaults.outdir}'.",
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
        "--check_python",
        action="store_true",
        help=f"Check the python part. Write only the failed ones, with extensions '{BROKEN_PYTHON_FILE_EXT}'.",
    )
    arg_parser.add_argument(
        "inputFiles", nargs="*", help="The '.pkp' files to process."
    )
    args = arg_parser.parse_args()
    if not args.inputFiles:
        arg_parser.print_help()
        sys.exit(2)
    with open(args.stats, "w") as statsFile:
        try:
            run(statsFile, args)
        except OSError as exc:
            print(f"OS error: {exc}")
        except BaseException as exc:
            print(f"Unexpected {exc=}, {type(exc)=}")
            raise


if __name__ == "__main__":
    main()
