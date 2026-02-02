# Clean Machine Run Log

Use this template to record G4V/G5V verification on a fresh host.

Run metadata
- Date:
- Machine/OS:
- Shell:
- Python version:
- Network status:

G4V: Install + launch
- Command: `scripts\install_martin.ps1`
- Result:
- Notes (pip/venv/shim):
- Command: `martin`
- Result:
- Notes:

G5V: Service checks
- Command: `scripts\martin_service.ps1 start`
- Result:
- PID:
- Command: `scripts\martin_service.ps1 status`
- Result:
- Command: `scripts\martin_service.ps1 stop`
- Result:
- Notes:

Tests (optional)
- Command: `scripts\run_tests.ps1`
- Result:
- Notes:

Follow-ups
- Bugs logged:
- Tickets updated:
