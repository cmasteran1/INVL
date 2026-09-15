# dmgbuild settings for the INVL Hub macOS disk image.
#
# Used by release.sh:
#   dmgbuild -s packaging/dmg_settings.py -D app=<path to .app> \
#            -D readme=<path to READ ME FIRST.md> "INVL Hub" <output.dmg>
#
# The license block embeds EULA.txt as a click-through agreement: macOS shows
# it when the image is opened, and nothing mounts until the customer clicks
# Agree. That is the drag-install equivalent of an installer's license page.

app = defines.get("app", "dist/INVL Hub.app")  # noqa: F821 - injected by dmgbuild
readme = defines.get("readme")  # noqa: F821

files = [app] + ([readme] if readme else [])
symlinks = {"Applications": "/Applications"}

format = "UDZO"

license = {
    "default-language": "en_US",
    "licenses": {"en_US": "EULA.txt"},
}
