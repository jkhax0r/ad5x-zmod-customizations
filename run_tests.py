"""Run offline suites separately; never connects to a printer."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
failed = []
for suite in ['test_probe.py', 'test_stock_mesh_average.py', 'test_soak.py', 'test_purge.py', 'test_edge_paths.py']:
    print(suite, flush=True)
    result = subprocess.run([sys.executable, str(root/'tests'/suite), '-q'], cwd=root)
    if result.returncode:
        failed.append(suite)
if failed:
    raise SystemExit('Failed: ' + ', '.join(failed))
print('All offline suites passed. No printer connection or physical test.')
