# QB Pressure Replay
QB Pressure Replay is a standalone Python application that turns the supplied 2021 NFL regional event tracking data into an interactive browser dashboard for coaches and broadcasters. data from:https://github.com/ThompsonJamesBliss/nfl-big-data-bowl-regional-event-data

Users select a game and play to view a zoomed field replay with a consistent offensive direction, synchronized pass-rusher distance curves, and summaries of time to the replay endpoint, closest approach, and first entry into a user-defined 2-yard proximity zone.

The dashboard reveals which rushers approach the quarterback and when, places those movements alongside whole-play PFF hit, hurry, and sack labels, and compares rushers across supported plays in the selected game.

It also compares PFF pressure-label rates between plays with and without a rusher entering the zone, providing a descriptive check rather than proof of causation or a validated pressure metric; timing assumes 10 frames per second with a coarse timestamp consistency check.

Install pandas and numpy, run python QB_PRESSURE_REPLAY.py, and select or confirm the data folder before entering a game ID; the generated HTML supports playback, frame selection, filtering, and sorting, while unsupported plays are recorded in an exclusions CSV.
