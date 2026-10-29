**1.5.1: gentler adjusted copies**

This only matters if you've chosen "Let StockUp make adjusted copies". In the default exact-files mode, nothing is ever re-saved.

When StockUp does re-save a photo (sRGB, resize for upload, or a smaller file for a site's limit), the copy is now as faithful as it can be:
- **Full colour detail.** Copies keep the original's chroma subsampling instead of storing colour at a quarter of the resolution. In testing, a resized copy went from 21.3 dB to 46.6 dB closeness to a perfect resize; fine coloured edges are no longer smeared.
- **Never lower quality:** copies are saved at quality 95 or the original's own quality, whichever is higher.
- **Truer colours when converting to sRGB:** relative colorimetric with black point compensation, Photoshop's default.
- **Black-and-white stays black-and-white** when only resizing.
- **All metadata carried over,** now including file comments.
- When a site's file-size limit needs a smaller file, quality steps down gently first, and colour detail is reduced only if that isn't enough.

Copies are larger as a result (about twice the size in testing), still well within every site's limits.

**Downloads:** `StockUp-Mac.zip` (Apple silicon), `StockUp-Windows.zip`, or `stockup.py`. Script users can press **Update now** in StockUp. App users: download the new zip, quit the old app, and open the new one.
