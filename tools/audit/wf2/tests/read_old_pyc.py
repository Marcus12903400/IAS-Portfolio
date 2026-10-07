import marshal, sys, dis, types
path = sys.argv[1]
want = sys.argv[2] if len(sys.argv) > 2 else None
with open(path, 'rb') as f:
    f.read(16)
    code = marshal.load(f)
print("module consts/names present:", [c.co_name for c in code.co_consts if isinstance(c, types.CodeType)])
def walk(c, depth=0):
    for k in c.co_consts:
        if isinstance(k, types.CodeType):
            yield k
            yield from walk(k, depth+1)
for fn in walk(code):
    if want and fn.co_name != want:
        continue
    if want:
        print("=== FOUND", fn.co_name, "line", fn.co_firstlineno)
        print("consts:", [k for k in fn.co_consts if not isinstance(k, types.CodeType)])
        print("names:", fn.co_names)
        dis.dis(fn)
