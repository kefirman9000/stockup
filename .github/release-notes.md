**New in 1.7: StockUp updates itself**
- **Update now really is one click.** When a new version is out, the banner's **Update now** downloads it (with a progress bar), checks it, installs it, and reopens StockUp on the batch you had open. Your current version is kept as a backup.
- **Only exact releases are ever installed.** Every release now publishes SHA-256 fingerprints of its files (`SHA256SUMS`). StockUp refuses any download that doesn't match exactly, and checks that the new app really is the new version before swapping it in. If anything goes wrong, your current version is left exactly as it was. The script version checks the fingerprint too.
- **Never mid-upload.** StockUp won't update while an upload is running.
- **Tells you if it can't.** On a Mac, StockUp needs to be in your Applications folder to replace itself; macOS runs apps still sitting in their download folder from a temporary read-only copy. StockUp says so, and offers a **Download** button.

**New: Site steps, so categories and editorial really reach each site**
- Shutterstock doesn't read categories or the editorial setting from photos sent by FTP; they only arrive through its spreadsheet, applied on the Submit page once the photos show up there. Most other sites work the same way. When an upload ends, StockUp now walks you through that last step for each site. It gives you a spreadsheet with only the photos you just uploaded, plus buttons to open the site and show the file.
- Each photo shows its status per site: **✓ applied**, **spreadsheet not applied**, or **changed since applied**. There's also a filter for photos still waiting on a site step.
- Dreamstime's spreadsheet is sent for you, now with only the photos already on Dreamstime.
- The Shutterstock spreadsheet now matches Shutterstock's sample columns exactly.

**This is the last update you install by hand.** Download the zip for your computer below. From now on, StockUp updates itself.
- Mac: unzip, drag StockUp into Applications (replacing the old one), and open it from there.
- Windows: unzip, replace your old StockUp folder with the new one, and double-click StockUp.exe.

Script users can press **Update now** in StockUp as usual.
