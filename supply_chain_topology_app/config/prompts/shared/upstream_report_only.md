If acting on a capability returns `{"Error": "upstream_missing", "message": "..."}`,
the work it depends on has not happened yet, and the message names what is missing.

You cannot correct it. You cannot obtain the missing prerequisite, because only the
capability selected for this step is available to you, and you cannot repeat a step.
Do not retry, and do not substitute a different capability.

Report it instead: name the capability that could not run and what it said was missing,
in the appropriate field of your final response.

Where any other guidance says to resolve `upstream_missing` and carry on without
reporting it, this replaces that guidance: here the condition cannot be resolved, so it
must be reported.
