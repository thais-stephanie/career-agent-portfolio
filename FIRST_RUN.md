# First run
Download the Windows ZIP linked in README, extract it to a writable folder and double-click Start-Career-Agent.cmd. Keep the console open. Python is installed by uv; Node is not needed. First setup downloads tools and dependencies over HTTPS.

Home starts with an empty personal database. Open settings to describe your search, then import a posting or choose supported sources. Review imported career details before confirming them. Open Resume Tailor Beta to create a candidate and review its own sources separately.

## Returning and stopping
Use the same folder and launcher. Ctrl+C stops both servers. If a port is occupied, the launcher refuses to open a browser onto that unknown service. Close your previous launcher, or open PowerShell in this folder and run `./Start-Career-Agent.ps1 -Port 8875`.

## Demo
Start-Demo.cmd uses data/demo.db, data/demo-config and data/tailor-demo. Personal mode uses data/personal.db, config and data/tailor-personal. They do not copy each other's evidence. Stop one mode before launching the other on the same ports.

## Recovery and backups
If setup fails, check internet access and rerun the launcher. Do not delete data to repair dependencies. Before upgrading, stop both apps and make a private copy of data/ and any config/*.local.yaml files. Tailor also offers candidate Export backup / Import backup. Backups contain personal information and are not encrypted by the app.

PDF requires Word or LibreOffice. Without either, use Word or Markdown export. If clipboard access is unavailable, select the job description and copy it manually. The browser and clipboard belong to your operating system; check any clipboard sync settings.

The first resume can take longer while a local office application starts to
verify its page count. Keep the launcher open until generation finishes.
If you stop it during generation, restart and generate that resume again;
an interrupted run is not a completed export.
