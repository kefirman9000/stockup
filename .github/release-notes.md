**1.4.1: tested on every release**
- **Automated tests.** StockUp now has 48 tests covering the safety features: exact-file uploads, originals never changing, GPS removal, Pause and Stop never leaving half a photo on a site, no overwriting, no double uploads, encrypted connections, every site's spreadsheet, and category picking. They run on Mac, Windows and Linux after every change, and a release is only published if all of them pass.
- **Resize for upload** now leaves a photo alone if resizing would shrink it by less than 10%, since re-saving it isn't worth it.
- Ready for an upcoming change in the image library StockUp uses.

**Downloads:** `StockUp-Mac.zip` (Apple silicon), `StockUp-Windows.zip`, or `stockup.py`. Script users can press **Update now** in StockUp. App users: download the new zip, quit the old app, and open the new one.
