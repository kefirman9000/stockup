**StockUp 1.8.1: Shutterstock gets your title**

- **Fix:** Shutterstock's spreadsheet has one text column. It's called *Description*, but Shutterstock shows that text as the photo's **title**. StockUp put the photo's caption (description) there, so photos tagged with a full title and a longer, sentence-style caption, as IPTC Tagger saves them, showed the caption on Shutterstock instead of the title. `shutterstock.csv` now gets your **title**, exactly as written.
- The caption is still used when the title is under 5 words (Shutterstock's minimum), or when the caption is already written as an editorial caption (place and date first) and the title isn't.
- To send the caption as before, untick **Settings › Titles and captions › Send Shutterstock my title rather than the caption**.

Download the zip for your computer below, or press **Update now** in StockUp.
