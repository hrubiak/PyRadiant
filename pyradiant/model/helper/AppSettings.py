from PyQt6.QtCore import QSettings


class AppSettings:
    def __init__(self, org="PyRadiant", app="PyRadiant", group="PyRadiantSettings"):
        self._settings = QSettings(org, app)
        self._group = group

    def set(self, key, value):
        self._settings.beginGroup(self._group)
        self._settings.setValue(key, value)
        self._settings.endGroup()

    def get(self, key, default=None):
        self._settings.beginGroup(self._group)
        value = self._settings.value(key, default)
        self._settings.endGroup()
        return value

    def keys(self):
        self._settings.beginGroup(self._group)
        keys = self._settings.allKeys()
        self._settings.endGroup()
        return keys

    def dump(self):
        print(f"Dumping settings under [{self._group}]:")
        for key in self.keys():
            print(f"  {key} = {self.get(key)}")

    def clear(self):
        self._settings.beginGroup(self._group)
        self._settings.remove("")
        self._settings.endGroup()
