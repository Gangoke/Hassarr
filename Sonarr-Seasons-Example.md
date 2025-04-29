# Adding "The Voice" Seasons 1, 2, and 3 to Sonarr

## Example Service Call

```yaml
service: hassarr.add_tv_show
data:
  title: "The Voice"
  seasons: "seasons 1, 2, and 3"
```

## How It Works

When you make this service call, Hassarr processes it in the following way:

1. **Service call received** with title "The Voice" and seasons "seasons 1, 2, and 3"

2. **Seasons parsing**:
   ```python
   parsed_seasons = parse_seasons_input("seasons 1, 2, and 3")
   # This extracts [1, 2, 3] from the string
   ```

3. **API lookup**:
   - Searches Sonarr for "The Voice"
   - Retrieves the show details including available seasons

4. **Season monitoring setup**:
   - Creates a seasons array where:
     - Seasons 1, 2, and 3 will have `"monitored": true`
     - All other seasons (including season 0/specials if available) will have `"monitored": false`

5. **Example payload** sent to Sonarr:
   ```json
   {
     "title": "The Voice",
     "titleSlug": "the-voice",
     "tvdbId": 174331,
     "images": [...],
     "year": 2011,
     "rootFolderPath": "/tv",
     "qualityProfileId": 1,
     "monitored": true,
     "addOptions": {
       "searchForMissingEpisodes": true
     },
     "seasons": [
       {"seasonNumber": 0, "monitored": false},
       {"seasonNumber": 1, "monitored": true},
       {"seasonNumber": 2, "monitored": true},
       {"seasonNumber": 3, "monitored": true},
       {"seasonNumber": 4, "monitored": false},
       {"seasonNumber": 5, "monitored": false},
       ...
     ]
   }
   ```

6. **Result**:
   - Only episodes from seasons 1, 2, and 3 will be monitored and downloaded
   - Other seasons will be added to Sonarr but not monitored or downloaded

## Flexible Season Input

The integration is designed to handle many different ways of specifying seasons:

| Input Format | Interpretation |
|-------------|---------------|
| `"all"` | All seasons will be monitored |
| `"Season 1"` | Only Season 1 will be monitored |
| `"seasons 1, 2, and 3"` | Only Seasons 1, 2, and 3 will be monitored |
| `"S1, S2, S3"` | Only Seasons 1, 2, and 3 will be monitored |
| `"1,2,3"` | Only Seasons 1, 2, and 3 will be monitored |
| `[1, 2, 3]` | Only Seasons 1, 2, and 3 will be monitored |

The integration automatically extracts the season numbers from whatever format you provide.

Similar code found with 1 license type
