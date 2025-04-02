# This file is part of webmacs.
#
# webmacs is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# webmacs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with webmacs.  If not, see <http://www.gnu.org/licenses/>.

import json
import logging
import shutil
import sys

from . import BUFFERS, windows, current_window
from .webbuffer import create_buffer, QUrl, DelayedLoadingUrl, close_buffer
from .window import Window


FORMAT_VERSION = 2


def _session_load(stream):
    data = json.load(stream)
    version = data.get("version", 0)
    urls = data["urls"]
    if version < 2:
        urls = reversed(urls)

    # apply the session config

    # now, load urls in buffers
    for url in urls:
        # new format, url must be a dict
        buff = create_buffer(DelayedLoadingUrl(
            url=QUrl(url["url"]),
            title=url["title"]
        ))
        if version >= 2:
            buff.last_use = url["last_use"]

    def create_window(wdata):
        win = Window()
        win.restore_state(wdata, version)
        win.show()

    current_index = data.get("current-window", 0)
    for i, wdata in enumerate(data["windows"]):
        if i != current_index:
            create_window(wdata)

    # create the current last, so it has focus and is on top
    create_window(data["windows"][current_index])


def _session_save(stream):
    win_state = [w.dump_state() for w in windows()]
    urls = [{
        "url": b.url().toString(),
        "title": b.title(),
        "last_use": b.last_use,
    } for b in BUFFERS]

    try:
        win_index = windows().index(current_window())
    except Exception:
        if current_window() is None:
            win_index = 0
    json.dump({
        "version": FORMAT_VERSION,
        "urls": urls,
        "windows": win_state,
        "current-window": win_index,
    }, stream)


def session_clean():
    # clean every opened buffers and windows
    for window in windows():
        window.quit_if_last_closed = False
        window.close()
        for view in window.webviews():
            view.setBuffer(None)

    for buffer in BUFFERS:
        close_buffer(buffer)


def session_load(session_file):
    """
    Try to load the session, given the profile.

    Must be called at application startup, when no buffers nor views is set up
    already.
    """
    if session_file is None:
        return
    try:
        with open(session_file) as f:
            _session_load(f)
    except Exception:
        logging.warning("Unable to load the session from %s. Trying backup",
                        session_file)
        try:
            session_file = session_file + ".backup"
            with open(session_file) as f:
                _session_load(f)
        except Exception:
            logging.exception("Unable to load the session from backup file %s.",
                              session_file)
            y_n = None
            while y_n not in {"y", "n"}:
                y_n = input("Could not load from backup. Create new session? (y/n)")
            if y_n == "y":
                session_clean()
            else:
                sys.exit(1)


def session_save(session_file):
    if session_file is None:
        return
    """
    Save the session for the given profile.
    """
    shutil.copy(session_file, session_file + ".backup")
    with open(session_file, "w") as f:
        _session_save(f)
