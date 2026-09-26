# First run

The complete guide, for every platform, is [docs/INSTALL.md](docs/INSTALL.md).
This page is the short version for the Windows ZIP.

Extract the ZIP into a folder you can write to (Downloads or Documents, not
Program Files) and double-click **Start-Demo.cmd** to try the demo, or
**Start-Career-Agent.cmd** for your own workspace. Keep the window open. The
first setup downloads uv, Python 3.12 and the locked libraries over HTTPS;
Node is not needed.

Career Agent opens at http://127.0.0.1:8765/ and Resume Tailor Beta at
http://127.0.0.1:8766/. 127.0.0.1 means this computer.

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
`data\profiles.json`, the first profile's database `data\personal.db` and
Resume Tailor workspace `data\tailor-personal`, every later profile under
`data\profiles\`, and the shared job catalogue `data\shared\catalogue.db`. The
first profile's settings are `config\*.local.yaml`. The demo uses
`data\demo.db`, `data\demo-config` and `data\tailor-demo`, and never reads
personal data. Demo and personal mode cannot run at the same time on the same
ports.

## Recovery and backups

If setup fails, check the internet connection and run the launcher again. Do
not delete `data` to repair a failed setup.

Before an update, stop both apps and make a backup from PowerShell in this
folder:

```powershell
.\.venv\Scripts\career-agent.exe backup --profile "My profile"
.\.venv\Scripts\career-agent.exe backup --catalogue --profile "My profile"
```

Resume Tailor has **Export backup** in its candidate menu. Backups contain
personal information, never API keys, and are not encrypted. See
[updating](docs/INSTALL.md#updating-to-a-new-version).

PDF requires Word or LibreOffice. Without either, use Word or Markdown export.
The first resume can take longer while a local office application starts to
count its pages. If you stop the launcher during generation, start it again and
generate that resume again; an interrupted run is not a completed export.
