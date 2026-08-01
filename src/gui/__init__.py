import pyqtgraph as pg

# MockCamera/ThorlabsCamera frames are plain numpy arrays shaped
# (height, width) -- row-major / [y, x], like every other array in this
# codebase. Set this once, globally, before any ImageView/PlotWidget is
# constructed, or images render transposed.
pg.setConfigOptions(imageAxisOrder="row-major")
