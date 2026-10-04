"""Static checks: YAML in the semantic view & agent spec parses; every procedure handler exists."""
import importlib
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

sv = re.search(r"\$\$(.*?)\$\$", open(os.path.join(ROOT, "sql", "07_semantic_view.sql")).read(), re.S).group(1)
m = yaml.safe_load(sv)
tables = {t["name"]: t for t in m["tables"]}
cols = {t: {c["name"] for k in ("dimensions", "time_dimensions", "facts") for c in tables[t].get(k, [])} for t in tables}
for r in m["relationships"]:
    for rc in r["relationship_columns"]:
        assert rc["left_column"] in cols[r["left_table"]], rc
        assert rc["right_column"] in cols[r["right_table"]], rc
for vq in m["verified_queries"]:
    for t in re.findall(r"__(\w+)", vq["sql"]):
        assert t in tables, (vq["name"], t)
print(f"semantic view OK: {len(tables)} tables, {len(m['relationships'])} relationships, "
      f"{len(m['verified_queries'])} verified queries")

ag = re.search(r"FROM SPECIFICATION\s*\$\$(.*?)\$\$", open(os.path.join(ROOT, "sql", "08_cortex_agent.sql")).read(), re.S).group(1)
a = yaml.safe_load(ag)
names = {t["tool_spec"]["name"] for t in a["tools"]}
assert names == set(a["tool_resources"]), (names, set(a["tool_resources"]))
print(f"agent spec OK: tools {sorted(names)}")

procs = re.findall(r"HANDLER = '(\w+)\.(\w+)'", open(os.path.join(ROOT, "sql", "02_procedures.sql")).read())
for mod, fn in procs:
    assert hasattr(importlib.import_module(mod), fn), (mod, fn)
print(f"procedures OK: {len(procs)} handlers resolve")
