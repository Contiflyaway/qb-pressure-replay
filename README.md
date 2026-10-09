# QB Pressure Replay
QB Pressure Replay is a standalone Python application that turns the supplied 2021 NFL regional event tracking data into an interactive browser dashboard for coaches and broadcasters, using data from the [NFL Big Data Bowl Regional Event Data repository](https://github.com/ThompsonJamesBliss/nfl-big-data-bowl-regional-event-data).

Users select a game and play to view a zoomed field replay with a consistent offensive direction, synchronised pass-rusher distance curves, and summaries of time to the replay endpoint, closest approach, and first entry into a user-defined 2-yard proximity zone.

The dashboard reveals which rushers approach the quarterback and when, displays whole-play PFF hit, hurry, and sack labels, and compares rushers across supported plays in the selected game.

It also compares PFF pressure-label rates between plays with and without a rusher entering the zone; these comparisons are descriptive rather than causal, the 2-yard threshold is not an official or validated pressure metric, and timing assumes 10 frames per second with a coarse timestamp consistency check.

To run the application, install dependencies with `python -m pip install pandas numpy`, then run `python qb_pressure_replay.py` to open a setup window: the first field selects the complete data folder containing `games.csv`, `plays.csv`, `players.csv`, `pffScoutingData.csv`, and the `tracking` subfolder, while the second field accepts a `tracking_ID` such as `2021090900`; click **Open Replay** to open the dashboard in your browser, where you can select plays, play or pause the replay, inspect individual frames, filter, and sort, with excluded plays recorded in a CSV file.
<img width="2496" height="1403" alt="image" src="https://github.com/user-attachments/assets/28af5e7a-1b1d-4895-9832-9e3d57d620f4" />
