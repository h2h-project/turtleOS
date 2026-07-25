# ---- LEGACY-NEO6M: delete this file when NEO-6M support is dropped ----
# Superseded by src.sensors.xiao_gnss (GnssModule).
# Kept as a shim so any code that still imports Ublox6GPS does not crash.
from src.sensors.xiao_gnss import GnssModule as Ublox6GPS  # noqa: F401
# ---- END LEGACY-NEO6M ----
