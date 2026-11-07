"""CIT scoring (numpy only, no model training).

Response for one item = peak GSR in its ~10s window minus mean GSR in the
2s before the item appeared. Z-score responses across the items in each round,
average across rounds, highest score = pick. The filler item is never scored.
"""
