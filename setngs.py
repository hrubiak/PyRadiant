from PyQt6.QtCore import QSettings

settings = QSettings("PyRadiant", "PyRadiant")

print("Settings file location:", settings.fileName())
print("All keys:")
for key in settings.allKeys():
    print(f"  {key} = {settings.value(key)}")

