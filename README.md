QO-100 RTTY Sprint Logger
==========================

This program uses MMTTY as the RTTY modem and continuously reads the Received-log TXT file.

CONFIGURATION
1. In MMTTY, enable writing received text to a TXT file.
2. In the logger, press "Choose RX file..." and select that file.

Operation (with MMTTY running)
1. First three double-clicks: Call → Locator → Member Number; after the number, the sequence stops;
2. To correct, click the box, then double-click the correct value;
3. To correct manually, type directly in the box;
4. The locator is verified only at the time the QSO is logged
5. After recording the QSO, the sequence automatically restarts from Call;

The program does NOT try to understand what you clicked: it simply copies the text
into the box chosen by the operator. 
Validation (locator only) occurs when you log the QSO.

The log is immediately saved in ADIF with automatic UTC.

<b>The ADIF file is saved in C:\Users\<user>\Documents\QO100_RTTY_Sprint_2026.adi</b>
