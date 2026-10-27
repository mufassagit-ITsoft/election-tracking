# Map layout 
import json
import math
from pathlib import Path

__author__ = "Mustafa Prodhan"

STATES_GEOJSON = Path(__file__).parent / "seed" / "us_states.geojson"
 
NAME_TO_POSTAL = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
    "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
 
# (col, row) of each state's block in the tile grid used for the House layer
TILE_GRID = {
    "AK": (0, 0), "ME": (10, 0),
    "VT": (9, 1), "NH": (10, 1),
    "WA": (0, 2), "ID": (1, 2), "MT": (2, 2), "ND": (3, 2), "MN": (4, 2), "IL": (5, 2), "WI": (6, 2),
    "MI": (7, 2), "NY": (8, 2), "RI": (9, 2), "MA": (10, 2),
    "OR": (0, 3), "NV": (1, 3), "WY": (2, 3), "SD": (3, 3), "IA": (4, 3), "IN": (5, 3), "OH": (6, 3),
    "PA": (7, 3), "NJ": (8, 3), "CT": (9, 3),
    "CA": (0, 4), "UT": (1, 4), "CO": (2, 4), "NE": (3, 4), "MO": (4, 4), "KY": (5, 4), "WV": (6, 4),
    "VA": (7, 4), "MD": (8, 4), "DE": (9, 4),
    "AZ": (1, 5), "NM": (2, 5), "KS": (3, 5), "AR": (4, 5), "TN": (5, 5), "NC": (6, 5), "SC": (7, 5),
    "DC": (8, 5),
    "OK": (3, 6), "LA": (4, 6), "MS": (5, 6), "AL": (6, 6), "GA": (7, 6),
    "HI": (0, 7), "TX": (3, 7), "FL": (8, 7),
}
CELL = 8  # tile-grid cell is 8x8 district squares; the largest state (CA, 52) fits in 8x7
 
 
def state_features():
    # [(postal, name, geometry)] from the bundled GeoJSON (Puerto Rico left out).
    d = json.loads(STATES_GEOJSON.read_text())
    out = []
    for f in d["features"]:
        name = f["properties"]["name"]
        if name in NAME_TO_POSTAL:
            out.append((NAME_TO_POSTAL[name], name, f["geometry"]))
    return out
 
 
def house_tiles(districts):
    # house districts on the state 
    out = []
    for st, names in sorted(districts.items()):
        n = len(names)
        if st not in TILE_GRID or n < 1:
            continue
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        c0, r0 = TILE_GRID[st]
        ox = c0 * CELL + (CELL - cols) / 2
        oy = r0 * CELL + (CELL - rows) / 2
        for i in range(n):
            out.append((f"{st}-{names[i]}", {"x": ox + i % cols, "y": oy + i // cols, "w": 1}))
    return out