"""INSECURE test double for subprocess wiring; never use for real backups.

This intentionally is not encryption. Real encryption must use GnuPG.
"""

import hashlib
import sys
from pathlib import Path


args = sys.argv[1:]
if "--encrypt" in args:
    target = Path(args[args.index("--output") + 1])
    raw = sys.stdin.buffer.read()
    target.write_bytes(b"ZYVFAKE1" + hashlib.sha256(raw).digest() + raw[::-1])
elif "--decrypt" in args:
    archive = Path(args[-1]).read_bytes()
    if not archive.startswith(b"ZYVFAKE1"):
        sys.exit(2)
    data = archive[40:][::-1]
    if hashlib.sha256(data).digest() != archive[8:40]:
        sys.exit(2)
    if "--output" in args:
        Path(args[args.index("--output") + 1]).write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
else:
    sys.exit(2)
