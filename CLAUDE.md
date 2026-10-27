# Rail feed: live train map

Goal: a live map of trains in Great Britain on our own server, built on Network Rail's open data feeds.

## What exists
- `test_feed.py`: connects to the feed and prints train movements for 30 seconds. Run it to check a login works: `.venv/bin/python test_feed.py`
- `.env`: Network Rail data feeds login (`NR_USERNAME`, `NR_PASSWORD`). Git-ignored. Never commit it, print it or put it in browser code. Each developer uses their own Network Rail login.
- Server: an Ubuntu VPS where Python 3 is installed and the feed test runs successfully.

## The feed
- STOMP at `publicdatafeeds.networkrail.co.uk:61618`, using `stomp.py`.
- Topic `/topic/TRAIN_MVT_ALL_TOC`. Each frame is a JSON array of messages.
- `header.msg_type`: 0001 activation, 0002 cancellation, 0003 movement, 0005 reinstatement, 0006 change of origin, 0007 change of identity, 0008 change of location.
- Movement body: `loc_stanox`, `event_type` (ARRIVAL/DEPARTURE), `actual_timestamp` (ms), `timetable_variation` (minutes), `variation_status` (ON TIME/EARLY/LATE/OFF ROUTE), `train_id`.
- `train_id` characters 3-6 are the headcode. Its first digit is the train class: 1-2 passenger, 4 and 6-8 freight, 5 empty coaching stock.
- Locations are STANOX codes and include junctions, sidings and yards, not just stations. About 4 messages a second across all of Britain.

## Reference data
- **CORPUS**: STANOX -> TIPLOC, CRS (`3ALPHA`) and name (`NLCDESC`). Download from `https://publicdatafeeds.networkrail.co.uk/ntrod/SupportingFileAuthenticate?type=CORPUS` with HTTP basic auth (same login). It redirects to a gzipped JSON file of about 790 KB. Blank fields are a single space. Several rows can share a STANOX: prefer the row with a CRS. Only rows with a CRS are passenger stations.
- **NaPTAN** (Department for Transport): station latitude/longitude. Rail stations have ATCO codes `9100` + TIPLOC. Stations only.
- **BPLAN** (Network Rail): OS grid coordinates for junctions and other timing points. Needed for stage 2.
- Don't use openraildata/reference-data on GitHub: last updated 2022, short names only, no CRS.

## Plan
1. Station names from CORPUS.
2. Station positions from NaPTAN.
3. Server: an always-on process that holds the feed connection, keeps the latest position of each train and pushes changes to browsers (WebSocket or Server-Sent Events). The login stays on the server.
4. Map page: Leaflet or MapLibre, one dot per train coloured by lateness. First version is stations only: ignore events at non-station locations.

Later: junction positions from BPLAN, then timetable-based smoothing so trains move between points.

## Rules
- Build the smallest working version first, then improve it.
- On the VPS, run the server as its own non-root user, kept running by systemd.
- Show this credit on the map: "Contains information of Network Rail Infrastructure Limited licensed under the Open Government Licence."
