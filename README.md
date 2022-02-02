# pkptools

## tools for pkp files

### pkpextract.py

#### Extract information from a .pkp file.

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

Author: gianni ferrarotti <gianni.ferrarotti@gmail.com>
License: MIT
