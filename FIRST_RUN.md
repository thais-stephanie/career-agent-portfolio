# First run

The complete guide, for every platform, is [docs/INSTALL.md](docs/INSTALL.md).
This page is the short version for the Windows ZIP.

Extract the ZIP into a folder you can write to (Downloads or Documents, not
Program Files) and double-click **Start-Demo.cmd** to try the demo, or
**Start-Career-Agent.cmd** for your own workspace. Keep the window open. The
first setup downloads uv, Python 3.12 and the locked libraries over HTTPS;
Node is not needed.

Career Agent opens at http://127.0.0.1:8765/; Resumes is a page of it.
127.0.0.1 means this computer.

## The Career Agent shortcut

After the first setup succeeds, a **Career Agent** shortcut appears on the
Desktop and in the Start menu. It starts Career Agent with no console window
and opens it in its own window (Microsoft Edge in app mode, or your usual
browser without Edge). Closing the Career Agent windows, or **Quit Career
Agent** in the side menu, stops it.

If the shortcut shows a message:

- **The address is in use**: another program, or the demo, is using port 8765.
  Close it and try again.
- **Setup is not finished**: double-click **Start-Career-Agent.cmd** once.
- **Could not start** or **took too long**: double-click
  **Start-Career-Agent.cmd** to see what happens. The shortcut's own record of
  its last start is `data\logs\desktop.log`.

If the shortcut is missing, or the folder was moved, double-click
**Create-Career-Agent-Shortcuts.cmd** to make it again for this folder.

## Returning and stopping

Double-click the same launcher in the same folder. **Ctrl+C** in its window
stops both apps; if Windows then asks `Terminate batch job (Y/N)?`, type `Y`.

If the ports are in use, the launcher refuses to open a browser onto an
unknown service. Close the other launcher window, or open PowerShell in this
folder (type `powershell` in File Explorer's address bar) and run
`.\Start-Career-Agent.cmd -Port 8875`. Windows blocks the `.ps1` file from a
downloaded ZIP, so use the `.cmd` file.

## Where your data is

Personal mode keeps everything under `data\`: the profile registry
`data\profiles.json`, the first profile's database `data\personal.db`
(and `data\tailor-personal`, files of the retired Resume Tailor, if it was
used), every later profile under
`data\profiles\`, and the shared job catalogue `data\shared\catalogue.db`. The
first profile's settings are `config\*.local.yaml`. The demo uses
`data\demo.db` and `data\demo-config`, and never reads personal data. Demo
and personal mode cannot run at the same time on the same port.

## Recovery and backups

If setup fails, check the internet connection and run the launcher again. Do
not delete `data` to repair a failed setup.

Before an update, stop Career Agent and make a backup from PowerShell in this
folder:

```powershell
.\.venv\Scripts\career-agent.exe backup --profile "My profile"
.\.venv\Scripts\career-agent.exe backup --catalogue --profile "My profile"
```

Backups contain personal information, never API keys, and are not
encrypted. See
[updating](docs/INSTALL.md#updating-to-a-new-version).

PDF export uses Microsoft Edge (or Chrome) on this computer. Without either,
use Word or JSON export. If you stop the launcher during an export, start it
again and export again; an interrupted export is not a completed one.
