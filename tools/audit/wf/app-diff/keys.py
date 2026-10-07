from autodeck2 import sheets
from autodeck_app import server
d = set(sheets.DEFAULTS)
srv = set(server._SHEET_NUMBERS) | set(server._SHEET_FLAGS)
print("server keys not in sheets.DEFAULTS:", sorted(srv - d))
print("DEFAULTS keys the server never validates:", sorted(d - srv))
print("DEFAULTS:", {k: sheets.DEFAULTS[k] for k in sorted(d)})
