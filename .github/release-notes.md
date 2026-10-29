**1.6.1: opens in seconds on every Mac**
- **Fixed a long wait when opening StockUp on some Macs.** Python's built-in server looked up the network name of the computer's own address every time StockUp started. On some Macs that lookup hangs for over 30 seconds before giving up; on GitHub's test Mac it took 35 seconds. StockUp no longer does the lookup. A new test makes sure it never comes back.
- StockUp now records how long each step of opening takes, so the release checks can catch any slowdown on Mac and Windows.

**Downloads:** `StockUp-Mac.zip` (Apple silicon) or `StockUp-Windows.zip` (unzip, open the StockUp folder, double-click StockUp.exe), or `stockup.py`. Script users can press **Update now** in StockUp. App users: download the new zip, quit the old app, and open the new one.
