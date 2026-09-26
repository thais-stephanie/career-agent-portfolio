# Install Career Agent

This guide takes you from nothing installed to a running Career Agent, with no
programming knowledge needed. It covers version **v0.2.0-beta.1**.

Career Agent runs on your own computer. It is a program that opens in your web
browser, but the pages come from your computer, not from a website.

**Contents**

- [Which computers it runs on](#which-computers-it-runs-on)
- [Choose how to install](#choose-how-to-install)
- [Path A: install with Claude Code or Codex](#path-a-install-with-claude-code-or-codex)
- [Path B on Windows: download and double-click](#path-b-on-windows-download-and-double-click)
- [Path B on macOS or Linux: use the Terminal](#path-b-on-macos-or-linux-use-the-terminal)
- [What you see the first time](#what-you-see-the-first-time)
- [Try the demo first](#try-the-demo-first)
- [Set up your own search](#set-up-your-own-search)
- [How to open Career Agent next time](#how-to-open-career-agent-next-time)
- [How to stop Career Agent](#how-to-stop-career-agent)
- [Open a terminal in the Career Agent folder (Windows)](#open-a-terminal-in-the-career-agent-folder-windows)
- [Backups](#backups)
- [Updating to a new version](#updating-to-a-new-version)
- [Optional features](#optional-features)
- [Troubleshooting](#troubleshooting)
- [Asking for help](#asking-for-help)
- [Uninstalling](#uninstalling)

## Which computers it runs on

| Computer | Status for this release |
|---|---|
| Windows 11, 64-bit (x64) | **Tested.** The install, the demo, personal mode, restart and shutdown were run end to end for this release. |
| Windows 10, 64-bit | Not tested for this release. It uses the same launcher as Windows 11. |
| Windows on ARM | Not tested. The launcher downloads the ARM version of uv, but nothing else was checked. |
| macOS | **Not tested.** There is no double-click launcher. The terminal steps below are the same commands that were tested on Windows. |
| Linux | **Not tested.** Same as macOS. |

You need about 700 MB of free disk space (measured on the test computer:
360 MB in the Career Agent folder, plus 300 MB of Python and downloads that uv
keeps elsewhere) and an internet connection the first time. After that, Career Agent
only uses the internet when you ask it to (see [Privacy](PRIVACY.md)).

## Choose how to install

Pick one path and ignore the other.

- **Path A** is for people who already use **Claude Code** or **Codex**, the
  coding assistants that run in a terminal. You paste one message and the
  assistant does the installation.
- **Path B** is for everyone else. On Windows you download a ZIP file and
  double-click. On macOS and Linux you type a few commands into the Terminal.

## Path A: install with Claude Code or Codex

Claude Code and Codex are programs you talk to in a terminal window. They can
read files, run commands and explain what they did. If you do not have either
one, use [Path B](#path-b-on-windows-download-and-double-click); installing a
coding assistant only for this is not necessary.

<details>
<summary>How do I open Claude Code or Codex?</summary>

Open a terminal. On Windows, press the **Windows key**, type `PowerShell`
and press **Enter**. On macOS, see [open Terminal](#macos-open-terminal); on
Linux, [open a terminal](#linux-open-a-terminal). Then type `claude` for Claude
Code, or `codex` for Codex, and press **Enter**.

If the terminal says the command is not recognised, the assistant is not
installed. Follow the official instructions:
[Claude Code](https://docs.claude.com/en/docs/claude-code/overview) or
[Codex CLI](https://developers.openai.com/codex/cli).

</details>

Copy the whole message below, paste it into Claude Code or Codex, and press
**Enter**. The same message works in both.

```text
Please install Career Agent on this computer for me. I am not a programmer, so
explain each step in plain words before you do it, and ask me before anything
that needs administrator rights.

Project: https://github.com/thais-stephanie/career-agent-portfolio
Release: v0.2.0-beta.1
Install guide: docs/INSTALL.md in that repository.

Follow these rules:
1. First detect my operating system. Read docs/INSTALL.md and follow the path
   for my system. Windows uses the release ZIP and Start-Career-Agent.cmd.
   macOS and Linux use uv and the terminal commands in that guide.
2. Check whether Career Agent is already on this computer (look for folders
   that contain Start-Career-Agent.cmd and a data folder). If one exists, stop
   and ask me. Never overwrite, move or delete an existing installation or
   anything in its data, config, backups or .env files.
3. Only install what the guide says is required. Do not install Node, Ollama,
   Microsoft Word, LibreOffice or any other optional tool unless I ask.
4. Put the new installation in a folder under my user folder, not in
   Program Files, and tell me the full path.
5. Never create, guess or ask me to paste an API key. If an optional feature
   needs a key, tell me which one and where it would go, and stop there.
6. Start the demo first (Start-Demo.cmd on Windows, or the --demo command on
   macOS/Linux) and check that both addresses answer:
   http://127.0.0.1:8765/ (Career Agent) and http://127.0.0.1:8766/
   (Resume Tailor). Then stop the demo by ending the process you started,
   and check that ports 8765 and 8766 are free again.
7. Only after the demo works, run the normal launcher once with its check
   option (Windows: Start-Career-Agent.cmd -Check -NoOpen; macOS/Linux:
   uv run --no-sync python scripts/launch.py --check) and show me that it
   printed "Setup complete". Then tell me to start Career Agent myself by
   double-clicking Start-Career-Agent.cmd (Windows) or with the command in the
   guide (macOS/Linux).
8. Do not turn on LinkedIn collection, semantic matching or any AI provider.
   Do not collect jobs. I will do that myself later.
9. At the end, tell me: the folder you installed into, everything you
   installed or changed, and the exact steps to open Career Agent again
   tomorrow.
```

When the assistant finishes, continue at
[What you see the first time](#what-you-see-the-first-time).

## Path B on Windows: download and double-click

You do not need to install Python, Git or anything else first. The launcher
downloads what it needs.

### 1. Download the ZIP file

1. Open this page in your browser:
   <https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.1>
2. Scroll down to **Assets**.
3. Click **Career-Agent-v0.2.0-beta.1-Windows.zip**. Your browser saves it in
   your **Downloads** folder.

### 2. Extract it

A ZIP file is a compressed package. Windows has to extract it before the
program inside can run.

1. Open **File Explorer** (the yellow folder icon on the taskbar) and go to
   **Downloads**.
2. Right-click **Career-Agent-v0.2.0-beta.1-Windows.zip** and choose
   **Properties**. If the bottom of the **General** tab shows **Unblock**,
   tick it and click **OK**. This tells Windows you trust the file you
   downloaded, so it does not block the launcher later.
3. Right-click the ZIP again and choose **Extract All...**.
4. Windows suggests a folder with the same name inside Downloads. Click
   **Extract**. A new window opens showing the extracted files.

Decide where the folder lives before you start it for the first time; the
Downloads folder is fine. Do not put it in `C:\Program Files` (read-only for
normal programs), and do not put it in a folder that OneDrive, Dropbox or
another service syncs: your private `data` folder would be uploaded, and
syncing can damage a database that is in use. On many Windows 11 computers
**Documents** and **Desktop** are synced to OneDrive.

### 3. Start the demo

In the extracted folder, double-click **Start-Demo.cmd**.

If a blue **Windows protected your PC** window appears, the Unblock step was
skipped. Click **More info**, then **Run anyway**; or close it, unblock the ZIP
as in step 2 and extract it again.

A black or blue window opens. This is the launcher. The first time, it shows
three steps:

```text
[1/3] First setup: downloading uv, the small tool that installs Python for you.
[2/3] Checking Career Agent and Resume Tailor Beta. Python 3.12 is managed automatically.
[3/3] Starting. Your browser will open by itself.
```

uv is a small program that installs Python and the Python libraries Career
Agent uses. If uv is not already on the computer, the launcher downloads it
into a `.tools` folder inside the Career Agent folder and checks its checksum.
The libraries go into a `.venv` folder next to it. Administrator rights are not
needed.

On the release test computer this first setup took 40 seconds. A slower
internet connection takes longer. Later starts take a few seconds.

When it is ready, the window shows:

```text
Career Agent is running: http://127.0.0.1:8765/
Resume Tailor Beta: http://127.0.0.1:8766/
If your browser did not open, copy the first address into it.
Keep this window open while you use the apps. Press Ctrl+C here to stop both.
```

and your browser opens Career Agent. Continue at
[What you see the first time](#what-you-see-the-first-time).

## Path B on macOS or Linux: use the Terminal

macOS and Linux were not tested for this release. These steps use the same
commands that were tested on Windows. If one of them fails, please
[report it](#asking-for-help).

#### macOS: open Terminal

1. Press **Command + Space**. A search box appears.
2. Type `Terminal`.
3. Press **Enter**. A window with a text prompt opens. You type commands
   there and press **Enter** to run each one.

#### Linux: open a terminal

1. Open your applications menu.
2. Search for `Terminal`.
3. Open it. A window with a text prompt opens.

### 1. Install uv

uv is the small tool that installs Python and the libraries Career Agent uses.

Copy this line into the Terminal and press **Enter**:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Linux, if the terminal says `curl: command not found`, install curl with
your system's software installer first. This is the official installer from
the uv project
([documentation](https://docs.astral.sh/uv/getting-started/installation/)).
When it finishes, **close the Terminal window and open a new one**, so the
new `uv` command is found.

Check that it worked:

```sh
uv --version
```

If you see something like `uv 0.11.7`, continue. If you see
`command not found`, see [uv is not recognised](#uv-is-not-recognised).

### 2. Download and extract Career Agent

1. Open <https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.1>.
2. Under **Assets**, click **Career-Agent-v0.2.0-beta.1-source.tar.gz**. It
   goes to your **Downloads** folder.
3. Double-click the downloaded file. macOS and most Linux desktops extract it
   into a folder called **Career-Agent-v0.2.0-beta.1** in Downloads.

### 3. Enter the Career Agent folder

A terminal always works "inside" one folder. `cd` means "change folder".
Copy this and press **Enter**:

```sh
cd ~/Downloads/Career-Agent-v0.2.0-beta.1
```

`~` means your home folder. If you moved the folder somewhere else, type
`cd `, drag the folder from Finder or your file manager into the Terminal
window, and press **Enter**.

To check where you are, type `pwd` and press **Enter**. The last part of the
answer should be `Career-Agent-v0.2.0-beta.1`.

### 4. Install

```sh
uv sync --locked --no-dev --python 3.12
```

This downloads Python 3.12 and the libraries into a `.venv` folder inside the
Career Agent folder. It ends with a list of installed packages. It can take a
few minutes the first time.

### 5. Start the demo

```sh
uv run --no-sync python scripts/launch.py --demo
```

When you see `Career Agent is running: http://127.0.0.1:8765/`, your
browser opens Career Agent. Leave the Terminal window open.

## What you see the first time

`127.0.0.1` is an address that means "this computer". The pages come from
the launcher window you just started, not from the internet, and nobody else
on the internet can open them. Two addresses are used:

- <http://127.0.0.1:8765/>: Career Agent.
- <http://127.0.0.1:8766/>: Resume Tailor Beta. You normally open it from
  Career Agent's side bar instead.

If the browser did not open by itself, copy `http://127.0.0.1:8765/` into the
browser's address bar and press **Enter**.

## Try the demo first

The demo has 21 invented jobs and an invented candidate, Alex Morgan. It uses
separate storage (`data/demo.db`, `data/demo-config` and `data/tailor-demo`)
and never touches your personal data. It never uses an AI provider.

Things to try:

- **Discover Jobs**: each card shows Search Fit, Posting completeness and
  whether anything in the posting rules you out.
- Click a job title, then **Why this fits your search**: every reason is
  quoted from the posting.
- **Resume Tailor** in the side bar: Alex Morgan's resume, evidence and the
  details that still need review.

When you are done, go to the launcher window and press **Ctrl+C** (hold the
**Ctrl** key and press **C**). The window says
`Both apps stopped.` and you can close it.

The demo and your personal workspace use the same two addresses, so only one
of them can run at a time.

## Set up your own search

Stop the demo first (**Ctrl+C** in its window), then start the normal
launcher:

- **Windows:** double-click **Start-Career-Agent.cmd** in the Career Agent
  folder.
- **macOS or Linux:** in the Terminal, inside the Career Agent folder, run
  `uv run --no-sync python scripts/launch.py`

The first start creates your local profile, **My profile**, with an empty
workspace. Career Agent then asks a few short questions, one at a time: the
work you want, the roles you have in mind, where you live, where companies
can hire you, work model, contract, levels, pay, your CV and a final review.
Each answer is saved as you go, and you can change any of them later in
**Settings & Sources**. **Finish setup later** skips the questions.

To collect jobs, use **Find jobs** or **Refresh due sources**. This is the
moment Career Agent contacts public job boards and employer job pages. It
sends search words and places, never your CV or profile. Nothing is collected
until you press one of these buttons.

A CV you add is read on your computer. Career Agent proposes statements from
it, and nothing becomes part of your Career Profile until you confirm it.

### More than one person

Several people can use one installation. Open the profile menu at the top of
the side bar to create or switch profiles. Each profile has its own settings,
CV, evidence, scores, applications and Resume Tailor workspace. Public job
postings are stored once, in a shared catalogue, so a posting collected for
one profile is visible to the others; the searches that found it stay
private to the profile that ran them.

Profiles are not accounts: there are no passwords. Anyone who can use your
Windows, macOS or Linux user account can open every profile's files.

## How to open Career Agent next time

**Windows**

1. Open File Explorer and go to the Career Agent folder (for example
   Downloads, then **Career-Agent-v0.2.0-beta.1-Windows**).
2. Double-click **Start-Career-Agent.cmd**.
3. Keep the window open while you use Career Agent.

You can make a desktop shortcut: right-click **Start-Career-Agent.cmd**,
choose **Show more options**, then **Send to** and **Desktop (create
shortcut)**. The shortcut starts the launcher from its own folder.

**macOS or Linux**

1. Open Terminal.
2. Enter the folder:
   ```sh
   cd ~/Downloads/Career-Agent-v0.2.0-beta.1
   ```
3. Start it:
   ```sh
   uv run --no-sync python scripts/launch.py
   ```
4. Keep the Terminal window open while you use Career Agent.

Everything you saved is still there, because it lives in the `data` folder
inside the Career Agent folder.

## How to stop Career Agent

Click the launcher window (on Windows) or the Terminal window, then press
**Ctrl+C**. Both apps stop and the window says `Both apps stopped.`
On Windows the window may then ask `Terminate batch job (Y/N)?`: type `Y` and
press **Enter**, or close the window. Closing the browser tab does not stop
Career Agent.

## Open a terminal in the Career Agent folder (Windows)

Backups, updates and a few settings use typed commands. This is the easiest
way to open a terminal that is already inside the right folder.

#### Windows: open PowerShell

1. Open the Career Agent folder in File Explorer.
2. Click an empty part of the **address bar** at the top of the window (the
   bar that shows the folder's path). The path turns into selected text.
3. Type `powershell` and press **Enter**.

A PowerShell window opens. The line before the cursor ends with the Career
Agent folder's name, for example:

```text
PS C:\Users\you\Downloads\Career-Agent-v0.2.0-beta.1-Windows>
```

Commands in this guide are pasted there, one at a time, followed by
**Enter**. Paste with **Ctrl+V** or a right-click.

## Backups

Stop Career Agent before a backup, an update or copying files.

There are two kinds of Career Agent backup:

- A **profile backup** holds one person's private data: settings, confirmed
  Career Profile, evidence, scores, saved jobs, applications and notes.
- A **catalogue backup** holds the shared public job postings that every
  profile reads. It has nothing private in it.

Resume Tailor keeps its own backup: in Resume Tailor, open the candidate menu
and choose **Export backup**.

**Windows.** [Open PowerShell in the Career Agent folder](#windows-open-powershell),
then paste this line to back up the first profile, **My profile**:

```powershell
.\.venv\Scripts\career-agent.exe backup --profile "My profile"
```

It writes a ZIP file into the `backups` folder and prints what it holds. For
another profile, put its name between the quotes. To see every profile's
name:

```powershell
.\.venv\Scripts\career-agent.exe profiles
```

For the shared catalogue:

```powershell
.\.venv\Scripts\career-agent.exe backup --catalogue --profile "My profile"
```

An installation updated from v0.1.0-alpha.2 keeps its postings inside the
first profile until it is split (see [Updating](#updating-to-a-new-version)),
so it has no catalogue to back up yet: its profile backup already holds the
postings.

**macOS or Linux.** In the Terminal, inside the Career Agent folder:

```sh
uv run --no-sync career-agent backup --profile "My profile"
uv run --no-sync career-agent backup --catalogue --profile "My profile"
```

Backups never contain API keys or the `.env` file. They are not encrypted, so
treat a profile backup like a copy of your CV. The `backups` folder is inside
the Career Agent folder: copy the ZIP files somewhere else (a USB drive or a
private cloud folder) if you want them to survive a lost computer.

## Updating to a new version

A new version goes into a **new folder**. You then copy your data across.
The old folder stays as it was until you decide to delete it, so nothing is
lost if something goes wrong.

1. Stop Career Agent (**Ctrl+C** in the launcher window).
2. Make a [profile backup](#backups) and a catalogue backup. If the old
   folder is **v0.1.0-alpha.2**, skip this step: its backup command is older
   and works differently. The update does not change the old folder, so it
   stays as your copy.
3. Download and extract the new version, as in
   [Path B](#path-b-on-windows-download-and-double-click). It gets a new
   folder name, for example **Career-Agent-v0.2.1-Windows**.
4. In File Explorer, open **View**, then **Show**, and tick **File name
   extensions**, so you see full names such as `search.local.yaml`.
5. From the **old** folder, copy these into the **new** folder:
   - the whole `data` folder;
   - the files in `config` whose names end in `.local.yaml` (into the new
     `config` folder);
   - the `.env` file, if you have one;
   - `companion\resume-tailor\.env`, if you created one (into the same place
     in the new folder);
   - the `backups` folder, if you want your backups next to the new version.
   If Windows asks whether to replace files, choose **Replace**.
6. Double-click **Start-Career-Agent.cmd** in the **new** folder. The first
   start installs the new version's libraries and updates your data to the
   new format. Your jobs, profiles and applications stay.
7. When everything looks right, you can delete the old folder. You do not
   have to.

On macOS and Linux, copy the same items between the two folders, then run the
install and start commands from [Path B](#4-install) in the new folder.

An installation from **v0.1.0-alpha.2** updates the same way (skip the
backup commands in step 2). It becomes the
first local profile, **My profile**, and keeps its postings in its own
database. Moving them into the shared catalogue is a separate step
(`career-agent catalogue split`, described in
[MULTI_PROFILE.md](MULTI_PROFILE.md)); nothing requires it.

## Optional features

Career Agent works without any of these. Each one is off until you turn it on.

### LinkedIn through JobSpy (experimental)

- **What it does:** searches LinkedIn's public job search for the roles and
  work phrases in your settings, and reads some of the postings it finds.
- **What leaves your computer:** short search phrases and places, sent to
  LinkedIn from your own internet connection. Career Agent never signs in to
  LinkedIn and never asks for or stores a LinkedIn password, cookie or
  session. Your CV and profile are not sent.
- **Cost:** none.
- **Risks:** LinkedIn restricts automated collection. It may refuse or slow
  down the requests, and results can be partial. When LinkedIn refuses, the
  source stops and waits a day before it is asked again instead of retrying.
- **Turn on:** in Settings & Sources, find **LinkedIn via JobSpy**, read the
  warning, tick the box and choose **Turn on for this profile**. After that,
  **Find jobs** includes it. It is off for every new profile.
- **Check:** after a run, the Source health table in Settings & Sources shows
  its result.
- **Turn off:** choose **Turn off** in the same place. A run in progress stops
  before its next search.

### Semantic matching

- **What it does:** an AI reads selected postings and says which parts of
  your search they support, quoting the posting. Career Agent checks every
  quote and does the scoring itself.
- **What leaves your computer:** your search phrases and the title and text of
  each selected posting, sent to the provider you choose. Never your CV,
  profile, evidence or applications.
- **Cost:** DeepSeek is paid per use: each run shows an estimate first and
  stops at its budget. Claude Code or Codex use your own subscription.
- **Turn on:** Settings & Sources, AI & Semantic Matching. A DeepSeek key can
  be added there; it is written to the `.env` file and never shown again.
- **Turn off:** do not start a run. Nothing is sent without one.

Details: [SEMANTIC_MATCHING.md](SEMANTIC_MATCHING.md).

### Local model reading (Ollama)

- **What it does:** a model running on your own computer reads one posting
  and writes a summary. It never changes Search Fit.
- **What leaves your computer:** nothing. Career Agent refuses any Ollama
  address that is not on this computer.
- **Cost:** none, but it is slow: on the test laptop's processor, `qwen3:4b`
  took about 3 minutes to read a 5,000-character posting.
- **Turn on:** install [Ollama](https://ollama.com/download), then in a
  terminal run `ollama pull qwen3:4b` (a download of a few GB). In a job's
  details, choose **Ask the local model to read it**.
- **Turn off:** do not start a reading. **Cancel** stops one in progress.

### Resume Tailor AI provider

Resume Tailor works without AI: Markdown and Word export are generated on your
computer. It can also use Anthropic, an OpenAI-compatible service or Ollama.
Those services receive job descriptions and the evidence you select, and
hosted ones charge you for use.

To turn it on under the launcher (Windows):

1. Stop Career Agent.
2. Open **Notepad** and type one line, with your own key:
   ```text
   ANTHROPIC_API_KEY=YOUR_API_KEY_HERE
   ```
   Choose **File**, **Save as**, go to the Career Agent folder and then
   `companion\resume-tailor`, set **Save as type** to **All files**, type
   `.env` as the file name and click **Save**. Without **All files**, Notepad
   saves `.env.txt`, which is not read. Do not share this value, paste it into
   an issue or show it in a screenshot.
3. [Open PowerShell in the Career Agent folder](#windows-open-powershell) and
   run these two lines:
   ```powershell
   $env:LLM_PROVIDER = "anthropic"
   .\Start-Career-Agent.cmd
   ```

The provider is used only while this PowerShell window's launcher runs. To
turn it off, stop the launcher and start it again by double-clicking, which
uses no provider. Demo mode never uses one.

### PDF export in Resume Tailor

PDF export and page counts need Microsoft Word or LibreOffice installed on the
computer. Without either, Resume Tailor says PDF is unavailable, and Markdown
and Word export still work.

## Troubleshooting

#### The launcher window says it cannot download (setup stopped)

`Setup stopped: Setup could not download what it needs.` means the first setup
could not reach the internet. Check your connection and double-click the
launcher again. Nothing you saved was changed.

#### "ports 8765 and 8766 are already in use"

Career Agent is probably already running in another launcher window. Use that
window's page (<http://127.0.0.1:8765/>), or press **Ctrl+C** in that window
and start again. The demo and personal mode cannot run at the same time.

To open the demo, or another profile, next to the one already running,
[open PowerShell in the Career Agent folder](#windows-open-powershell) and run
one of these:

```powershell
.\Start-Career-Agent.cmd -Port 8875 -Demo
.\Start-Career-Agent.cmd -Port 8875 -ProfileName "Profile name"
```

That copy uses <http://127.0.0.1:8875/> and <http://127.0.0.1:8876/>. The same
profile cannot be open in two windows at once.

#### PowerShell says running scripts is disabled

Windows blocks `.ps1` files downloaded from the internet. Use
`.\Start-Career-Agent.cmd` instead of `.\Start-Career-Agent.ps1`; it accepts
the same options.

#### The browser did not open

Copy `http://127.0.0.1:8765/` into your browser's address bar. The launcher
window must still be open.

#### The page says it cannot connect

The launcher is not running. Start it again, and keep its window open.

#### Resume Tailor says Career Agent switched to another profile

A Resume Tailor tab opened for one profile stays tied to it. Reload the tab,
or open Resume Tailor again from Career Agent's side bar.

#### PDF export is unavailable

Install Microsoft Word or LibreOffice, or use Markdown or Word export.

#### The local model is taking several minutes

That is normal on a laptop processor. A reading has a six-minute limit and
can be cancelled.

#### A source says "Refused by the site" or "needs attention"

The site refused Career Agent's requests; LinkedIn does this often. A refused
source waits a day before it is asked again, and a failed one waits an hour.
Nothing needs fixing. You can also turn LinkedIn off.

#### uv is not recognised

On macOS or Linux, close the Terminal window and open a new one after
installing uv. If it is still missing, run the installer line again and read
its last lines, which say where uv was installed.

On Windows you do not need uv on the command line: the launcher uses its own
copy in the `.tools` folder.

## Asking for help

Open an issue at
<https://github.com/thais-stephanie/career-agent-portfolio/issues> (you need a
free GitHub account) and include:

- your operating system and version (for example Windows 11);
- the Career Agent version (v0.2.0-beta.1);
- what you did: the file you double-clicked or the exact command;
- the exact error text, copied from the launcher window: select it with the
  mouse and press **Ctrl+C** (Windows; with text selected this copies instead
  of stopping anything) or **Command + C** (macOS) to copy it.

Do **not** include: your `.env` file, API keys, your CV, your Career Profile
or evidence, your search settings, application notes, anything from the
`data` or `backups` folders, or screenshots that show any of these.

## Uninstalling

Everything Career Agent stores is inside its folder:

| Inside the Career Agent folder | What it is |
|---|---|
| `data` | All profiles, the shared job catalogue, Resume Tailor workspaces and the demo. **Private.** |
| `config\*.local.yaml` | The first profile's search settings. **Private.** |
| `backups` | Backups you made. **Private.** |
| `.env` and `companion\resume-tailor\.env` | API keys, if you added any. **Secret.** |
| `.venv`, `.tools` | Installed Python libraries and uv. Safe to delete. |
| everything else | The program itself. |

**To remove the program and keep your data:** stop Career Agent, copy the
`data` folder, the `config\*.local.yaml` files, both `.env` files and
`backups` to a safe place (not a synced folder), then delete the Career Agent
folder.

**To remove everything, including your data:** first check that you do not
need anything from the private items above. Then stop Career Agent and delete
the Career Agent folder. This cannot be undone.

uv also keeps Python and a download cache outside the folder, which it
can share with other programs that use uv. On Windows they are in
`%APPDATA%\uv` and `%LOCALAPPDATA%\uv`; on macOS and Linux in `~/.local/share/uv`
and `~/.cache/uv`. They hold no personal data. Delete them only if nothing
else on your computer uses uv. On macOS and Linux the `uv` program itself is
`~/.local/bin/uv`.
