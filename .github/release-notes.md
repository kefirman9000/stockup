**StockUp 1.8.3: Alamy uploads reach Alamy**

- **Fix: Alamy uploads go into Alamy's Stock folder.** Alamy only takes photos from the folders on its FTP server (Stock, Live News, Archive stock, Vectors). StockUp was putting them at the top level, where Alamy doesn't look, so they never reached Alamy Image Manager, even though the upload and the connection test worked.
- **Photos sent to Alamy's top level before go again.** They don't count as sent, so the next upload sends them to Stock. To use another folder (Archive stock, say), type it as the folder in Alamy's login.
- **StockUp's features are moving into [IPTC Tagger](https://github.com/kefirman9000/iptc-tagger).** IPTC Tagger has the same fix.

Download the zip for your computer below.
