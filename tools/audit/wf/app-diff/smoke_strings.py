import ast, re, sys
src = open(r"D:/AutoDeck/tools/ui_smoke.py", encoding="utf-8").read()
ast.parse(src)  # syntax check, read-only
print("ui_smoke.py parses OK")
appjs = open(r"D:/AutoDeck/app/autodeck_app/static/app.js", encoding="utf-8").read()
html = open(r"D:/AutoDeck/app/autodeck_app/static/index.html", encoding="utf-8").read()
hay = (appjs + html).replace("\u2014", "-").replace("\u2019", "'")
tree = ast.parse(src)
seen = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        s = node.value.strip()
        if len(s.split()) < 3 or len(s) > 90: continue
        if s in seen: continue
        seen.add(s)
        norm = s.replace("\u2014", "-").replace("\u2019", "'")
        # only strings that look like page wording checks: used inside a Compare 'in' or a check()
        if norm in hay: continue
        print(f"{node.lineno}: {s!r}")
