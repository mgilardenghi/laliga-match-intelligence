# LaLiga Match Intelligence

Predicts Spanish La Liga matches, simulates the rest of the season, and measures its own accuracy against real results and the betting market. A team focus mode analyzes one club in depth.

Status: planning. See [MVP specification](docs/spec/mvp.md) and [architecture decisions](docs/adr/).

## Data sources

- Match results, statistics and odds: [football-data.co.uk](https://www.football-data.co.uk/)
- Fixtures: [openfootball/football.json](https://github.com/openfootball/football.json) (CC0)

Raw data is downloaded at runtime and is not included in this repository.

This project is not affiliated with LaLiga or any football club.
