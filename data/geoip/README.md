# GeoLite2 database drop-in

Place the MaxMind GeoLite2 City database file in this directory as
`GeoLite2-City.mmdb` (manual download from MaxMind; the file is licensed and
never committed — see `.gitignore`).

The path is configurable via the `GEOIP_DB_PATH` environment variable
(default: `data/geoip/GeoLite2-City.mmdb`). The API reads the file lazily at
the point of use: requests that do not require IP geolocation (first session,
private/reserved IPs) work without it.
