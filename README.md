# StockUp

**Send one batch of stock photos to Shutterstock, Adobe Stock, Dreamstime, Alamy and iStock, without typing anything twice.**

You keyword and title your photos once, the way you already do. StockUp reads those words straight from the files, lets you mark which shots are editorial in a couple of clicks, makes the files each site asks for, and uploads to every site at the same time.

![StockUp in dark mode](screenshots/review-dark.png)

## What it does for you

- **Shows the whole batch at a glance.** Every photo with its title, keyword count and categories.
- **Marks editorial in bulk.** Shift-click to pick a range, then click **Editorial**. Photos can also be marked for you, if they already have a caption like `CAMDEN, MAINE - JULY 4, 2026: …`, a keyword such as `editorial`, or a color label you choose in Lightroom.
- **Keeps your captions as you wrote them.** If a caption already starts with a place and date, StockUp leaves it alone. For the rest, it adds the place and date for you.
- **Sets categories in bulk** for Shutterstock and Adobe.
- **Uploads to the sites you tick**, all at once. If your connection drops, press **Upload** again and it picks up where it stopped.
- **Makes each site's files:**
  - Shutterstock's spreadsheet, including the editorial flag
  - Adobe's spreadsheet
  - creative and editorial folders ready to drag into iStock
  - a list of anything a site is likely to reject
- **Light and dark mode.** It follows your computer, or you can choose Light or Dark at the top.
- **Updates itself.** When a new version is out, an **Update now** button appears at the top of the page.

| Settings | Uploading |
|---|---|
| ![Settings](screenshots/settings-dark.png) | ![Uploading](screenshots/upload-dark.png) |

<details><summary>Light mode</summary>

![StockUp in light mode](screenshots/review-light.png)
</details>

## Get started

1. **Install Python** (free) from [python.org](https://www.python.org/downloads/).
   - On Windows, tick **"Add Python to PATH"** during setup.
   - On a Mac, afterwards open *Applications › Python 3* and double-click **Install Certificates**.
2. **Download `stockup.py`** from the [latest release](../../releases/latest) and put it in a folder of its own.
3. **Open it.**
   - **Windows:** double-click `stockup.py`.
   - **Mac:** open Terminal, type `python3 ` (with a space), drag `stockup.py` into the window and press Return.

   Pick your batch folder and the page opens in your browser.
4. **Add your logins once.** The first run creates `stockup_sites.ini` next to `stockup.py`. Open it in any text editor and fill in your username and password for each site. Then click **Check logins** on the page.

## Each batch

1. Open StockUp and choose the folder with the batch.
2. Pick photos and click **Editorial** or **Creative**. Set categories, and the place and date for editorial shots that need them.
3. Tick the sites next to **Send to**, then click **Make site files** and **Upload**.
4. Finish on the websites:

| Site | What's left to do |
|---|---|
| Shutterstock | On the Submit page, click **CSV** and choose `shutterstock.csv`. This adds descriptions, keywords, categories and the editorial flag. |
| Adobe Stock | Under *Uploaded Files › New*, click **Upload CSV** and choose the `adobe_…csv` file. |
| Dreamstime, Alamy | They read your titles and keywords from the photos. Tick editorial for the files listed in `editorial_files.txt`. |
| iStock | Drag `istock_creative` into a Creative batch and `istock_editorial` into an Editorial batch. |

All of these files are saved in a `stockup_output` folder inside your batch folder.

## Good to know

- **Your passwords stay on your computer**, in `stockup_sites.ini`. Don't share that file.
- **Adobe Stock upload** only works if Adobe has given your account SFTP login details (shown on its Upload page). Otherwise, drag the photos into Adobe's website. The spreadsheet still saves you the typing.
- **Adobe and editorial:** Adobe only accepts *illustrative* editorial, not news-style photos.
- **Photos must be JPEGs** with titles and keywords saved in them, the way Lightroom, Bridge, Photo Mechanic and most keywording tools save them.
- **Thumbnails load faster** if you also install Pillow: `pip install pillow`.

## Other ways to run it

```
python stockup.py "My Batch"                                    open the page for a folder
python stockup.py build  "My Batch"                             make the site files only
python stockup.py upload "My Batch" --sites shutterstock,alamy   upload without the page
python stockup.py test                                          check your logins
python stockup.py update                                        install the newest version
```
