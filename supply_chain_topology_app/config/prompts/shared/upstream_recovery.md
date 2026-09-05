Working order out in advance is not guaranteed to be right. If acting on a capability
returns `{"Error": "upstream_missing", "message": "..."}`, the work it depends on has
not happened yet, and the message names what is missing. Obtain that first through
whatever means your interface provides — a named tool call, a dispatch description, a
handoff, a ledger entry — then retry the one that failed. Treat this as a correction
rather than a failure: it is the system telling you the order you chose does not hold.
