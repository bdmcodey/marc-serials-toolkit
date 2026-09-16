"""
Per-session file storage, shared by every application that keeps an upload.

A cataloguer's MARC file and their pattern library are both held on disk under
a random id recorded in their Flask session, and swept on a timer so nothing
sits on a server longer than the work takes.

One sweep, one set of rules
---------------------------
This existed twice. The workbench learned in 0.9.1 that a pattern library is not
an upload -- it is work, a hundred decisions about a collection -- and gave it
its own far longer age limit, measured from last use. The converter's copy of
the sweep was not changed, and both applications default to the *same*
directory, so running the converter deleted libraries the workbench was keeping:
any library untouched for six hours, on any machine where both had been used.
The page had already read it and went on showing patterns the server no longer
had, and every record then converted with the standard parser.

There is now one sweep, and it applies each rule to the kind of file it is for.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from typing import Optional

from flask import session

# Where uploads and libraries live. Both applications read the same variable and
# default to the same directory, which is why the sweep below has to be right
# for every kind of file in it rather than for the one its caller had in mind.
UPLOAD_DIR = os.environ.get(
    "MARC_UPLOAD_DIR", os.path.join(tempfile.gettempdir(), "marc_uploads")
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# An uploaded MARC file is the cataloguer's data, and should not sit on a server
# any longer than the work takes.
UPLOAD_TTL_SECONDS = int(os.environ.get("MARC_UPLOAD_TTL", 6 * 3600))

# A pattern library is kept far longer, and measured from last use rather than
# from when it was written: a library someone converts with every week is in
# use, whether or not they have edited it.
LIBRARY_TTL_SECONDS = int(os.environ.get("MARC_LIBRARY_TTL", 30 * 86400))

# Which stored things are libraries rather than uploads.
LIBRARY_EXT = ".json"


def _upload_dir() -> str:
    """
    The current store, read at call time.

    Not a module constant in disguise: the tests repoint UPLOAD_DIR per test so
    one client cannot walk another's working set, and every path below has to
    follow.
    """
    return UPLOAD_DIR


def ttl_for(filename: str) -> int:
    """How long a stored file of this kind is kept."""
    return (LIBRARY_TTL_SECONDS if filename.endswith(LIBRARY_EXT)
            else UPLOAD_TTL_SECONDS)


def purge_old_stored_files() -> None:
    """
    Delete stored files past their age, each kind by its own limit.

    One sweep used to apply the upload limit to everything in the directory,
    which meant an upload could delete a pattern library that had taken an
    afternoon to confirm.
    """
    now = time.time()
    try:
        names = os.listdir(_upload_dir())
    except OSError:
        return
    for fname in names:
        fpath = os.path.join(_upload_dir(), fname)
        try:
            if now - os.path.getmtime(fpath) > ttl_for(fname):
                os.remove(fpath)
        except OSError:
            pass


def file_path(file_id: str, ext: str = ".mrc") -> str:
    """Absolute path for a stored file given its id."""
    return os.path.join(_upload_dir(), f"{file_id}{ext}")


def save_file(session_key: str, data: bytes, ext: str = ".mrc") -> None:
    """Write data to disk and record the file id in the session."""
    purge_old_stored_files()
    file_id = session.get(session_key)
    if not isinstance(file_id, str) or len(file_id) != 32:
        file_id = uuid.uuid4().hex
    session[session_key] = file_id
    with open(file_path(file_id, ext), "wb") as fh:
        fh.write(data)


def load_file(session_key: str, ext: str = ".mrc",
              refresh: bool = False) -> Optional[bytes]:
    """
    Read a stored file, or None.

    `refresh` marks it as still in use, so its age is measured from the last
    time it was wanted rather than from the last time it was written.  Reading
    is what a pattern library mostly gets: a cataloguer who converts with the
    same hundred patterns every week never rewrites them.
    """
    file_id = session.get(session_key)
    if not file_id:
        return None
    path = file_path(file_id, ext)
    if not os.path.exists(path):
        return None
    if refresh:
        try:
            os.utime(path, None)
        except OSError:
            pass                     # read-only store; the read still works
    with open(path, "rb") as fh:
        return fh.read()
