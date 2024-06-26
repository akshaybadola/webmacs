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

from PyQt6.QtWidgets import QWidget, QLineEdit, QHBoxLayout, QLabel, \
    QTableView, QHeaderView, QApplication, QSizePolicy, QFrame
from PyQt6.QtGui import QPainter
from PyQt6.QtCore import pyqtSignal as Signal, \
    QEvent, QSortFilterProxyModel, QRegularExpression, Qt, QModelIndex, \
    pyqtProperty

from ..keymaps import MINIBUFFER_KEYMAP as KEYMAP
from .prompt import Prompt
from .. import variables
from .. import windows
from ..keyboardhandler import LOCAL_KEYMAP_SETTER


class Popup(QTableView):
    def __init__(self, window, buffer_input):
        QTableView.__init__(self, window)
        # do not diplay more than one line in a cell, and elide text on middle
        # (best for urls)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        self.setVisible(False)
        self.setFrameStyle(QFrame.Shape.Box)
        self._window = window
        self._buffer_input = buffer_input
        window.installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setDefaultSectionSize(24)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self._max_visible_items = 10

    def _resize(self, size):
        # size is calculated given the window and the minibuffer input
        # geometries
        h = (24) * min(self._max_visible_items, self.model().rowCount()) + (
            2 * self.lineWidth())
        w = size.width()
        y = size.height() - h - self._buffer_input.height()

        self.setGeometry(0, y, w, h)

        # Split the columns width, nicer when we have at least two of them.
        cols = self.model().columnCount()
        if cols > 0:
            col_width = round(w / cols)
            for i in range(cols):
                self.setColumnWidth(i, int(col_width))

    def popup(self):
        self._resize(self._window.size())

        if not self.isVisible():
            self.show()

    def eventFilter(self, obj, event):
        # resize the popup when the window is resized
        if obj == self._window and event.type() == QEvent.Type.Resize:
            self._resize(event.size())
        return False


class MinibufferInput(QLineEdit):
    completion_activated = Signal(QModelIndex)

    FuzzyMatch = Prompt.FuzzyMatch
    SimpleMatch = Prompt.SimpleMatch

    def __init__(self, parent, window):
        QLineEdit.__init__(self, parent)
        self._completer_model = None
        self._popup = Popup(window, self)
        self.textEdited.connect(self._show_completions)
        self._popup.installEventFilter(self)
        self.installEventFilter(self)
        self._eat_focusout = False
        self._proxy_model = QSortFilterProxyModel(self)
        self._proxy_model.setFilterKeyColumn(-1)
        self._popup.setModel(self._proxy_model)
        self._popup.activated.connect(self._on_completion_activated)
        self._popup.selectionModel().currentRowChanged.connect(
            self._on_row_changed)
        self._right_italic_text = ""
        self._mark = False
        self.configure_completer({})

    def configure_completer(self, opts):
        self._popup._max_visible_items = opts.get("max-visible-items", 10)
        self._match = opts.get("match", self.SimpleMatch)
        self._autocomplete_single = opts.get("autocomplete-single", True)
        self._autocomplete = opts.get("autocomplete", False)
        if self._autocomplete:
            self._autocomplete_single = False
        self._complete_empty = opts.get("complete-empty", False)

    def keymap(self):
        prompt = self.parent()._prompt
        if prompt and prompt.keymap:
            return prompt.keymap
        return KEYMAP

    def eventFilter(self, obj, event):
        etype = event.type()
        if etype == QEvent.Type.FocusOut and obj == self \
           and self._eat_focusout and self._popup.isVisible():
            # keep the focus on the line edit
            return True
        elif etype == QEvent.Type.MouseButtonPress:
            # if we've clicked in the widget (or its descendant), let it handle
            # the click
            pos = obj.mapToGlobal(event.pos())
            target = QApplication.widgetAt(pos)
            if target and (self.isAncestorOf(target) or target == self):
                if not self._popup.underMouse():
                    self._popup.hide()
                target.event(event)
                return True

            if not self._popup.underMouse():
                self._popup.hide()
                return True
        elif etype in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            # send event to the line edit
            self._eat_focusout = True
            self.event(event)
            self._eat_focusout = False
            return True

        return QLineEdit.eventFilter(self, obj, event)

    def event(self, evt):
        t = evt.type()
        if t == QEvent.Type.Show:
            LOCAL_KEYMAP_SETTER.minibuffer_input_focus_changed(self, True)
        elif t == QEvent.Type.Hide:
            LOCAL_KEYMAP_SETTER.minibuffer_input_focus_changed(self, False)
        return QLineEdit.event(self, evt)

    def set_completer_model(self, completer_model):
        self._proxy_model.setSourceModel(completer_model)

    def completer_model(self):
        return self._proxy_model.sourceModel()

    def set_match(self, type):
        self._match = type
        if self._popup.isVisible():
            self._show_completions(self.text())

    def _on_row_changed(self, current, old):
        if self._autocomplete:
            self.complete(hide_popup=False)

    def _show_completions(self, txt, force=False):
        force = force or self._complete_empty
        if self._match is not None:
            if self._match == self.SimpleMatch:
                pattern = "^" + QRegularExpression.escape(txt)
            elif self._match == self.FuzzyMatch:
                pattern = ".*".join(QRegularExpression.escape(t)
                                    for t in txt.split())
            self._proxy_model.setFilterRegularExpression(QRegularExpression(
                pattern,
                QRegularExpression.PatternOption.CaseInsensitiveOption
            ))
        else:
            self._proxy_model.setFilterRegularExpression(None)

        if self._proxy_model.rowCount() == 0:
            self._popup.hide()
        elif not txt and not force:
            self._popup.hide()
        else:
            self._popup.popup()

    def show_completions(self, filter_text=None):
        self._show_completions(
            filter_text if filter_text is not None else self.text(), True)

    def _on_completion_activated(self, index, hide_popup=True):
        if hide_popup:
            self._popup.hide()
        model = index.model()
        if index.column() != 0:
            index = model.index(index.row(), 0)

        self.setText(model.data(index))
        self.completion_activated.emit(model.mapToSource(index))

    def popup(self):
        return self._popup

    def complete(self, hide_popup=True):
        if not self._popup.isVisible():
            return

        index = self._popup.selectionModel().currentIndex()
        if index.isValid():
            self._on_completion_activated(index, hide_popup=hide_popup)
        elif self._autocomplete_single and self._proxy_model.rowCount() == 1:
            self._on_completion_activated(self._proxy_model.index(0, 0),
                                          hide_popup=hide_popup)

    def select_next_completion(self, forward=True, steps=1):
        model = self._proxy_model
        entries = model.rowCount()
        if entries == 0:
            return

        selection = self._popup.selectionModel().currentIndex()
        if not selection.isValid():
            row = 0 if forward else (entries - 1)
        else:
            row = selection.row()
            if forward:
                row = row + steps
                if row >= entries:
                    row = 0
            else:
                row = row - steps
                if row < 0:
                    row = (entries - 1)

        self._popup.selectRow(row)

    def select_first_completion(self):
        self._popup.selectRow(0)

    def select_last_completion(self):
        entries = self._proxy_model.rowCount()
        self._popup.selectRow(entries - 1)

    def select_next_page_completion(self, forward=True):
        self.select_next_completion(forward=forward,
                                    steps=self._popup._max_visible_items - 1)

    def mark(self):
        return self._mark

    def set_mark(self, value=None):
        if value is None:
            value = not self._mark
        self._mark = value
        return self._mark

    def reinit(self):
        self.setText("")
        self.setEchoMode(self.EchoMode.Normal)
        self.setValidator(None)
        self._right_italic_text = ""

    def set_right_italic_text(self, text):
        self._right_italic_text = text
        self.update()

    def paintEvent(self, event):
        QLineEdit.paintEvent(self, event)
        if not self._right_italic_text:
            return
        painter = QPainter(self)
        font = painter.font()
        font.setItalic(True)
        painter.setFont(font)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawText(self.rect().adjusted(0, 0, -10, 0),
                         Qt.AlignmentFlag.AlignRight,
                         self._right_italic_text)

    @pyqtProperty("QColor")
    def background_color(self):
        return self.palette().color(self.backgroundRole())

    @background_color.setter
    def background_color(self, color):
        palette = self.palette()
        palette.setColor(self.backgroundRole(), color)
        self.setPalette(palette)


