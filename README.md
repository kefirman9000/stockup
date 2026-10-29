# StockUp

**Send one batch of stock photos to every agency you sell on, without typing anything twice.**

StockUp works with Shutterstock, Adobe Stock, Dreamstime, Alamy, iStock, Depositphotos, 123RF and Pond5.

You keyword and title your photos once, the way you already do. StockUp reads those words from the files and checks each photo against every site's rules, offering a fix for anything a site would reject. It then makes the files each site asks for and uploads to all of them at once.

![StockUp in dark mode](screenshots/review-dark.png)

## Download

Get the newest version from the **[Releases page](../../releases/latest)**:

| Your computer | Download |
|---|---|
| Mac with Apple silicon (M1 or newer) | `StockUp-Mac.zip` |
| Windows | `StockUp-Windows.zip` |
| Intel Mac, Linux, or you'd rather use Python | `stockup.py` (see [Running the script](#running-the-script)) |

**First time opening it**
- **Mac:** unzip, drag **StockUp** to Applications, then right-click it and choose **Open**. macOS asks this once because the app isn't from the App Store. If it still won't open, go to *System Settings › Privacy & Security* and click **Open Anyway**.
- **Windows:** unzip and double-click **StockUp**. If a blue "Windows protected your PC" box appears, click **More info › Run anyway**.

StockUp asks which folder holds your batch, then opens in your web browser. Nothing is sent anywhere until you click **Upload**.

## What it does for you

- **Checks every photo for every site.** Each photo has a badge per site: green means ready, amber means it needs a fix, and crossed out means it won't be sent. **Check photos** lists everything by site, with a button to fix it.
- **Fixes problems for you**, only when you click. Your originals are never changed; fixes make copies just for that site.
  - Shortens titles and descriptions that are too long, and shows the suggestion first.
  - Gives files a safe name where a site would mangle accents or symbols.
  - Makes **sRGB** copies of Adobe RGB, ProPhoto, CMYK or grayscale photos.
  - Makes smaller or resized copies of files over a site's limit.
  - Makes standard (baseline) JPEGs for Alamy.
- **Skips photos a site won't take,** like ones under its minimum size, so they don't fail review. **Send anyway** overrides this.
- **Marks editorial in bulk.** Shift-click a range and click **Editorial**. Photos are also marked for you if the caption already starts with a place and date, or if they have a keyword or Lightroom color label you choose.
- **Keeps your captions as written** when they already start with a place and date. For the rest, StockUp adds the place and date in each site's format.
- **Uploads to the sites you tick, all at once.** If the connection drops, press **Upload** again and it carries on.
- **Keeps your passwords in your computer's keychain:** Keychain on Mac, Credential Manager on Windows.
- **Light and dark mode,** and an **Update** button when a new version is out.

| Check photos | One photo, site by site |
|---|---|
| ![Check photos](screenshots/checks-dark.png) | ![Site details](screenshots/details-dark.png) |

| Logins | Uploading |
|---|---|
| ![Logins](screenshots/logins-dark.png) | ![Uploading](screenshots/upload-dark.png) |

<details><summary>Settings and light mode</summary>

![Settings](screenshots/settings-dark.png)
![StockUp in light mode](screenshots/review-light.png)
</details>

## Each batch

1. Open StockUp and choose the batch folder.
2. The first time, open **Settings** and tick the sites you sell on. Then open **Logins** and add each one.
3. Pick photos and click **Editorial** or **Creative**. Set categories, plus the place and date for editorial shots.
4. Open **Check photos** and apply the fixes you want.
5. Click **Make site files**, tick the sites next to **Send to**, and click **Upload**.
6. Finish on each website. **Make site files** shows the next step for every site:

| Site | What's left to do |
|---|---|
| Shutterstock | On the Submit page, click **CSV** and choose `shutterstock.csv`. This sets descriptions, keywords, categories and the editorial flag. |
| Adobe Stock | Under *Uploaded Files › New*, click **Upload CSV**. Tick editorial for the files in `editorial_list.txt`. |
| Depositphotos | Upload `depositphotos.csv`. This sets the editorial flag too. |
| 123RF | Upload `123rf.csv`. Tick editorial for the files in `editorial_list.txt`. |
| Pond5 | *Uploads › Apply CSV › pond5.csv*. Pond5 only documents its video spreadsheet, so check your first batch. |
| Dreamstime, Alamy | They read your titles and keywords from the photos. Tick editorial for the files in `editorial_list.txt`. |
| iStock | No upload from StockUp. Drag the `creative` folder into a Creative batch and `editorial` into an Editorial batch. |

Everything is saved in a `stockup_output` folder inside your batch folder, with one folder per site. `checks.txt` lists anything still worth a look.

## What StockUp checks

| | Smallest photo | Biggest file | Color | Keywords | Text limits |
|---|---|---|---|---|---|
| Shutterstock | 4 MP | 50 MB | sRGB | 7–50 | Description up to 200 characters, at least 5 words |
| Adobe Stock | 4 MP (100 MP max) | 45 MB | sRGB | 5–49 | Title up to 70 characters, file name up to 30 |
| Dreamstime | 3 MP (70 MP max) | | | | |
| Alamy | 17 MB when opened (about 6 MP) | | | | Standard (baseline) JPEG |
| iStock | | 50 MB | | | |
| Depositphotos | 3.8 MP | 50 MB | | 8–50 | Description up to 250 characters |
| 123RF | 6 MP | 30 MB | | 7+ | Description up to 180 characters, 4 GB per FTP batch |
| Pond5 | 2 MP, 1200 px each side (170 MP max) | | sRGB or Adobe RGB | 5–50 | Title up to 80 characters |

These come from each site's contributor help pages. Sites change their rules, so check the help pages if a photo is rejected for a reason StockUp didn't flag.

## Good to know

- **Adobe Stock uploads** only work if Adobe has given your account SFTP login details (shown on its Upload page). Otherwise, drag the files into Adobe's website. The spreadsheet still saves the typing.
- **Adobe and editorial:** Adobe only accepts *illustrative* editorial. StockUp flags editorial photos for Adobe so you can choose to skip them.
- **Pond5** uses a separate FTP password, which you set in Pond5's *Dashboard › Account › FTP*.
- **Photos must be JPEGs** with titles and keywords saved in them, the way Lightroom, Bridge, Photo Mechanic and most keywording tools save them.

## Running the script

If you'd rather not use the app, install [Python](https://www.python.org/downloads/), download `stockup.py`, and run it:

```
pip install pillow keyring paramiko      optional extras: fixes, keychain, Adobe SFTP
python stockup.py                        then choose your batch folder
```

On a Mac, use `python3`, and once after installing Python, run *Applications › Python 3 › Install Certificates*. The script updates itself from the **Update now** button.

Other commands:

```
python stockup.py build  "My Batch"                             make the site files only
python stockup.py upload "My Batch" --sites shutterstock,alamy   upload without the page
python stockup.py test                                          check your logins
python stockup.py update                                        install the newest version
```
