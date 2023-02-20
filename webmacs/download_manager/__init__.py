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

import os
import re
import json
import shlex
import logging
import itertools
import tempfile

from PyQt6.QtCore import QObject, pyqtSlot as Slot, pyqtSignal as Signal, \
    QProcess

from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest

from .prompts import (DlChooseActionPrompt, DlOpenActionPrompt, DlPrompt,
                      OverwriteFilePrompt)
from .. import current_minibuffer, hooks, variables


default_download_dir = variables.define_variable(
    "default-download-dir",
    "Change the default download dir.",
    "",
    type=variables.String(),
)

TEMPORARY_DOWNLOAD_DIR = None
keep_temporary_download_dir = variables.define_variable(
    "keep-temporary-download-dir",
    "If set to True, the download dir proposed will be the last used one.",
    False,
    type=variables.Bool(),
)


def dl_path(dl):
    return os.path.join(dl.downloadDirectory(), dl.suggestedFileName())


def get_user_download_dir():
    """
    Returns the directory the user wants to put its download in.

    Return None if there is no specific directory.
    """
    if keep_temporary_download_dir.value and TEMPORARY_DOWNLOAD_DIR:
        return TEMPORARY_DOWNLOAD_DIR
    return default_download_dir.value or None



def find_unique_suggested_path(dirname, filename):
    """
    Do the same logic as chromium does to create a unique path suggestion.
    """
    fnames = set(os.listdir(dirname))

    if filename not in fnames:
        return os.path.join(dirname, filename)

    parts = filename.split(".", 1)
    if len(parts) == 1:
        name, ext = parts[0], ""
    else:
        name, ext = parts[0], "." + parts[1]

    counter = itertools.count(1)
    while True:
        newfname = "{}({}){}".format(name, next(counter), ext)
        if newfname not in fnames:
            return os.path.join(dirname, newfname)


STATE_STR = {
    QWebEngineDownloadRequest.DownloadState.DownloadRequested: "Requested",
    QWebEngineDownloadRequest.DownloadState.DownloadInProgress: "In progress",
    QWebEngineDownloadRequest.DownloadState.DownloadCompleted: "Completed",
    QWebEngineDownloadRequest.DownloadState.DownloadCancelled: "Cancelled",
    QWebEngineDownloadRequest.DownloadState.DownloadInterrupted: "Interrupted",
}


def state_str(state):
    return STATE_STR.get(state, "Unknown state")


def download_to_json(dlitem):
    try:
        progress = (round(dlitem.receivedBytes() / float(dlitem.totalBytes())
                          * 100, 2))
    except ZeroDivisionError:
        progress = -1
    return json.dumps({
        "path": dl_path(dlitem),
        "state": state_str(dlitem.state()),
        "id": dlitem.id(),
        "isFinished": dlitem.isFinished(),
        "progress": progress,
    })


class DownloadManager(QObject):
    download_started = Signal(object)

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        self.downloads = []
        self._buffers = []  # list of web buffers currently showing downloads
        self._running_procs = {}

        def on_buffer_load_finished(buff):
            url = buff.url()
            if url.scheme() == "webmacs" and url.authority() == "downloads":
                self.attach_buffer(buff)
            else:
                self.detach_buffer(buff)

        hooks.webbuffer_load_finished.add(on_buffer_load_finished)
        hooks.webbuffer_closed.add(self.detach_buffer)

    def attach_buffer(self, buffer):
        self._buffers.append(buffer)
        for dl in self.downloads:
            buffer.runJavaScript("add_download(%s);" % download_to_json(dl))

    def detach_buffer(self, buffer):
        try:
            self._buffers.remove(buffer)
        except ValueError:
            pass

    def _start_download(self, dlitem):
        dlitem.accept()
        self.downloads.append(dlitem)
        dlitem.destroyed.connect(lambda: self.downloads.remove(dlitem))
        self.download_started.emit(dlitem)
        dl = download_to_json(dlitem)
        for buffer in self._buffers:
            buffer.runJavaScript("add_download(%s);" % dl)
        dlitem.receivedBytesChanged.connect(self._download_state_changed)
        dlitem.totalBytesChanged.connect(self._download_state_changed)
        dlitem.stateChanged.connect(self._download_state_changed)
        dlitem.isFinishedChanged.connect(self._download_state_changed)
        dlitem.isFinishedChanged.connect(dlitem.deleteLater)

    @Slot()
    def _download_state_changed(self):
        dlitem = self.sender()
        dl = download_to_json(dlitem)
        for buffer in self._buffers:
            buffer.runJavaScript("update_download(%s);" % dl)

    @Slot("QWebEngineDownloadRequest*")
    def download_requested(self, dl):
        minibuff = current_minibuffer()

        prompt = DlChooseActionPrompt(os.path.join(dl.downloadDirectory(),
                                                   dl.suggestedFileName()),
                                      dl.mimeType())
        action = minibuff.do_prompt(prompt)

        if action == "open":
            prompt = DlOpenActionPrompt()
            executable = minibuff.do_prompt(prompt)
            if executable is None:
                return

            dl.setDownloadDirectory(tempfile.gettempdir())
            
            logging.info(f"Downloading {dl_path(dl)}...")

            def finished():
                if dl.state() == \
                   QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
                    logging.info(
                        f"Opening external file {dl_path(dl)} with {executable}")
                    self._run_program(executable, dl_path(dl))

            dl.isFinishedChanged.connect(finished)
            self._start_download(dl)

        elif action == "download":
            dl_dir = get_user_download_dir() or dl.downloadDirectory()
            name = dl.suggestedFileName()
            path = os.path.join(dl_dir, name)
            if os.path.exists(path):
                try:
                    path = find_unique_suggested_path(dl_dir, name)
                except OSError as exc:
                    logging.warning(
                        "Can't use user_dir %s: %s", user_dir, str(exc)
                    )

            prompt = DlPrompt(path, dl.mimeType())
            path = minibuff.do_prompt(prompt)
            if path is None:
                return

            if os.path.isdir(path):
                path = find_unique_suggested_path(path, name)

            if os.path.isfile(path):
                if not minibuff.do_prompt(OverwriteFilePrompt(path)):
                    return

            dl.setDownloadDirectory(os.path.dirname(path))
            dl.setDownloadFileName(os.path.basename(path))
            if keep_temporary_download_dir.value:
                global TEMPORARY_DOWNLOAD_DIR
                TEMPORARY_DOWNLOAD_DIR = os.path.dirname(path)

            logging.info("Downloading %s...", path)

            def finished():
                state = state_str(dl.state())
                logging.info("Finished download [%s] of %s", state, dl_path(dl))
                minibuff.show_info("[{}] download: {}".format(state,
                                                              dl_path(dl)))
            dl.isFinishedChanged.connect(finished)
            self._start_download(dl)

    def _run_program(self, executable, path):
        shell_arg = "{} {}".format(executable, shlex.quote(path))
        args = ["-c", shell_arg]
        shell = get_shell()
        proc = QProcess()
        self._running_procs[proc] = path

        logging.debug("Executing command: %s %s", shell, " ".join(args))

        proc.finished.connect(self._program_finished)
        proc.start(shell, args, QProcess.OpenModeFlag.ReadOnly)

    @Slot(int, QProcess.ExitStatus)
    def _program_finished(self, code, status):
        proc = self.sender()
        path = self._running_procs.pop(proc)
        logging.debug("Removing downloaded file %s", path)
        try:
            os.unlink(path)
        except Exception:
            pass


def get_shell():
    return os.environ.get("SHELL", "/bin/sh")
