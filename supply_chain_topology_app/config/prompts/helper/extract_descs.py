"""Extract verbatim Field(description=...) strings via AST (no imports, no side effects)."""
import ast
import sys


def literal_of(node):
    """Best-effort constant folding for str concatenation / implicit joins."""
    try:
        return ast.literal_eval(node)
    except Exception:
        pass
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = literal_of(node.left)
        right = literal_of(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    return None


def find_desc(call):
    """Return description= kwarg from a Field(...) call, plus other notable kwargs."""
    desc = None
    extras = {}
    for kw in call.keywords:
        if kw.arg == "description":
            desc = literal_of(kw.value)
        elif kw.arg in ("min_length", "default", "default_factory"):
            v = literal_of(kw.value)
            extras[kw.arg] = v if v is not None else ast.unparse(kw.value)
    return desc, extras


def walk_classes(path, label):
    src = open(path).read()
    tree = ast.parse(src)
    print(f"\n{'='*70}\n{label}  ({path})\n{'='*70}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = [ast.unparse(b) for b in node.bases]
        if not any("BaseModel" in b for b in bases):
            continue
        print(f"\n### {node.name}")
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign) or stmt.value is None:
                continue
            fname = ast.unparse(stmt.target)
            ftype = ast.unparse(stmt.annotation)
            call = stmt.value
            # Handle Annotated[list[X], Field(...)] style
            if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "Field":
                desc, extras = find_desc(call)
            else:
                desc, extras = None, {}
                for sub in ast.walk(stmt):
                    if isinstance(sub, ast.Call) and getattr(sub.func, "id", "") == "Field":
                        desc, extras = find_desc(sub)
                        break
            print(f"- {fname} : {ftype}")
            if extras:
                print(f"    [{extras}]")
            print(f"    DESC: {desc!r}")


walk_classes(sys.argv[1], "core/schemas.py — DOMAIN SCHEMAS")
walk_classes(sys.argv[2], "topologies/planner_executor.py — MasterOutput")
