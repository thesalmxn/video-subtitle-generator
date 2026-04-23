"""Run this ONCE to download the Greek→English language pack for offline translation."""

import argostranslate.package
import argostranslate.translate

print("Downloading language pack index...")
argostranslate.package.update_package_index()

print("Finding Greek → English package...")
available = argostranslate.package.get_available_packages()
pkg = next(p for p in available if p.from_code == "el" and p.to_code == "en")

print("Downloading and installing...")
argostranslate.package.install_from_path(pkg.download())

print("Done! Greek → English language pack installed successfully!")
print("You can now run the subtitle script offline.")