"""
AirBuddy 2.1 Font Registry

Semantic font roles:
- SMALL   : secondary labels, compact UI text, version tags, status hints (sans-serif)
- MED     : primary labels (Temperature, eCO2, Humidity)
- LARGE   : hero numbers + title text (time, ppm, °C, "airBuddy")
- SYMBOLS : bars, blocks, icons, UI glyphs
"""

# --- Font data modules (ezFBfont compatible) ---

# Symbols / blocks / extended glyphs
# from . import ezFBfont_amstrad_cpc_extended_supp_08 as SYMBOLS

# Sans-serif text fonts
from . import ezFBfont_PTSansNarrow_07_ascii_11 as SMALL
from . import mulish14 as MED

# Serif / hero font (time, big numbers, title)
from . import arvo24 as LARGE
from . import arvo16 as ARVO16
from . import arvo20 as ARVO20



# --- Registry helpers ---

_REGISTRY = {
    "small": SMALL,
    "med": MED,
    "medium": MED,
    "large": LARGE,
    "hero": LARGE,
    "arvo16": ARVO16,
    "arvo20": ARVO20,
}


def get(name: str):
    """Return a font module by semantic name."""
    key = (name or "").strip().lower()
    if key not in _REGISTRY:
        raise KeyError(
            "Unknown font '{}'. Available: {}".format(
                name, ", ".join(sorted(_REGISTRY.keys()))
            )
        )
    return _REGISTRY[key]

def list_fonts():
    """Return available font modules."""
    return dict(_REGISTRY)
