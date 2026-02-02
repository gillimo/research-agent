# Verification Checklist

Use these steps on a clean machine to confirm Martin is ready to run.

Status
- Pending clean-machine run (requires separate host).
- Log template: `docs/clean_machine_run_log.md`
- After the run, update G4V/G5V in `docs/tickets.md` with the results.

1) Install + launch
   - Run `scripts\\install_martin.ps1`
   - Open a new shell and run `martin`
   - If installs are slow, rerun with `-SkipDeps` and install manually.

2) Service check
   - `scripts\\martin_service.ps1 start`
   - `scripts\\martin_service.ps1 status`
   - `scripts\\martin_service.ps1 stop`

3) Test run
   - `scripts\\run_tests.ps1`
   - Use `scripts\\run_tests.ps1 -SkipInstall` to avoid reinstalling deps.

4) SocketBridge IPC smoke (optional)
   - Start `martin` (or run the CLI that starts the socket server)
   - `python scripts\\socketbridge_smoke.py`

5) Remote transport check (optional)
   - Set `remote_transport.ssh_host` in `config\\local.yaml`
   - Run `/remote status` and confirm validation passes

6) Trust policy key (optional)
   - `/trust keygen` and set `MARTIN_ENCRYPTION_KEY`
   - `/export session logs\\session_export.json` (check `.enc` when enabled)
