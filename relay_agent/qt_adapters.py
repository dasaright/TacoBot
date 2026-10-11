"""Small control adapters for reusing tested relay actions with the Qt desktop."""
import uuid
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtWidgets import QTreeWidgetItem


class Value:
    def __init__(self, value=None, getter=None, setter=None):
        self.value, self.getter, self.setter = value, getter, setter

    def get(self):
        return self.getter() if self.getter else self.value

    def set(self, value):
        self.value = value
        if self.setter:
            self.setter(value)


class Control:
    def __init__(self, widget):
        self.widget = widget

    def configure(self, **options):
        if 'state' in options:
            self.widget.setEnabled(options['state'] != 'disabled')
        if 'text' in options:
            self.widget.setText(options['text'])
        if 'values' in options:
            old = self.widget.currentText()
            self.widget.blockSignals(True)
            self.widget.clear(); self.widget.addItems(options['values'])
            if old in options['values']:
                self.widget.setCurrentText(old)
            self.widget.blockSignals(False)

    def instate(self, states):
        return ('disabled' in states) == (not self.widget.isEnabled())


class Text:
    def __init__(self, widget):
        self.widget = widget

    def get(self, *args):
        return self.widget.toPlainText()

    def configure(self, **options):
        if 'state' in options:
            self.widget.setReadOnly(options['state'] == 'disabled')

    def delete(self, *args):
        self.widget.clear()

    def insert(self, index, text):
        self.widget.setPlainText(text)


class Root:
    def __init__(self, view):
        self.view, self.timers, self.counter = view, {}, 0

    def after(self, milliseconds, callback):
        self.counter += 1; key = self.counter
        timer = QTimer(self.view); timer.setSingleShot(True)
        self.timers[key] = timer
        def run():
            self.timers.pop(key, None); timer.deleteLater(); callback()
        timer.timeout.connect(run); timer.start(milliseconds)
        return key

    def after_cancel(self, key):
        timer = self.timers.pop(key, None)
        if timer:
            timer.stop(); timer.deleteLater()

    def after_idle(self, callback):
        return self.after(0, callback)

    def title(self, text):
        self.view.setWindowTitle(text)

    def destroy(self):
        self.view.close()


class Tree:
    def __init__(self, widget):
        self.widget, self.items = widget, {}

    def insert(self, parent, position, iid=None, values=(), open=False, tags=()):
        key = iid or uuid.uuid4().hex
        item = QTreeWidgetItem([str(value) for value in values])
        item.setData(0, Qt.ItemDataRole.UserRole, key)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
        self.items[key] = item
        if parent:
            self.items[parent].addChild(item)
        else:
            self.widget.addTopLevelItem(item)
        item.setExpanded(open)
        if 'current' in tags:
            from PySide6.QtGui import QColor, QBrush
            item.setForeground(0, QBrush(QColor('#52d4ba')))
        return key

    def get_children(self, parent=''):
        if parent:
            item = self.items[parent]; items = [item.child(i) for i in range(item.childCount())]
        else:
            items = [self.widget.topLevelItem(i) for i in range(self.widget.topLevelItemCount())]
        return tuple(item.data(0, Qt.ItemDataRole.UserRole) for item in items)

    def delete(self, *keys):
        for key in keys:
            item = self.items.get(key)
            if not item:
                continue
            for child in list(self.get_children(key)):
                self.delete(child)
            parent = item.parent()
            if parent:
                parent.takeChild(parent.indexOfChild(item))
            else:
                self.widget.takeTopLevelItem(self.widget.indexOfTopLevelItem(item))
            self.items.pop(key, None)

    def item(self, key, option):
        return self.items[key].isExpanded() if option == 'open' else None

    def parent(self, key):
        item = self.items.get(key)
        return item.parent().data(0, Qt.ItemDataRole.UserRole) if item and item.parent() else ''

    def selection(self):
        return tuple(item.data(0, Qt.ItemDataRole.UserRole) for item in self.widget.selectedItems())

    def selection_set(self, keys):
        keys = [keys] if isinstance(keys, str) else keys
        self.widget.clearSelection()
        for key in keys:
            if key in self.items:
                self.items[key].setSelected(True)

    def focus(self, key=None):
        if key is not None:
            self.widget.setCurrentItem(self.items[key]); return
        item = self.widget.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ''

    def exists(self, key):
        return key in self.items

    def identify_region(self, x, y):
        return 'tree' if self.widget.itemAt(QPoint(x, y)) else ''

    def identify_row(self, y):
        item = self.widget.itemAt(QPoint(25, y))
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ''
