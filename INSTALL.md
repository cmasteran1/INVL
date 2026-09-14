# Installing INVL Hub

INVL Hub runs on one computer in your workplace. Your counter devices find
it over your Wi-Fi network and report their counts to it.

> **You will see a security warning the first time you open it. This is
> expected.** INVL Hub is not distributed through the Mac App Store or the
> Microsoft Store, so your computer doesn't recognise the publisher yet. The
> steps below tell your computer to trust it. You only do this once.

---

## Before you start

**Check the download is intact.** The download page lists a SHA-256 hash for
each file. Compare it with your copy:

macOS — open Terminal and run:

```bash
shasum -a 256 ~/Downloads/INVLHub-*.dmg
```

Windows — open PowerShell and run:

```powershell
Get-FileHash -Algorithm SHA256 "$HOME\Downloads\INVLHub-windows-x64.zip"
```

If the value doesn't match the one on the download page, delete the file and
download it again. Don't install it.

---

## macOS

1. Open the downloaded `.dmg` file.
2. Drag **INVL Hub** onto the **Applications** folder.
3. Open your **Applications** folder and double-click **INVL Hub**.
4. macOS will block it, with a message like *"Apple could not verify Inventory
   Hub is free of malware"*. Click **Done**.
5. Open **System Settings** → **Privacy & Security**.
6. Scroll down to the **Security** section. You'll see *"INVL Hub was
   blocked to protect your Mac."* Click **Open Anyway**.
7. Confirm with your password or Touch ID, then click **Open**.

INVL Hub now opens normally every time.

### If step 6 shows no "Open Anyway" button

Some versions of macOS report the app as *"damaged and can't be opened"* and
offer no override. That message is misleading — it means the download was
quarantined, not that anything is wrong with the file. Clear the quarantine
flag from Terminal:

```bash
xattr -dr com.apple.quarantine /Applications/Inventory\ Hub.app
```

Then open the app normally. If you'd rather not use Terminal, contact us and
we'll walk you through it.

---

## Windows

1. **Before extracting**, right-click the downloaded `.zip` file → **Properties**
   → tick **Unblock** at the bottom → **OK**. This saves you a warning on every
   file inside.
2. Right-click the `.zip` → **Extract All…** and choose where to put it, for
   example `C:\Program Files\INVLHub` or your Desktop.
3. Open the extracted `INVLHub` folder and double-click **INVLHub.exe**.
4. Windows shows a blue **"Windows protected your PC"** box. Click **More info**,
   then **Run anyway**.

INVL Hub now opens normally every time.

---

## Allow it through the firewall — this one matters

The first time INVL Hub runs, your computer asks whether to allow incoming
network connections.

- **macOS:** *"Do you want the application 'INVL Hub' to accept incoming
  network connections?"* → click **Allow**.
- **Windows:** Windows Defender Firewall prompt → tick **Private networks** →
  click **Allow access**.

**If you deny this, your counter devices will never reach the hub.** The app
will look like it's working — the dashboard opens and behaves normally — but
every device will sit there failing to connect. If you clicked Deny by mistake:

- macOS: **System Settings** → **Network** → **Firewall** → **Options** → find
  INVL Hub and set it to *Allow incoming connections*.
- Windows: **Windows Security** → **Firewall & network protection** → **Allow an
  app through firewall** → find INVL Hub and tick **Private**.

---

## First run

INVL Hub opens its own window showing the dashboard. Leave it running while
you're using your counter devices — closing the window quits the app and the
devices will stop being able to report.

**Check the bottom of the dashboard.** If there's a red banner there, it's
telling you about something that will stop your devices connecting — most often
that another copy of INVL Hub is already running, or that port 8000 is in
use by another program. Fix that before setting up devices.

---

## Where your data is kept

Your counts live on your own computer, not on the internet. Uninstalling and
reinstalling the app does not delete them.

| System | Location |
|---|---|
| macOS | `~/Library/Application Support/INVLHub/` |
| Windows | `%APPDATA%\INVLHub\` |

Use **Snapshot backup** at the bottom of the dashboard to save a dated copy into
that same folder, or **Backup DB** in the toolbar to save a copy anywhere you
like. Do this before moving to a new computer.
