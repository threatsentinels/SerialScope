"""Shared constants. Limits are deliberately explicit so losses are measurable."""
APP_NAME = "SerialScope"
MAX_CHANNELS = 16
LIVE_BUFFER_SAMPLES = 100_000   # live ring keeps at least this many recent samples (up to 2x)
CONSOLE_LOG_LINES = 20_000      # raw lines retained in memory for the console
MAX_LINE_BYTES = 4096           # longer lines are truncated and counted
WORKER_QUEUE_LIMIT = 500_000    # records waiting for the GUI; overflow is counted
MAX_PLOT_POINTS = 3000          # per channel per redraw (min/max decimation)
SESSION_FORMAT = "serialscope-session"
SESSION_VERSION = 2
CHANNEL_COLORS = ["#5fd068", "#e5a823", "#38bdd0", "#e2574c", "#a98bd8", "#d0d4da",
                  "#7aa7ff", "#e07b39", "#8fd14f", "#d96ba0", "#4fb89a", "#c9b458"]
