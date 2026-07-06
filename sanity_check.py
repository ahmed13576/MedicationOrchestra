import ast, os

backend_dir = 'backend'

# 1. Check main.py for missing imports (false-positive aware)
print("=== main.py: Functions called vs available ===")
with open(os.path.join(backend_dir, 'main.py'), 'r', encoding='utf-8') as f:
    source = f.read()
tree = ast.parse(source)

# All names defined locally (classes, functions, variables at module level)
locally_defined = set()
for node in ast.iter_child_nodes(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        locally_defined.add(node.name)
    elif isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name):
                locally_defined.add(t.id)

# All imported names
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        for alias in node.names:
            imported.add(alias.asname or alias.name.split('.')[0])
    elif isinstance(node, ast.ImportFrom):
        for alias in node.names:
            imported.add(alias.asname or alias.name)

available = imported | locally_defined

# All bare function calls (not method calls)
used_funcs = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            used_funcs.add(node.func.id)

import builtins as _builtins
builtin_names = set(dir(_builtins))
missing = used_funcs - available - builtin_names

if missing:
    print("  MISSING (true gaps):", sorted(missing))
else:
    print("  All clear - no missing symbols.")

print("  Locally defined:", sorted(locally_defined))

# 2. Check all service files parse without SyntaxError
print()
print("=== Service files: Syntax check ===")
services_dir = os.path.join(backend_dir, 'services')
for fname in sorted(os.listdir(services_dir)):
    if fname.endswith('.py'):
        fpath = os.path.join(services_dir, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                ast.parse(f.read())
            print("  OK  " + fname)
        except SyntaxError as e:
            print("  ERR " + fname + ": " + str(e))

# 3. Check requirements.txt has key packages
print()
print("=== requirements.txt: Key packages ===")
with open(os.path.join(backend_dir, 'requirements.txt'), 'r') as f:
    reqs = f.read().lower()
for pkg in ['fastapi', 'uvicorn', 'firebase-admin', 'google-cloud-firestore',
            'google-cloud-aiplatform', 'google-genai', 'pillow', 'python-multipart']:
    status = 'OK' if pkg in reqs else 'MISSING'
    print("  " + status + "  " + pkg)