def _update_minibuffer_height(var):
    for window in windows():
        window.minibuffer().set_height(var.value)


MINIBUFFER_HEIGHT = variables.define_variable(
    "minibuffer-height",
    "The height in pixel of the minibuffer.",
    25,
    type=variables.Int(min=1),
    callbacks=(_update_minibuffer_height,)
)


class Minibuffer(QWidget):
    def __init__(self, window):
        QWidget.__init__(self, window)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.label = QLabel(self)
        self.rlabel = QLabel(self)
        self.__default_label_policy = self.label.sizePolicy()
        # when input line edit is hidden, this size policy allow to not resize
        # the parent widget if the text in the label is too long.
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Fixed)
        layout.addWidget(self.label)

        self._input = MinibufferInput(self, window)
        layout.addWidget(self._input)

        self.rlabel.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.rlabel)

        self.set_height(MINIBUFFER_HEIGHT.value)

        self._input.installEventFilter(self)
        self._input.hide()
        self._prompt = None

    def set_height(self, height):
        self.label.setMinimumHeight(height)

    def eventFilter(self, obj, event):
        if obj == self._input:
            if event.type() == QEvent.Type.Hide:
                self.label.setSizePolicy(QSizePolicy.Policy.Ignored,
                                         QSizePolicy.Policy.Fixed)
            elif event.type() == QEvent.Type.Show:
                self.label.setSizePolicy(self.__default_label_policy)
                obj.setMaximumHeight(self.label.height())
        return False

    def show_info(self, text):
        if self._input.isHidden():
            self.label.setText(text)

    def input(self):
        return self._input

    def prompt(self):
        return self._prompt

    def do_prompt(self, prompt, **kwargs):
        self.close_prompt()
        self._prompt = prompt
        if prompt:
            prompt.closed.connect(self._prompt_closed)
            prompt.closed.connect(prompt.deleteLater)
            return prompt.exec(self, **kwargs)

    def close_prompt(self):
        if self._prompt:
            self._prompt.close()
            self._prompt = None

    def _prompt_closed(self):
        self._prompt = None
