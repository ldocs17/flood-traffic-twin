# Crop the Norfolk Hampton Blvd district network to a ~1.8km x 2.4km study area
# around the CNN-LSTM flood model's 320m x 320m domain.
#
# Source net:  C:\Users\dcost\ChandraMentorship\sumo_norfolk\norfolk_hampton.net.xml
#              (read-only sibling repo input -- never edited in place)
# Output net:  data\net\district.net.xml  (this repo)
#
# Crop box (lon-min,lat-min,lon-max,lat-max = WEST,SOUTH,EAST,NORTH), chosen so that:
#   1. The flood model's georeferenced grid (IMPLEMENTATION_CONTEXT.md #2) is fully
#      inside the crop:
#         flood grid: N=36.898650 S=36.895770 W=-76.304447 E=-76.300846
#   2. Two parallel N-S corridors survive: Hampton Blvd (spans lat 36.8888-36.9168,
#      lon -76.3091..-76.3028 per sumo_norfolk/road_segments.json) and Colley Ave
#      (spans lat 36.8872-36.8919, lon -76.2955..-76.2951).
#   3. At least one E-W connector ties the two corridors together: Jamestown
#      Crescent (lat 36.8919-36.8982, lon -76.3029..-76.2955) runs almost exactly
#      between them; Magnolia Ave (lat ~36.894-36.895) crosses both longitudes too.
#
# Box used (with buffer beyond each road's extent so we don't clip right at an
# endpoint node):
#   WEST  = -76.3125   (west of Hampton Blvd's westernmost point, -76.3091)
#   SOUTH =  36.8840    (south of Colley Ave's southernmost point, 36.8872)
#   EAST  = -76.2925   (east of Colley Ave's easternmost point, -76.2951)
#   NORTH =  36.9060    (north of the flood grid's north edge, 36.898650, and of
#                        most of the Hampton Blvd segment used)
# Size: ~1.78 km (E-W) x ~2.44 km (N-S) -- within the "~2-3 km district" target
# from PROJECT_PLAN.md D1.

$ErrorActionPreference = "Stop"

$SumoHome   = "C:\Program Files (x86)\Eclipse\Sumo"
$SourceNet  = "C:\Users\dcost\ChandraMentorship\sumo_norfolk\norfolk_hampton.net.xml"
$OutDir     = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputNet  = Join-Path $OutDir "district.net.xml"

$West  = -76.3125
$South =  36.8840
$East  = -76.2925
$North =  36.9060

$boundary = "$West,$South,$East,$North"

& "$SumoHome\bin\netconvert.exe" `
    --sumo-net-file "$SourceNet" `
    --keep-edges.in-geo-boundary "$boundary" `
    --keep-edges.components 1 `
    --remove-edges.isolated `
    --output-file "$OutputNet"

if ($LASTEXITCODE -ne 0) {
    throw "netconvert failed with exit code $LASTEXITCODE"
}

Write-Host "Cropped net written to $OutputNet"
