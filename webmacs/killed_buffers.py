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

import collections
from PyQt6.QtCore import QDataStream, QByteArray, QIODevice
from .webbuffer import create_buffer
from . import variables, hooks


max_size = variables.define_variable(
    "revive-buffers-limit",
    "The maximum number of killed buffers that can be revived."
    " If set to a -1, there is no limit. Default to 10.",
    10,
    type=variables.Int(min=-1),
    callbacks=(
        lambda v: KilledBuffer.update_max_size(v.value)
    ),
)


class KilledBuffer(object):
    all = collections.deque(maxlen=max_size.value)

    @classmethod
    def update_max_size(cls, nb):
        new_all = collections.deque(maxlen=nb if nb >= 0 else None)
        for item in reversed(cls.all):
            new_all.appendleft(item)
        cls.all = new_all

    def __init__(self, url, title, icon, history_data, delayed):
        self.url = url
        self.title = title
        self.icon = icon
        self.history_data = history_data
        self.delayed = delayed
        self.all.appendleft(self)

    @classmethod
    def from_buffer(cls, buff):
        data = QByteArray()
        stream = QDataStream(data, QIODevice.OpenModeFlag.WriteOnly)
        stream << buff.history()

        return cls(
            buff.url(),
            buff.title(),
            buff.icon(),
            data,
            buff.delayed_loading_url()
        )

    def revive(self):
        buff = create_buffer()
        stream = QDataStream(self.history_data, QIODevice.OpenModeFlag.ReadOnly)
        stream >> buff.history()
        self.all.remove(self)

        if self.delayed:
            buff.load(self.delayed.url)
        return buff


hooks.webbuffer_closed.add(KilledBuffer.from_buffer)
