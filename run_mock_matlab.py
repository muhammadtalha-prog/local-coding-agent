import sys
import os
from unittest.mock import patch
from external_software import ExternalSoftwareAgent

if len(sys.argv) < 2:
    print("Usage: python run_mock_matlab.py <script_name_without_extension>")
    sys.exit(1)

script_name = sys.argv[1]
agent = ExternalSoftwareAgent()

print(f"Running '{script_name}' via Mock MATLAB Executor...")
with patch("shutil.which", return_value=None):
    res = agent.execute_command(f"matlab -batch {script_name}", ".")

print(f"\nExit code: {res['exit_code']}")
if res['stdout']:
    print(f"Stdout:\n{res['stdout']}")
if res['stderr']:
    print(f"Stderr:\n{res['stderr']}")
